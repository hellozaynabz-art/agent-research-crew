import json
import threading
from types import SimpleNamespace as NS

from research_crew import crew
from research_crew.llm import Claude

USAGE = NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)


def _text(t, citations=None):
    return NS(type="text", text=t, citations=citations)


class Script:
    """Routes fake responses by system prompt, as the real crew calls roles concurrently."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.review_round = 0

    def create(self, **kw):
        system = kw["system"]
        with self.lock:
            self.calls.append(kw)
        if system == crew.PLANNER:
            plan = {"objective": "Size the market", "questions": ["Q1?", "Q2?"]}
            return NS(stop_reason="end_turn", content=[_text(json.dumps(plan))], usage=USAGE)
        if system == crew.RESEARCHER:
            q = kw["messages"][0]["content"].split("Your question: ")[1]
            cites = [
                NS(url="https://a.example", title="Source A"),
                NS(url=f"https://{q[:2].lower()}.example", title=f"Source {q[:2]}"),
            ]
            return NS(stop_reason="end_turn", content=[_text(f"Notes for {q}", cites)], usage=USAGE)
        if system == crew.REVIEWER:
            self.review_round += 1
            verdict = "1. Claim X lacks a citation." if self.review_round == 1 else "APPROVED"
            return NS(stop_reason="end_turn", content=[_text(verdict)], usage=USAGE)
        return NS(stop_reason="end_turn", content=[_text(f"output of {system[:20]}")], usage=USAGE)


def _claude(script):
    return Claude(client=NS(beta=NS(messages=script)))


def test_full_run_dedupes_sources_and_revises_after_review():
    script = Script()
    brief = crew.run("AI coding assistants", _claude(script), log=lambda m: None)
    assert brief.plan.questions == ["Q1?", "Q2?"]
    # Source A is cited by both researchers but listed once.
    assert brief.markdown.count("https://a.example") == 1
    assert "## Sources" in brief.markdown
    writer_calls = [c for c in script.calls if c["system"] == crew.WRITER]
    assert len(writer_calls) == 2  # draft + revision after the reviewer's feedback
    assert "Claim X lacks a citation" in writer_calls[1]["messages"][0]["content"]


def test_researchers_get_web_tools():
    script = Script()
    crew.run("topic", _claude(script), log=lambda m: None)
    research_calls = [c for c in script.calls if c["system"] == crew.RESEARCHER]
    assert len(research_calls) == 2
    assert {t["name"] for t in research_calls[0]["tools"]} == {"web_search", "web_fetch"}


def test_pause_turn_is_resumed():
    responses = [
        NS(stop_reason="pause_turn", content=[_text("partial ")], usage=USAGE),
        NS(
            stop_reason="end_turn",
            content=[_text("done", [NS(url="https://x", title="X")])],
            usage=USAGE,
        ),
    ]
    client = NS(beta=NS(messages=NS(create=lambda **kw: responses.pop(0))))
    finding = crew.research("Q", "obj", Claude(client=client))
    assert finding.notes == "partial done"
    assert [s.url for s in finding.sources] == ["https://x"]


def test_number_sources_renders_global_numbers():
    f1 = crew.Finding("A?", "n1", [crew.Source("u1", "T1"), crew.Source("u2", "T2")])
    f2 = crew.Finding("B?", "n2", [crew.Source("u2", "T2"), crew.Source("u3", "T3")])
    sources, notes = crew.number_sources([f1, f2])
    assert [s.url for s in sources] == ["u1", "u2", "u3"]
    assert "[2] T2; [3] T3" in notes
