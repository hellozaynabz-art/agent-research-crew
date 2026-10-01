"""`uv run research-crew "The market for AI coding assistants in 2026"`"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from research_crew import crew
from research_crew.llm import Claude

console = Console()


def main() -> None:
    topic = " ".join(sys.argv[1:]) or console.input("[bold]Research topic:[/] ")
    claude = Claude()
    brief = crew.run(topic, claude, log=lambda m: console.print(f"[dim]{m}[/]"))

    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:50]
    out = Path("reports") / f"brief_{date.today():%Y-%m-%d}_{slug}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(brief.markdown)
    out.with_suffix(".json").write_text(crew.to_json(brief))
    console.print(Markdown(brief.markdown))
    u = claude.usage
    console.print(f"[green]saved {out}[/] [dim]· {u.calls} model calls · ${u.cost_usd:.2f}[/]")


if __name__ == "__main__":
    main()
