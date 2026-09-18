"""The agent: audit a flow against a signal and propose a concrete patch.

Two backends behind one interface:

  llm    -- gpt-5-nano via the Responses API with a constrained output schema.
            Reasons about WHY the flow mishandles the signal and can revise
            from plain-language feedback.
  rules  -- composer.rules. Deterministic, offline, always available.

`propose()` and `revise()` never raise. If the model is unreachable, rate
limited, or returns something unappliable, we fall back to rules and say so in
`source`. A live demo must always produce a proposal.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from universal_events.config import settings

from . import rules
from .context import ContextBundle
from .patch import EditOp, apply_patch, validate_flow

# gpt-5-nano, USD per 1M tokens.
# USD per 1M tokens, per model. A table rather than two constants because
# hardcoding one model's prices meant every displayed cost was understated
# by ~2.5x the moment the default model changed.
#
# Model choice was made on measurement, not price: gpt-5-nano burned ~11k
# reasoning tokens per audit, took 77s, and still missed the obvious finding.
# gpt-5.4-mini answers in ~5s and gets it right.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}
DEFAULT_PRICE = (0.75, 4.50)


def price_for(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens. Unknown models fall back safely high."""
    return PRICING.get(model, DEFAULT_PRICE)

# Fields that must end up numeric when the patch is applied.
NUMERIC_FIELDS = {"hours", "duration_hours", "days"}

Source = Literal["llm", "rules"]


class ProposedOp(BaseModel):
    """Mirror of EditOp, flattened for the model's output schema."""

    op: Literal["set_field", "insert_after", "fill_branch"]
    rationale: str = ""
    step_id: str | None = None
    field_name: str | None = None
    value_text: str | None = None
    value_number: float | None = None
    new_step_json: str | None = None
    branch: Literal["true", "false"] | None = None
    branch_steps_json: str | None = None

    def to_edit_op(self) -> EditOp:
        """Convert to an EditOp.

        The model emits nested step objects as JSON STRINGS rather than free
        objects. Structured-output schemas need concrete types, and a string
        we parse ourselves is safer than an open-ended dict the model can
        shape however it likes.
        """
        value: Any = None
        if self.value_number is not None:
            value = self.value_number
        elif self.value_text is not None:
            value = self.value_text

        # Models sometimes write prose into a numeric field ("12 hours rather
        # than 3 days"). Coerce rather than reject: a patch that fails to apply
        # over a formatting slip loses a correct finding.
        if self.field_name in NUMERIC_FIELDS and isinstance(value, str):
            found = re.search(r"-?\d+(?:\.\d+)?", value)
            value = float(found.group()) if found else None

        step = None
        if self.new_step_json:
            try:
                parsed = json.loads(self.new_step_json)
            except ValueError:
                parsed = None
            # May arrive as a single object or a list of steps.
            if isinstance(parsed, list):
                step = parsed[0] if parsed else None
            elif isinstance(parsed, dict):
                step = parsed

        if self.op == "fill_branch":
            raw = self.branch_steps_json or self.new_step_json
            if raw:
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = []
                value = parsed if isinstance(parsed, list) else [parsed]

        return EditOp(
            op=self.op,
            rationale=self.rationale,
            step_id=self.step_id,
            field=self.field_name,
            value=value,
            step=step,
            branch=self.branch,
        )


class LearnedRule(BaseModel):
    """One durable lesson, with its own scope.

    Scope is per-rule, not per-batch: a single rejection can contain both a
    business-specific rule ("our members hate texts") and a universal one
    ("nothing before 9am"), and one shared flag forces the wrong scope onto
    one of them.
    """

    rule: str = Field(
        description="One short imperative, e.g. 'Never propose SMS'. Return ONE "
        "rule per distinct instruction the user gave. Do not restate the same "
        "instruction twice in different words."
    )
    applies_everywhere: bool = Field(
        description="False if the rule depends on WHO this business's customers "
        "are, what it sells, or which channels they prefer -- those are specific "
        "to this account. True only for rules any business in any industry would "
        "want, such as timing or frequency conventions. When uncertain, choose "
        "False: over-applying one account's preference to others is the worse "
        "mistake."
    )


class LLMAudit(BaseModel):
    """Exactly what the model is allowed to return."""

    headline: str = Field(description="One line naming the problem, under 90 chars")
    observation: str = Field(description="What was noticed, citing the actual numbers")
    audit_summary: str = Field(description="Which steps were examined and what is wrong")
    findings: list[str] = Field(description="Specific problems, one per line")
    predicted_impact: str = Field(description="What changes if this is approved")
    ops: list[ProposedOp] = Field(description="The concrete edits to make")
    confidence: float = Field(description="0 to 1")
    learned: list[LearnedRule] = Field(
        default_factory=list,
        description="Only when revising: EVERY durable lesson in the user's "
        "feedback, each with its own scope. A single rejection often carries "
        "several -- return all of them, not just the first. Empty otherwise.",
    )


class DraftedFlow(BaseModel):
    """A whole new automation, for when nothing covers the signal.

    Copy must be templated. This flow will run for every member who matches
    the trigger, so a hardcoded name would greet all of them wrong.
    """

    name: str = Field(description="Short flow name a marketer would recognise")
    trigger_kind: Literal["metric", "segment"] = Field(
        description="'segment' when the trigger is a state like going quiet "
        "(no event fires for an absence); 'metric' when a real event starts it"
    )
    trigger_value: str = Field(
        description="Metric name, or the segment definition e.g. "
        "'Members with no booking in 21 days'"
    )
    steps_json: str = Field(
        description="JSON array of step objects. Types: delay, email, sms, split, "
        'update_profile. e.g. [{"type":"delay","hours":24},'
        '{"type":"email","subject":"...","body":"..."}]'
    )
    observation: str
    rationale: str = Field(description="Why this flow, and why it is needed at all")
    predicted_impact: str
    confidence: float
    learned: list[LearnedRule] = Field(default_factory=list)


class AuditOutput(BaseModel):
    headline: str
    observation: str
    audit_summary: str
    findings: list[str] = Field(default_factory=list)
    predicted_impact: str = ""
    ops: list[EditOp] = Field(default_factory=list)
    confidence: float = 0.0
    source: Source = "rules"
    model_used: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    # (rule, applies_everywhere) pairs. Scope is per rule.
    learned: list[tuple[str, bool]] = Field(default_factory=list)
    fallback_reason: str | None = None
    # Set instead of `ops` when the proposal is to CREATE an automation
    # rather than patch one. Composer's native unit is a generated campaign
    # or flow, so this is the more product-shaped proposal of the two.
    drafted_flow: dict[str, Any] | None = None
    drafted_campaign: dict[str, Any] | None = None

    @property
    def kind(self) -> str:
        if self.drafted_campaign:
            return "campaign"
        if self.drafted_flow:
            return "create"
        return "patch"

    @property
    def cost_label(self) -> str:
        if self.source == "rules":
            return "$0.00 (deterministic, no model call)"
        if self.cost_usd is None:
            return "unknown"
        return f"${self.cost_usd:.6f}"


SYSTEM_PROMPT = """\
You are the proactive agent inside Klaviyo Composer. You monitor a business's \
event stream, and when something looks wrong you audit the marketing automation \
flow that should be handling it, then propose a specific fix.

You are not writing a report. You are proposing an edit a human will approve or \
reject, so be concrete and specific.

Rules you must follow:
- Cite the actual numbers from the evidence. Never invent data.
- Only reference step ids that exist in the flow shown to you.
- Propose the smallest set of edits that genuinely fixes the problem. Two or \
three good edits beat eight speculative ones.
- EVERY finding you list must have an edit that fixes it. If you cannot fix \
something with the operations available, do not list it as a finding -- \
diagnosing a problem and then leaving it untouched is worse than staying \
quiet about it. You have insert_after and fill_branch, so "this needs a \
different treatment for repeat cases" IS fixable: add the steps.
- Do not over-correct. If the problem is that a flow waits too long, shorten \
the wait -- do not delete every delay so two emails land within hours of each \
other. Ask what a careful marketer would actually set.
- OBEY the operating constraints the user has already given you. They are not \
suggestions. If a constraint forbids something, do not propose it, and do not \
argue about it.
- Write copy in the voice of the actual business. A dental practice does not say \
"shop now"; a nonprofit does not say "your order".
- NEVER hardcode a person's name, class, or detail into copy. A flow runs for \
hundreds of people. Use {{ first_name }}, never "Hi Jordan". The profile you \
are shown is one EXAMPLE of who will receive this, not the only recipient.
- Any SMS step must include "send_if": "sms_consent == true".
- Explain WHY the current flow mishandles this specific signal, not generic \
best practice.

Edit operation reference:
- set_field: change one field on a step. Use step_id, field_name, and either \
value_text or value_number. For a delay, field_name is "hours" and you MUST \
use value_number with a bare number of hours (24, not "24 hours" and not \
"one day"). Never put prose in value_number fields.
- insert_after: add a step after step_id. Put the new step in new_step_json as \
a JSON object string, e.g. {"type":"sms","body":"...","send_if":"sms_consent == true"}
- fill_branch: populate an empty split branch. Use step_id, branch ("true" or \
"false"), and branch_steps_json as a JSON array string of step objects.

You cannot delete steps or change the trigger -- only adjust fields, insert
steps, and fill empty branches. Work within that.

Valid step types: delay, email, sms, split, update_profile.
Exact shapes:
  {"type":"delay","hours":24}
  {"type":"email","subject":"...","body":"..."}
  {"type":"sms","body":"...","send_if":"sms_consent == true"}
  {"type":"split","condition":{"field":"has_booked_since","op":"equals",
   "value":false},"true_branch":[...],"false_branch":[...]}
A split's condition MUST be an object with field, op and value -- never a
string like "has_booked == false" -- and at least one branch must have steps."""


def _ask(system: str, user: str, schema: type) -> tuple[Any, dict[str, Any]]:
    """One constrained model call. Every path through this module uses it.

    Returns the parsed object plus the cost/latency metadata the UI displays.
    Raises on failure -- callers decide which deterministic fallback to use.
    """
    import openai

    started = time.monotonic()
    response = openai.OpenAI(api_key=settings.openai_api_key).responses.parse(
        model=settings.openai_audit_model,
        input=[{"role": "system", "content": system},
               {"role": "user", "content": user}],
        text_format=schema,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("model returned no parsable output")

    usage = getattr(response, "usage", None)
    tin = getattr(usage, "input_tokens", None) if usage else None
    tout = getattr(usage, "output_tokens", None) if usage else None
    return parsed, {
        "model_used": settings.openai_audit_model,
        "input_tokens": tin,
        "output_tokens": tout,
        "cost_usd": _cost(settings.openai_audit_model, tin, tout),
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def _cost(model: str, tin: int | None, tout: int | None) -> float | None:
    if tin is None or tout is None:
        return None
    price_in, price_out = price_for(model)
    return tin / 1e6 * price_in + tout / 1e6 * price_out


def _usable() -> bool:
    """Whether the model path is available at all."""
    return settings.active_provider == "openai" and settings.openai_configured


def _from_rules(bundle: ContextBundle, reason: str | None = None) -> AuditOutput:
    """Deterministic proposal, honouring learned corrections."""
    findings = rules.audit_flow(bundle.flow, bundle.signal)
    blocked = {c.rule.lower() for c in bundle.corrections}

    ops: list[EditOp] = []
    kept: list[rules.Finding] = []
    for finding in findings:
        # Respect a learned "no SMS" style constraint even in rules mode.
        if any("sms" in rule for rule in blocked) and finding.code == "single_channel":
            continue
        kept.append(finding)
        ops.extend(finding.ops)

    if not kept:
        return AuditOutput(
            headline="No issues found in this flow",
            observation=bundle.signal.title,
            audit_summary="Audited every step; nothing actionable under current rules.",
            confidence=0.4,
            source="rules",
            fallback_reason=reason,
        )

    primary = kept[0]
    return AuditOutput(
        headline=primary.issue,
        observation=f"{bundle.signal.title}. {bundle.signal.detail}",
        audit_summary=primary.why,
        findings=[f"{f.issue} — {f.why}" for f in kept],
        predicted_impact=(
            "Contact arrives while the signal is still fresh, and no recipient "
            "exits the flow without a message."
        ),
        ops=ops,
        confidence=0.7,
        source="rules",
        fallback_reason=reason,
    )


def _finalise(
    audit: LLMAudit, bundle: ContextBundle, meta: dict[str, Any]
) -> AuditOutput:
    """Convert model output to EditOps and verify the patch actually applies."""
    ops = [op.to_edit_op() for op in audit.ops]
    result = apply_patch(bundle.flow, ops)

    if not result.applied:
        raise RuntimeError(
            "no ops applied: " + "; ".join(e.message for e in result.errors)
        )

    # Structural problems carry op_index -1, so they are not attributable to a
    # single op and cannot be dropped selectively. They mean the resulting flow
    # is broken, so the whole patch is refused rather than shown to the user.
    structural = [e.message for e in result.errors if e.op_index < 0]
    if structural:
        raise RuntimeError("patch produced an invalid flow: " + "; ".join(structural))

    # Drop individual ops that failed, keep the rest, and note it.
    if result.errors:
        bad = {e.op_index for e in result.errors if e.op_index >= 0}
        if bad:
            ops = [op for i, op in enumerate(ops) if i not in bad]

    return AuditOutput(
        headline=audit.headline,
        observation=audit.observation,
        audit_summary=audit.audit_summary,
        findings=audit.findings,
        predicted_impact=audit.predicted_impact,
        ops=ops,
        confidence=audit.confidence,
        source="llm",
        learned=[(x.rule, x.applies_everywhere) for x in audit.learned],
        **meta,
    )


def propose(bundle: ContextBundle) -> AuditOutput:
    """Audit the flow and propose a patch. Never raises."""
    if not _usable():
        return _from_rules(bundle, reason="no OpenAI key configured")

    prompt = (
        bundle.to_prompt()
        + "\n## Your task\n"
        "Audit the flow above against what was noticed. Identify why this flow "
        "mishandles this specific signal, then propose the concrete edits that "
        "fix it. Leave `learned` empty."
    )
    try:
        audit, meta = _ask(SYSTEM_PROMPT, prompt, LLMAudit)
        return _finalise(audit, bundle, meta)
    except Exception as exc:
        return _from_rules(bundle, reason=f"{type(exc).__name__}: {exc}"[:200])


def _no_model_revision(
    bundle: ContextBundle, previous_ops: list[EditOp], reason: str
) -> AuditOutput:
    """Revision is not possible without a model, so say that plainly.

    There was a keyword-matching version here that scanned feedback for words
    like "sms". It could not actually read what the user wrote, and pretending
    to understand feedback is worse than admitting the model is unreachable.
    """
    return AuditOutput(
        headline="Can't revise right now — the model is unreachable",
        observation=f"{bundle.signal.title}. {bundle.signal.detail}",
        audit_summary=(
            "Your feedback was recorded but cannot be acted on without a model. "
            "The original proposal is unchanged."
        ),
        ops=previous_ops,
        confidence=0.0,
        source="rules",
        fallback_reason=reason,
    )


def revise(
    bundle: ContextBundle, previous_ops: list[EditOp], user_feedback: str
) -> AuditOutput:
    """Revise a rejected proposal using the user's feedback."""
    if not _usable():
        return _no_model_revision(bundle, previous_ops, "no OpenAI key configured")

    prior = "\n".join(f"- {op.describe()} ({op.rationale})" for op in previous_ops)
    prompt = (
        bundle.to_prompt()
        + "\n## Your previous proposal, which the user REJECTED\n"
        + prior
        + "\n\n## The user's feedback, verbatim\n"
        + f'"{user_feedback}"\n\n'
        + "## Your task\n"
        "Produce a REVISED patch that addresses this feedback directly. Do not "
        "simply retry the same idea. If the feedback rules something out, remove "
        "it entirely rather than softening it. Keep the parts of your previous "
        "proposal the feedback did not object to.\n"
        "Also fill `learned` with EVERY durable lesson in this feedback -- one "
        "entry each, with its own applies_everywhere flag. If the user objected "
        "to two things, return two rules."
    )
    try:
        audit, meta = _ask(SYSTEM_PROMPT, prompt, LLMAudit)
        return _finalise(audit, bundle, meta)
    except Exception as exc:
        return _no_model_revision(
            bundle, previous_ops, f"{type(exc).__name__}: {exc}"[:200]
        )


# ---------------------------------------------------------------- create path

DRAFT_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "then propose a specific fix.",
    "and when NO automation covers it, you draft a new one.",
) + """

You are drafting a NEW automation because nothing in the account handles this
signal. Additional rules:
- Keep it short. Three to five steps. A marketer must be able to read it at a
  glance and take responsibility for it.
- Choose the trigger honestly. An absence ("stopped booking", "went quiet")
  fires no event, so its trigger_kind is "segment", not "metric". Only use
  "metric" when a real event starts the flow.
- Write the actual copy, in this business's voice. No placeholders.
- Do not duplicate what an existing flow already does."""


COPY_FIELDS = ("subject", "body", "cta", "preview")


def _detemplate(steps: list[dict], bundle: ContextBundle) -> list[str]:
    """Replace a leaked example name with a merge tag. Returns what it fixed.

    Defence in depth: the prompt forbids hardcoding names, but a flow that
    greets every member as "Jordan" is bad enough to be worth catching in code
    rather than trusting instructions.
    """
    fixed: list[str] = []
    names = {
        e.first_name for e in bundle.profile_events if getattr(e, "first_name", None)
    }
    if bundle.signal.profile_name:
        names.add(bundle.signal.profile_name.split()[0])
    names = {n for n in names if n and len(n) > 2}
    if not names:
        return fixed

    def all_steps(items: list[dict]):
        for step in items:
            yield step
            for branch in ("true_branch", "false_branch"):
                nested = step.get(branch)
                if isinstance(nested, list):
                    yield from all_steps(nested)

    for step in all_steps(steps):
        for field in COPY_FIELDS:
            text = step.get(field)
            if not isinstance(text, str):
                continue
            for name in names:
                if re.search(rf"\b{re.escape(name)}\b", text):
                    text = re.sub(rf"\b{re.escape(name)}\b", "{{ first_name }}", text)
                    fixed.append(f"{step.get('id', '?')}.{field}: {name!r} -> merge tag")
            step[field] = text
    return fixed


def _draft_to_flow(draft: DraftedFlow, bundle: ContextBundle) -> dict:
    """Turn the model's draft into a flow dict, assigning safe step ids."""
    try:
        steps = json.loads(draft.steps_json)
    except ValueError as exc:
        raise RuntimeError(f"drafted steps were not valid JSON: {exc}") from exc
    if not isinstance(steps, list) or not steps:
        raise RuntimeError("drafted flow had no steps")

    # Assign ids recursively: split branches contain steps too, and the
    # validator walks into them, so numbering only the top level leaves
    # nested steps without ids and fails the whole draft.
    counter = iter(range(1, 1000))

    def normalise(raw_steps: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in raw_steps:
            if not isinstance(raw, dict):
                continue
            step = dict(raw)
            step["id"] = f"s{next(counter)}"
            if step.get("type") == "sms":
                step.setdefault("send_if", "sms_consent == true")
            if step.get("type") == "split":
                for branch in ("true_branch", "false_branch"):
                    if isinstance(step.get(branch), list):
                        step[branch] = normalise(step[branch])
            out.append(step)
        return out

    clean = normalise(steps)
    detemplated = _detemplate(clean, bundle)

    return {
        "name": draft.name,
        "vertical": bundle.signal.vertical,
        "business": (bundle.sibling_flows[0].get("business") if bundle.sibling_flows else None),
        "status": "draft",
        "version": 1,
        "trigger": (
            {"type": "metric", "metric": draft.trigger_value}
            if draft.trigger_kind == "metric"
            else {"type": "segment", "segment": draft.trigger_value}
        ),
        "handles_signals": [bundle.signal.kind],
        "steps": clean,
        "_copy_fixes": detemplated,
    }


def propose_new_flow(bundle: ContextBundle) -> AuditOutput:
    """Draft a whole automation because nothing covers the signal."""
    if not _usable():
        return _draft_with_rules(bundle, reason="no OpenAI key configured")

    prompt = (
        bundle.to_prompt()
        + "\n## Your task\n"
        "Draft a new automation that handles the signal above. Explain why the "
        "business needs it, not just what it contains. Leave `learned` empty."
    )
    try:
        draft, meta = _ask(DRAFT_SYSTEM_PROMPT, prompt, DraftedFlow)
        flow = _draft_to_flow(draft, bundle)
        problems = validate_flow(flow)
        if problems:
            raise RuntimeError("drafted flow invalid: " + "; ".join(problems))

        return AuditOutput(
            headline=f"No automation handles this — drafted \"{draft.name}\"",
            observation=draft.observation,
            audit_summary=draft.rationale,
            findings=[
                f"No existing flow is responsible for '{bundle.signal.kind}'.",
                f"Trigger chosen: {draft.trigger_kind} — {draft.trigger_value}",
            ],
            predicted_impact=draft.predicted_impact,
            confidence=draft.confidence,
            source="llm",
            drafted_flow=flow,
            **meta,
        )
    except Exception as exc:
        return _draft_with_rules(bundle, reason=f"{type(exc).__name__}: {exc}"[:200])


def _draft_with_rules(bundle: ContextBundle, reason: str | None = None) -> AuditOutput:
    """Offline skeleton. Structure only -- copy is explicitly left unwritten.

    This deliberately does NOT fabricate marketing copy. Template text passed
    off as authored work is the thing that makes a rules fallback dishonest, so
    the placeholders here are visible and the proposal says so.
    """
    signal = bundle.signal
    days = (signal.evidence or {}).get("days_quiet") or 21
    blocked = {c.rule.lower() for c in bundle.corrections}
    steps: list[dict[str, Any]] = [
        {"id": "s1", "type": "delay", "hours": 24},
        {
            "id": "s2",
            "type": "email",
            "subject": "[NEEDS COPY] Re-engagement subject line",
            "body": "[NEEDS COPY] Written by the agent when a model is available.",
        },
    ]
    if not any("sms" in rule for rule in blocked):
        steps.append(
            {
                "id": "s3",
                "type": "sms",
                "body": "[NEEDS COPY] Short nudge.",
                "send_if": "sms_consent == true",
            }
        )

    flow = {
        "name": f"{(signal.vertical or 'Account').title()} Re-Engagement",
        "vertical": signal.vertical,
        "status": "draft",
        "version": 1,
        "trigger": {"type": "segment", "segment": f"No activity in {days} days"},
        "handles_signals": [signal.kind],
        "steps": steps,
    }
    return AuditOutput(
        headline=f"No automation handles this — drafted a skeleton ({len(steps)} steps)",
        observation=f"{signal.title}. {signal.detail}",
        audit_summary=(
            "Nothing in the account is responsible for this signal. This is a "
            "STRUCTURAL skeleton only: the copy is marked [NEEDS COPY] because "
            "it was generated without a model."
        ),
        findings=[f"No existing flow is responsible for '{signal.kind}'."],
        predicted_impact="Gives the business a starting point for an uncovered case.",
        confidence=0.4,
        source="rules",
        drafted_flow=flow,
        fallback_reason=reason,
    )


# -------------------------------------------------------------- campaign path

class DraftedCampaign(BaseModel):
    """A one-off send, drafted and awaiting permission.

    Distinct from a flow: a flow handles everyone who enters a condition from
    now on, while a campaign clears the people already in it. Creating a flow
    does nothing for the nine members who went quiet last month, which is why
    both proposals exist.
    """

    name: str = Field(description="Internal campaign name")
    channel: Literal["email", "sms"] = Field(description="email unless brevity matters")
    subject: str = Field(description="Subject line; empty string for SMS")
    body: str = Field(
        description="The full message. Use {{ first_name }}, never a real name."
    )
    send_timing: str = Field(description='e.g. "now" or "tomorrow 10am local time"')
    observation: str
    rationale: str = Field(description="Why send this, to these people, now")
    predicted_impact: str
    confidence: float
    learned: list[LearnedRule] = Field(default_factory=list)


CAMPAIGN_SYSTEM_PROMPT = SYSTEM_PROMPT.split("Edit operation reference:")[0] + """
You are drafting a ONE-OFF CAMPAIGN, not an automation.

The business has people who ALREADY match a condition. A new flow would only
catch future cases, so these people need a direct send now.

Rules:
- The audience size is given to you. It was counted by query. Use that number;
  never invent or round it.
- Write the complete message, ready to send. No placeholders, no "[insert X]".
- Use {{ first_name }} for personalisation. Never a real name.
- Respect the operating constraints absolutely, including channel choice. If a
  constraint forbids SMS, channel must be "email".
- Earn the send. These are real people who will receive this, so if the message
  would not be worth their attention, say so in rationale and set a low
  confidence."""


def propose_campaign(bundle: ContextBundle, aud: Any) -> AuditOutput:
    """Draft a one-off send to clear the existing backlog."""
    if not aud.size:
        return AuditOutput(
            headline="No one currently matches this condition",
            observation=bundle.signal.title,
            audit_summary="A campaign needs an existing audience; there is none.",
            confidence=0.0,
            source="rules",
        )

    if not _usable():
        return _campaign_with_rules(bundle, aud, reason="no OpenAI key configured")

    from .audience import render_for_prompt as render_audience

    prompt = (
        bundle.to_prompt()
        + "\n## The audience for this send\n"
        + render_audience(aud)
        + "\n\n## Your task\n"
        "Draft a one-off campaign to these people. A flow would only catch "
        "future cases; these are already in this state. Leave `learned` empty."
    )
    try:
        draft, meta = _ask(CAMPAIGN_SYSTEM_PROMPT, prompt, DraftedCampaign)

        # Enforce learned channel constraints in code, not just in the prompt.
        blocked = {c.rule.lower() for c in bundle.corrections}
        channel = draft.channel
        forced = None
        if channel == "sms" and any("sms" in rule for rule in blocked):
            channel = "email"
            forced = "channel forced to email by a learned correction"

        step = {"type": channel, "body": draft.body, "subject": draft.subject}
        fixes = _detemplate([dict(step, id="c1")], bundle)

        campaign = {
            "name": draft.name,
            "channel": channel,
            "subject": draft.subject if channel == "email" else "",
            "body": draft.body,
            "send_timing": draft.send_timing,
            "audience_description": aud.description,
            "audience_size": aud.size,          # from the query, not the model
            "audience_basis": aud.basis,
            "audience_sample": aud.sample_names,
            "_copy_fixes": fixes,
            "_forced": forced,
        }
        return AuditOutput(
            headline=f"Drafted {'an email' if channel == 'email' else 'an SMS'} to {aud.size} people — send it?",
            observation=draft.observation,
            audit_summary=draft.rationale,
            findings=[
                f"{aud.size} profiles already match: {aud.description}.",
                "A new flow would only catch future cases, not this backlog.",
            ]
            + ([forced] if forced else []),
            predicted_impact=draft.predicted_impact,
            confidence=draft.confidence,
            source="llm",
            drafted_campaign=campaign,
            **meta,
        )
    except Exception as exc:
        return _campaign_with_rules(
            bundle, aud, reason=f"{type(exc).__name__}: {exc}"[:200]
        )


def revise_campaign(
    bundle: ContextBundle, previous: dict[str, Any], user_feedback: str, aud: Any
) -> AuditOutput:
    """Revise a rejected CAMPAIGN from feedback.

    Campaign proposals carry no edit ops, so the flow-patch revision path had
    nothing to work with and silently produced no revision and no lessons --
    which broke the most important beat in the demo.
    """
    if not _usable():
        return _campaign_with_rules(bundle, aud, reason="no OpenAI key configured")

    from .audience import render_for_prompt as render_audience

    prompt = (
        bundle.to_prompt()
        + "\n## The audience for this send\n"
        + render_audience(aud)
        + "\n\n## The campaign you proposed, which the user REJECTED\n"
        + f"channel: {previous.get('channel')}\n"
        + f"timing: {previous.get('send_timing')}\n"
        + f"subject: {previous.get('subject')}\n"
        + f"body:\n{previous.get('body')}\n"
        + "\n## The user's feedback, verbatim\n"
        + f'"{user_feedback}"\n\n'
        + "## Your task\n"
        "Rewrite the campaign so it addresses this feedback directly. Change "
        "channel, timing, tone or copy as needed. Do not simply resend the same "
        "message. Keep what the feedback did not object to.\n"
        "Also fill `learned` with EVERY durable lesson in this feedback -- one "
        "entry each, with its own applies_everywhere flag."
    )
    try:
        draft, meta = _ask(CAMPAIGN_SYSTEM_PROMPT, prompt, DraftedCampaign)
        channel = draft.channel
        blocked = {c.rule.lower() for c in bundle.corrections}
        forced = None
        if channel == "sms" and any("sms" in r for r in blocked):
            channel = "email"
            forced = "channel forced to email by a learned correction"

        fixes = _detemplate(
            [{"id": "c1", "type": channel, "body": draft.body, "subject": draft.subject}],
            bundle,
        )
        campaign = {
            "name": draft.name,
            "channel": channel,
            "subject": draft.subject if channel == "email" else "",
            "body": draft.body,
            "send_timing": draft.send_timing,
            "audience_description": aud.description,
            "audience_size": aud.size,
            "audience_basis": aud.basis,
            "audience_sample": aud.sample_names,
            "_copy_fixes": fixes,
            "_forced": forced,
        }
        return AuditOutput(
            headline=f"Revised — {draft.name}",
            observation=draft.observation,
            audit_summary=draft.rationale,
            findings=([forced] if forced else [])
            + [f"Still targeting the same {aud.size} people."],
            predicted_impact=draft.predicted_impact,
            confidence=draft.confidence,
            source="llm",
            drafted_campaign=campaign,
            learned=[(x.rule, x.applies_everywhere) for x in draft.learned],
            **meta,
        )
    except Exception as exc:
        return _campaign_with_rules(
            bundle, aud, reason=f"{type(exc).__name__}: {exc}"[:200]
        )


def _campaign_with_rules(
    bundle: ContextBundle, aud: Any, reason: str | None = None
) -> AuditOutput:
    """Offline campaign shell. Copy is explicitly NOT fabricated."""
    return AuditOutput(
        headline=f"{aud.size} people match — campaign needs copy",
        observation=f"{bundle.signal.title}. {bundle.signal.detail}",
        audit_summary=(
            f"{aud.size} profiles already match: {aud.description}. The audience "
            "and timing are resolved, but the message body is marked [NEEDS COPY] "
            "because it was produced without a model."
        ),
        findings=[f"Audience rule: {aud.basis}"],
        predicted_impact="Clears the existing backlog once copy is written.",
        confidence=0.3,
        source="rules",
        drafted_campaign={
            "name": f"{(bundle.signal.vertical or 'Account').title()} Re-Engagement",
            "channel": "email",
            "subject": "[NEEDS COPY]",
            "body": "[NEEDS COPY] Written by the agent when a model is available.",
            "send_timing": "after review",
            "audience_description": aud.description,
            "audience_size": aud.size,
            "audience_basis": aud.basis,
            "audience_sample": aud.sample_names,
        },
        fallback_reason=reason,
    )
