"""A research team of specialised Claude agents.

planner    → splits the topic into focused research questions (structured output)
researchers→ one per question, in parallel, each with web search + fetch; keeps citations
analyst    → reconciles findings, flags conflicts and weak evidence
writer     → drafts the brief with numbered sources
reviewer   → checks every claim against the notes; the writer revises once if needed
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pydantic import BaseModel

from research_crew.llm import Claude, text_of

WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3},
]
MAX_CONTINUATIONS = 4


class Plan(BaseModel):
    objective: str
    questions: list[str]


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["objective", "questions"],
    "additionalProperties": False,
}


@dataclass
class Source:
    url: str
    title: str


@dataclass
class Finding:
    question: str
    notes: str
    sources: list[Source] = field(default_factory=list)


PLANNER = """You plan research for a market/competitive brief. Turn the user's topic into a clear
objective and 3 to 5 non-overlapping research questions that together answer it. Each question
must be answerable with public web sources (market size, players, pricing, trends, risks…)."""

RESEARCHER = """You are a meticulous research analyst. Answer one question using web search.
Prefer primary and recent sources (company sites, filings, official statistics, reputable press);
note the date of every figure. Write concise notes: key facts and numbers, who says so, and
anything uncertain or contested. Do not speculate beyond the sources."""

ANALYST = """You are a senior analyst. From the research notes, produce: the 5-8 most important
insights (each tied to source numbers like [3]), where sources disagree, which claims rest on weak
or old evidence, and what remains unknown. Be specific and quantitative."""

WRITER = """You write crisp executive briefs in Markdown. Structure: title, a 3-sentence bottom line,
then sections chosen to fit the topic, then 'Risks & open questions'. Cite sources inline as [n]
using only the numbered sources provided; every figure needs a citation. Max 700 words.
Do not add a sources list; it is appended automatically."""

REVIEWER = """You fact-check a brief against the research notes and source list. Return either
exactly APPROVED, or a numbered list of specific problems: claims not supported by the notes, wrong
or missing citations, numbers that differ from the notes, or misleading framing."""


def plan(topic: str, claude: Claude) -> Plan:
    response = claude.create(
        system=PLANNER,
        messages=[{"role": "user", "content": topic}],
        max_tokens=4_000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
    )
    return Plan.model_validate_json(text_of(response))


def research(question: str, objective: str, claude: Claude) -> Finding:
    messages = [
        {"role": "user", "content": f"Overall objective: {objective}\n\nYour question: {question}"}
    ]
    for _ in range(MAX_CONTINUATIONS + 1):
        response = claude.create(
            system=RESEARCHER, tools=WEB_TOOLS, messages=messages, output_config={"effort": "low"}
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "pause_turn":  # server tools hit their per-turn limit; resume
            break
    notes, sources = [], {}
    for msg in messages:
        if msg["role"] != "assistant":
            continue
        for block in msg["content"]:
            if block.type != "text":
                continue
            notes.append(block.text)
            for c in getattr(block, "citations", None) or []:
                url = getattr(c, "url", None)
                if url:
                    sources.setdefault(url, Source(url=url, title=getattr(c, "title", "") or url))
    return Finding(question=question, notes="".join(notes).strip(), sources=list(sources.values()))


def number_sources(findings: list[Finding]) -> tuple[list[Source], str]:
    """Give every unique source a global number and render the notes with those numbers."""
    index: dict[str, int] = {}
    ordered: list[Source] = []
    blocks = []
    for f in findings:
        refs = []
        for s in f.sources:
            if s.url not in index:
                ordered.append(s)
                index[s.url] = len(ordered)
            refs.append(f"[{index[s.url]}] {s.title}")
        blocks.append(f"### {f.question}\n{f.notes}\n\nSources: {'; '.join(refs) or 'none'}")
    return ordered, "\n\n".join(blocks)


def _ask(claude: Claude, system: str, content: str, effort: str = "medium") -> str:
    response = claude.create(
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort},
    )
    return text_of(response)


@dataclass
class Brief:
    topic: str
    plan: Plan
    findings: list[Finding]
    markdown: str
    review: str


def run(topic: str, claude: Claude | None = None, log=print, parallel: int = 4) -> Brief:
    claude = claude or Claude()
    p = plan(topic, claude)
    log(f"Plan: {p.objective}")
    for q in p.questions:
        log(f"  • {q}")

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        findings = list(pool.map(lambda q: research(q, p.objective, claude), p.questions))
    sources, notes = number_sources(findings)
    log(f"Research done: {len(sources)} unique sources")
    source_list = "\n".join(f"[{i}] {s.title} — {s.url}" for i, s in enumerate(sources, 1))

    analysis = _ask(claude, ANALYST, f"Objective: {p.objective}\n\n{notes}", effort="high")
    log("Analysis done")
    context = f"Topic: {topic}\n\n<analysis>\n{analysis}\n</analysis>\n\n<sources>\n{source_list}\n</sources>"
    draft = _ask(claude, WRITER, context, effort="high")

    review = _ask(
        claude,
        REVIEWER,
        f"<notes>\n{notes}\n</notes>\n\n<sources>\n{source_list}\n</sources>\n\n<brief>\n{draft}\n</brief>",
    )
    if review.strip() != "APPROVED":
        log("Reviewer requested changes; revising")
        draft = _ask(
            claude,
            WRITER,
            f"{context}\n\n<previous_draft>\n{draft}\n</previous_draft>\n\n"
            f"<review>\n{review}\n</review>\n\nRevise the draft to fix every issue.",
            effort="high",
        )
    markdown = f"{draft}\n\n## Sources\n" + "\n".join(
        f"{i}. [{s.title}]({s.url})" for i, s in enumerate(sources, 1)
    )
    return Brief(topic=topic, plan=p, findings=findings, markdown=markdown, review=review)


def to_json(brief: Brief) -> str:
    return json.dumps(
        {
            "topic": brief.topic,
            "plan": brief.plan.model_dump(),
            "findings": [
                {
                    "question": f.question,
                    "notes": f.notes,
                    "sources": [s.__dict__ for s in f.sources],
                }
                for f in brief.findings
            ],
            "review": brief.review,
        },
        indent=2,
    )
