"""Rich CLI.  [STUB -- commands not implemented]

Exists alongside the web UI on purpose: a terminal demo has no browser, no
port, and no rendering surprises. If the projector fights back, this is the
fallback.

Run:  python -m universal_events.cli --help
"""

from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, help="Map any business's events into Klaviyo.")


@app.command()
def doctor() -> None:
    """Check keys and connectivity. Run before every demo. TODO."""
    raise NotImplementedError


@app.command("sources")
def list_sources_cmd() -> None:
    """List the mock sources and the unknown-vertical payloads. TODO."""
    raise NotImplementedError


@app.command()
def send(
    source: str = typer.Argument(..., help="Source key, e.g. fitness"),
    sample: str = typer.Argument(..., help="Sample key, e.g. class_booked"),
    mode: str = typer.Option("auto", help="auto | config | llm | heuristic"),
) -> None:
    """Fire one mock event and show payload -> mapping -> Klaviyo. TODO."""
    raise NotImplementedError


@app.command()
def unknown(
    key: str = typer.Argument(..., help="veterinary | tutoring | physical_therapy"),
) -> None:
    """Map an unfamiliar vertical with no config. The generalization demo. TODO."""
    raise NotImplementedError


@app.command()
def notifications() -> None:
    """Part 2: run the proactive agent sweep. TODO."""
    raise NotImplementedError


if __name__ == "__main__":
    app()
