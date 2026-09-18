"""Demo CLI.

Every command is meant to be run in front of someone, so output is built to
be read aloud rather than parsed.

    python -m universal_events.cli --help
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.markup import escape
from rich.text import Text

from . import seed as seeder
from . import sources, store
from .config import settings
from .klaviyo import KlaviyoClient
from .mapping import pipeline

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Klaviyo proactive-agent demo.")
console = Console(highlight=False)

SEV = {"high": "red", "medium": "yellow", "low": "dim"}
KIND = {"patch": "cyan", "create": "magenta", "campaign": "green"}


def _c():
    """Import composer lazily so `doctor` works even if something is broken."""
    from composer import (audience, feedback, flow_store, patch, proposals,
                          runners, signals)
    return audience, feedback, flow_store, patch, proposals, runners, signals


# --------------------------------------------------------------------- setup


@app.command()
def doctor() -> None:
    """Preflight: keys, connectivity, data. Run this before recording."""
    console.print(Rule("preflight"))
    ok, msg = KlaviyoClient().verify_credentials()
    console.print(f"  Klaviyo    {'[green]OK[/]' if ok else '[red]FAIL[/]'}  {msg}")
    console.print(f"  revision   {settings.klaviyo_api_revision}")
    provider = settings.active_provider
    colour = "green" if provider == "openai" else "yellow"
    console.print(f"  LLM        [{colour}]{provider}[/]  model={settings.openai_audit_model}")
    if provider != "openai":
        console.print("             [yellow]running deterministic fallback — copy will be "
                      "marked [NEEDS COPY][/]")
    console.print(f"  events     {store.count()} in local store")
    _, _, flow_store, _, proposals, _, _ = _c()
    console.print(f"  flows      {len(flow_store.all_flows())}")
    console.print(f"  proposals  {proposals.summary()}")
    if settings.auto_sweep:
        console.print(f"  sweep      [yellow]background job ON[/] — every "
                      f"{settings.sweep_interval_seconds}s "
                      f"(production would be 86400)")
        console.print("             [dim]it will create proposals on its own; set "
                      "AUTO_SWEEP=false for a controlled run[/]")
    else:
        console.print(f"  sweep      background job [dim]off[/] — "
                      f"`sweep` / Run sweep now only")


@app.command()
def reset() -> None:
    """Wipe everything and reseed. Guarantees an identical starting state."""
    _, feedback, flow_store, _, proposals, runners, _ = _c()
    flow_store.reset(); proposals.reset(); feedback.reset(); runners.reset_log()
    counts = seeder.seed_demo_history()
    console.print(f"[green]reset[/] — {counts['total']} events seeded, "
                  "flows back to v1, no proposals, no corrections")
    for key, n in counts.items():
        if key != "total":
            console.print(f"    {key:11} {n}")


# ---------------------------------------------------------------- part one


@app.command("sync-profiles")
def sync_profiles() -> None:
    """Create the seeded members as Klaviyo profiles.

    Run once after `reset` if you want profiles to exist before any campaign,
    which is how it would work in production.
    """
    _, _, _, _, _, runners, _ = _c()
    console.print("creating seeded members as Klaviyo profiles "
                  "[dim](identities only, no events)[/]")
    counts = seeder.sync_profiles_to_klaviyo()
    console.print(f"  upserted {counts['created']}")
    if counts["failed"]:
        console.print(f"  [yellow]failed {counts['failed']}[/]")
    if counts["skipped"]:
        console.print(f"  [dim]skipped {counts['skipped']} over the cap[/]")
    console.print("[dim]campaigns will now find these profiles rather than create them[/]")


@app.command("sources")
def list_sources() -> None:
    """Show the mock event sources and which ones Klaviyo already integrates."""
    table = Table(box=None, pad_edge=False)
    for col in ("source", "tool", "Klaviyo connector", "events"):
        table.add_column(col)
    for src in sources.list_sources():
        note = {"native": "[yellow]native — parity, not novelty[/]",
                "none": "[green]none — new capability[/]",
                "unverified": "[dim]unverified[/]"}[src.klaviyo_connector]
        table.add_row(src.key, src.tool, note,
                      ", ".join(s.key for s in src.samples))
    console.print(table)
    console.print("\n[bold]unknown payloads[/] (no connector exists, none coming):")
    for key, spec in sources.UNKNOWN_PAYLOADS.items():
        console.print(f"  [green]{key}[/] — {spec['_description'].splitlines()[0]}")


@app.command("map")
def map_unknown(
    key: str = typer.Argument(..., help="veterinary | tutoring | physical_therapy"),
    send: bool = typer.Option(True, help="actually POST to Klaviyo"),
) -> None:
    """Map a payload from a tool nobody has integrated. The Part 1 proof."""
    spec = sources.UNKNOWN_PAYLOADS.get(key)
    if not spec:
        console.print(f"[red]unknown[/] — try: {', '.join(sources.UNKNOWN_PAYLOADS)}")
        raise typer.Exit(1)

    console.print(Rule(f"raw payload — {key}"))
    console.print(Syntax(json.dumps(spec["payload"], indent=2)[:900],
                         "json", theme="ansi_dark", word_wrap=True))

    res = pipeline.ingest(spec["payload"], send=send, persist=False)
    m = res.mapping
    console.print(Rule("inferred mapping (no config existed)"))
    console.print(f"  strategy    {m.strategy}   confidence {m.confidence:.0%}")
    console.print(f"  chain       {' -> '.join(res.fallback_chain)}")
    console.print(f"  metric      [bold]{m.metric_name}[/]")
    console.print(f"  profile     {m.identity.primary}  "
                  f"({m.identity.first_name} {m.identity.last_name})")
    if m.value:
        console.print(f"  value       {m.value} {m.value_currency}")
    console.print(f"  properties  {len(m.properties)} kept")
    console.print("\n  [dim]field by field:[/]")
    for t in m.field_traces[:8]:
        console.print(f"    {t.source_path:42} -> {t.destination}")
    if m.warnings:
        for w in m.warnings:
            console.print(f"  [yellow]! {w}[/]")

    if res.delivery:
        d = res.delivery
        console.print(Rule("Klaviyo"))
        console.print(f"  {'[green]' if d.ok else '[red]'}{d.summary}[/]   {d.latency_ms}ms")
        if d.profile_url:
            console.print(f"  {d.profile_url}")


@app.command()
def send(
    source: str = typer.Argument(..., help="dental | fitness | restaurant | nonprofit"),
    sample: str = typer.Argument(..., help="e.g. class_no_show"),
    trigger: bool = typer.Option(True, help="also run the trigger-pattern agent"),
) -> None:
    """Fire one mock event: map it, send it, and let the trigger agent react."""
    _, _, _, _, _, runners, _ = _c()
    try:
        src = sources.get_source(source)
    except KeyError as exc:
        console.print(f"[red]{exc}[/]"); raise typer.Exit(1)

    payload = src.build(sample)
    res = pipeline.ingest(payload, source_key=source, send=True)
    m = res.mapping
    console.print(Rule(f"{src.tool} -> Klaviyo"))
    console.print(f"  metric   [bold]{m.metric_name}[/]   via {m.strategy}"
                  f" ({res.detected_config or 'inferred'})")
    console.print(f"  profile  {m.identity.primary}")
    if res.delivery:
        d = res.delivery
        console.print(f"  klaviyo  {'[green]' if d.ok else '[red]'}{d.summary}[/]")

    if not trigger:
        return
    console.print(Rule("trigger pattern"))
    made = runners.on_event(m.identity.primary, m.metric_name, source_key=source)
    if not made:
        console.print("  [dim]nothing worth flagging for this profile yet[/]")
        return
    for p in made:
        console.print(f"  [{KIND.get(p.kind,'white')}]proposal {p.id}[/] — "
                      f"{escape(p.current.headline)}")
        console.print(f"  [dim]see it: ./demo show {p.id}[/]")


# ---------------------------------------------------------------- the agent


@app.command("signals")
def show_signals() -> None:
    """What the agent notices in the data. Deterministic — no model involved."""
    *_, signals = _c()
    for s in signals.detect_aggregate():
        console.print(f"\n[{SEV.get(s.severity,'white')}]● {s.severity.upper()}[/] "
                      f"[bold]{s.title}[/]")
        console.print(f"  {s.detail}")
        console.print(f"  [dim]kind={s.kind} scope={s.scope} origin={s.origin}[/]")


@app.command()
def sweep(campaigns: bool = typer.Option(True, help="also draft backlog campaigns")) -> None:
    """Run the recurring job once. This is the scheduled pattern."""
    audience, _, _, _, proposals, runners, signals = _c()
    console.print(Rule("scheduled sweep"))
    made = runners.sweep_once()
    if campaigns:
        # A new flow only catches future cases, so also clear the backlog.
        for s in signals.detect_aggregate():
            if s.kind in ("lapsed_member", "lapsed_donor"):
                p = proposals.create_from_signal(s, want_campaign=True)
                if p:
                    made.append(p)
    if not made:
        console.print("  [dim]no new proposals (existing ones still open)[/]")
    for p in made:
        console.print(f"  [{KIND.get(p.kind,'white')}]{p.kind:8}[/] {p.id}  "
                      f"{p.current.headline}")
    console.print(f"\n  [dim]{len(made)} proposal(s) — `inbox` to list[/]")


@app.command()
def inbox(status: str = typer.Option(None, help="pending | approved | revised")) -> None:
    """The proposal inbox."""
    *_, proposals, _, _ = _c()
    items = proposals.all_proposals(status)
    if not items:
        console.print("[dim]empty — run `sweep` or `send fitness class_no_show`[/]")
        return
    table = Table(box=None, pad_edge=False)
    for col in ("id", "kind", "status", "rev", "flow", "headline"):
        table.add_column(col)
    for p in items:
        table.add_row(p.id, f"[{KIND.get(p.kind,'white')}]{p.kind}[/]", p.status,
                      str(p.revision_count), p.flow_name[:22],
                      p.current.headline[:46])
    console.print(table)


@app.command()
def show(proposal_id: str = typer.Argument(...)) -> None:
    """One proposal in full: noticed, context, audit, and the change."""
    *_, proposals, _, _ = _c()
    p = proposals.get(proposal_id)
    if not p:
        console.print(f"[red]no proposal {proposal_id}[/]"); raise typer.Exit(1)
    r = p.current

    console.print(Panel(f"[bold]{r.headline}[/]",
                        subtitle=f"{p.kind} · {p.status} · revision {r.n} · "
                                 f"origin {p.origin}", expand=False))
    console.print("\n[bold]what was noticed[/]")
    console.print(f"  {p.signal_title}")
    console.print(f"  [dim]{escape(r.observation)}[/]")

    console.print("\n[bold]context the agent pulled[/]")
    for line in p.context_lines:
        console.print(f"  · {line}")

    console.print("\n[bold]the audit[/]")
    console.print(f"  {escape(r.audit_summary)}")
    if r.findings:
        console.print("\n[bold]findings[/]")
        for f in r.findings:
            console.print(f"  · {escape(f)}")

    if r.drafted_campaign:
        c = r.drafted_campaign
        console.print("\n[bold]the campaign it drafted[/]")
        console.print(f"  audience  [bold]{c['audience_size']}[/] people — "
                      f"{c['audience_description']}")
        console.print(f"  rule      [dim]{c['audience_basis']}[/]")
        console.print(f"  e.g.      {', '.join(c.get('audience_sample') or [])}")
        console.print(f"  channel   {c['channel']}   timing {c['send_timing']}")
        body = (
            f"[bold]{escape(c['subject'])}[/]\n\n" if c.get("subject") else ""
        ) + escape(c["body"])
        console.print(Panel(body, title="message", expand=False))
    elif r.diff_text:
        console.print("\n[bold]the change it made[/] "
                      "[dim](diff computed by applying the patch)[/]")
        for line in r.diff_text.splitlines():
            safe = escape(line)
            if line.startswith(("+++", "---")):
                console.print(f"  [dim]{safe}[/]")
            elif line.startswith("+"):
                console.print(f"  [green]{safe}[/]")
            elif line.startswith("-"):
                console.print(f"  [red]{safe}[/]")
            elif line.startswith("@@"):
                console.print(f"  [cyan]{safe}[/]")
            else:
                console.print(f"  {safe}")

    if r.predicted_impact:
        console.print(f"\n[bold]predicted impact[/]\n  {escape(r.predicted_impact)}")

    if r.prompted_by_feedback:
        console.print(f"\n[bold]this revision came from your feedback[/]")
        console.print(f"  [italic]\"{r.prompted_by_feedback}\"[/]")

    console.print(f"\n[dim]{r.source} · {r.model_used or 'deterministic'} · "
                  f"{r.cost_label} · {r.latency_ms or 0}ms · "
                  f"confidence {r.confidence:.0%}[/]")
    if r.fallback_reason:
        console.print(f"[yellow]fell back: {r.fallback_reason}[/]")
    if p.status in ("pending", "revised"):
        console.print(f"\n  approve:  [bold]./demo approve {p.id}[/]")
        console.print(f"  reject :  [bold]./demo reject {p.id} \"your feedback\"[/]")


@app.command()
def approve(proposal_id: str = typer.Argument(...)) -> None:
    """Approve it. Patches go live, drafted flows are created, campaigns send."""
    *_, proposals, _, _ = _c()
    p, result = proposals.approve(proposal_id)
    if not p:
        console.print(f"[red]no proposal {proposal_id}[/]"); raise typer.Exit(1)
    if not result:
        console.print(f"[yellow]nothing applied (status {p.status})[/]"); raise typer.Exit(1)
    if p.kind == "campaign":
        console.print(f"[green]sent[/] — {p.sent_to} recipients"
                      f"  [dim](recorded locally; nothing actually transmitted)[/]")
    else:
        console.print(f"[green]applied[/] — {result['name']} is now "
                      f"v{result['version']} ({result['status']})")


@app.command()
def reject(
    proposal_id: str = typer.Argument(...),
    feedback_text: str = typer.Argument(..., help="plain language, e.g. 'no SMS for donors'"),
) -> None:
    """Reject with feedback. The agent revises and remembers."""
    _, feedback, _, _, proposals, _, _ = _c()
    before = {c.id for c in feedback.all_corrections()}
    p = proposals.reject(proposal_id, feedback_text)
    if not p:
        console.print(f"[red]no proposal {proposal_id}[/]"); raise typer.Exit(1)
    r = p.current
    console.print(f"[yellow]revised[/] — now revision {r.n}")
    console.print(f"  {r.headline}")
    new = [c for c in feedback.all_corrections() if c.id not in before]
    if new:
        console.print("\n[bold]learned from that:[/]")
        for c in new:
            console.print(f"  · {escape(c.rule)}  [dim]({c.scope_label})[/]")
    console.print(f"\n[dim]see it: ./demo show {p.id}[/]")


@app.command()
def corrections() -> None:
    """Everything the agent has been taught, and how often it's used it."""
    _, feedback, *_ = _c()
    items = feedback.all_corrections()
    if not items:
        console.print("[dim]nothing learned yet — reject a proposal with feedback[/]")
        return
    for c in items:
        console.print(f"· [bold]{escape(c.rule)}[/]")
        console.print(f"    scope {c.scope_label} · applied {c.times_applied}x")
        if c.raw_feedback:
            console.print(f"    [dim]from: \"{escape(c.raw_feedback)}\"[/]")


@app.command()
def flows() -> None:
    """Current automations and their versions."""
    _, _, flow_store, patch, *_ = _c()
    for f in flow_store.all_flows():
        console.print(f"\n[bold]{f['name']}[/] "
                      f"[dim]v{f['version']} {f['status']} · {f.get('vertical')}[/]")
        for line in patch.render_outline(f)[1:]:
            console.print(f"  {escape(line)}")
    hist = flow_store.history()
    if hist:
        console.print("\n[bold]version history[/]")
        for h in hist:
            console.print(f"  {h['at'][:19]}  {h['flow_id']} v{h['version']}  {h['note'][:44]}")


@app.command("runs")
def run_log() -> None:
    """The two patterns, side by side. Trigger vs recurring."""
    _, _, _, _, _, runners, _ = _c()
    entries = runners.run_log()
    if not entries:
        console.print("[dim]no runs yet — try `sweep` and `send fitness class_no_show`[/]")
        return
    table = Table(box=None, pad_edge=False)
    for col in ("time", "pattern", "scope seen", "signals", "proposals", "ms", "detail"):
        table.add_column(col)
    for r in entries:
        scope = "one profile" if r.origin == "trigger" else "all profiles + windows"
        table.add_row(r.clock, f"[bold]{r.origin}[/]", scope, str(r.signals_found),
                      str(r.proposals_created), str(r.duration_ms), r.detail[:34])
    console.print(table)


if __name__ == "__main__":
    app()
