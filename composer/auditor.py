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
# gpt-5.4-mini, USD per 1M tokens. Chosen over gpt-5-nano on measurement:
# nano burned ~11k reasoning tokens per audit, took 77s, and still missed the
# obvious finding. 5.4-mini answered in ~6s for a third of the cost.
PRICE_IN = 0.25
PRICE_OUT = 2.00

# Fields that must end up numeric when the patch is applied.
NUMERIC_FIELDS = {"hours", "duration_hours", "days"}

Source = Literal["llm", "rules"]


class ProposedOp(BaseModel):
    """Mirror of EditOp, flattened for the model's output schema."""

    op: Literal["set_field", "insert_after", "remove_step", "fill_branch", "set_trigger"]
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


class LLMAudit(BaseModel):
    """Exactly what the model is allowed to return."""

    headline: str = Field(description="One line naming the problem, under 90 chars")
    observation: str = Field(description="What was noticed, citing the actual numbers")
    audit_summary: str = Field(description="Which steps were examined and what is wrong")
    findings: list[str] = Field(description="Specific problems, one per line")
    predicted_impact: str = Field(description="What changes if this is approved")
    ops: list[ProposedOp] = Field(description="The concrete edits to make")
    confidence: float = Field(description="0 to 1")
    learned_rules: list[str] = Field(
        default_factory=list,
        description="Only when revising: EVERY durable lesson in the user's "
        "feedback, one short imperative each. A single rejection often carries "
        "several ('no SMS' AND 'never ask within 30 days') -- return all of "
        "them, not just the first. Empty list otherwise.",
    )
    learned_rule_is_global: bool = Field(
        default=False,
        description="True ONLY if the lessons apply to every industry. Anything "
        "tied to this business type (donations, class bookings, appointments) is "
        "NOT global -- leave false.",
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
    learned_rules: list[str] = Field(default_factory=list)
    learned_rule_is_global: bool = False


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
    learned_rule: str | None = None
    learned_rule_is_global: bool = False
    # A single rejection often carries several lessons ("no SMS, and never ask
    # within 30 days"). Storing only the first silently loses the rest.
    learned_rules: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    # Set instead of `ops` when the proposal is to CREATE an automation
    # rather than patch one. Composer's native unit is a generated campaign
    # or flow, so this is the more product-shaped proposal of the two.
    drafted_flow: dict[str, Any] | None = None

    @property
    def kind(self) -> str:
        return "create" if self.drafted_flow else "patch"

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
- remove_step: delete step_id.
- fill_branch: populate an empty split branch. Use step_id, branch ("true" or \
"false"), and branch_steps_json as a JSON array string of step objects.
- set_trigger: change the triggering metric via value_text.

Valid step types: delay, email, sms, split, update_profile.
An email step needs subject and body. An sms step needs body."""


def _client() -> Any:
    import openai

    return openai.OpenAI(api_key=settings.openai_api_key)


def _call_llm(user_prompt: str) -> tuple[LLMAudit, dict[str, Any]]:
    started = time.monotonic()
    client = _client()
    response = client.responses.parse(
        model=settings.openai_audit_model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        text_format=LLMAudit,
    )
    latency = int((time.monotonic() - started) * 1000)
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("model returned no parsable output")

    usage = getattr(response, "usage", None)
    tokens_in = getattr(usage, "input_tokens", None) if usage else None
    tokens_out = getattr(usage, "output_tokens", None) if usage else None
    cost = None
    if tokens_in is not None and tokens_out is not None:
        cost = tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT

    return parsed, {
        "model_used": settings.openai_audit_model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": cost,
        "latency_ms": latency,
    }


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
        learned_rule=audit.learned_rules[0] if audit.learned_rules else None,
        learned_rule_is_global=audit.learned_rule_is_global,
        learned_rules=list(audit.learned_rules),
        **meta,
    )


def propose(bundle: ContextBundle) -> AuditOutput:
    """Audit the flow and propose a patch. Never raises."""
    if settings.active_provider != "openai" or not settings.openai_configured:
        return _from_rules(bundle, reason="no OpenAI key configured")

    prompt = (
        bundle.to_prompt()
        + "\n## Your task\n"
        "Audit the flow above against what was noticed. Identify why this flow "
        "mishandles this specific signal, then propose the concrete edits that "
        "fix it. Leave learned_rules empty."
    )
    try:
        audit, meta = _call_llm(prompt)
        return _finalise(audit, bundle, meta)
    except Exception as exc:
        return _from_rules(bundle, reason=f"{type(exc).__name__}: {exc}"[:200])


def revise(
    bundle: ContextBundle, previous_ops: list[EditOp], user_feedback: str
) -> AuditOutput:
    """Revise a rejected proposal using the user's feedback."""
    if settings.active_provider != "openai" or not settings.openai_configured:
        return _revise_with_rules(bundle, previous_ops, user_feedback)

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
        "Also fill learned_rules with EVERY durable lesson in this feedback -- "
        "one short imperative each -- so you never need to be told any of them "
        "again. If the user objected to two things, return two rules. Set "
        "learned_rule_is_global to true only if the lessons are industry-agnostic."
    )
    try:
        audit, meta = _call_llm(prompt)
        return _finalise(audit, bundle, meta)
    except Exception as exc:
        return _revise_with_rules(
            bundle, previous_ops, user_feedback, reason=f"{type(exc).__name__}: {exc}"[:200]
        )


# Offline revision: enough to keep the reject/revise beat working with no
# network. Handles the negations that actually come up in this demo.
NEGATION_PATTERNS = {
    "sms": ("sms", "text message", "texting"),
    "too_soon": ("too soon", "within 30", "30 days", "wait longer", "too fast", "too quick"),
}


def _revise_with_rules(
    bundle: ContextBundle,
    previous_ops: list[EditOp],
    user_feedback: str,
    reason: str | None = None,
) -> AuditOutput:
    lowered = user_feedback.lower()
    kept: list[EditOp] = []
    removed: list[str] = []
    learned: list[str] = []

    blocks_sms = any(token in lowered for token in NEGATION_PATTERNS["sms"]) and any(
        neg in lowered for neg in ("no ", "don't", "dont", "not ", "never", "avoid")
    )
    wants_slower = any(token in lowered for token in NEGATION_PATTERNS["too_soon"])

    for op in previous_ops:
        is_sms = (op.step or {}).get("type") == "sms" or (
            op.op == "fill_branch"
            and any((s or {}).get("type") == "sms" for s in (op.value or []))
        )
        if blocks_sms and is_sms:
            removed.append(op.describe())
            continue
        kept.append(op)

    if blocks_sms:
        learned.append("Never propose SMS steps for this audience")
    if wants_slower:
        learned.append("Do not ask for another gift within 30 days of the last one")
        for op in kept:
            if op.op == "set_field" and op.field == "hours":
                op.value = max(float(op.value or 0), 24 * 30)

    return AuditOutput(
        headline="Revised: " + (", ".join(learned) if learned else "adjusted per your feedback"),
        observation=f"{bundle.signal.title}. {bundle.signal.detail}",
        audit_summary=(
            f'You said: "{user_feedback}". '
            + (
                f"Removed {len(removed)} step(s) that conflicted: {', '.join(removed)}. "
                if removed
                else ""
            )
            + f"Kept the {len(kept)} change(s) you did not object to."
        ),
        findings=[f"Dropped: {r}" for r in removed],
        predicted_impact="The fix now respects the constraint you gave.",
        ops=kept,
        confidence=0.65,
        source="rules",
        learned_rule=learned[0] if learned else None,
        learned_rule_is_global=False,
        learned_rules=learned,
        fallback_reason=reason,
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

    for step in steps:
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

    clean: list[dict[str, Any]] = []
    for index, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            continue
        step = dict(raw)
        step["id"] = f"s{index}"
        # Consent gate is non-negotiable on SMS.
        if step.get("type") == "sms":
            step.setdefault("send_if", "sms_consent == true")
        clean.append(step)

    detemplated = _draft_to_flow_fixes = _detemplate(clean, bundle)

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
    if settings.active_provider != "openai" or not settings.openai_configured:
        return _draft_with_rules(bundle, reason="no OpenAI key configured")

    prompt = (
        bundle.to_prompt()
        + "\n## Your task\n"
        "Draft a new automation that handles the signal above. Explain why the "
        "business needs it, not just what it contains. Leave learned_rules empty."
    )
    try:
        started = time.monotonic()
        client = _client()
        response = client.responses.parse(
            model=settings.openai_audit_model,
            input=[
                {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text_format=DraftedFlow,
        )
        latency = int((time.monotonic() - started) * 1000)
        draft = response.output_parsed
        if draft is None:
            raise RuntimeError("model returned no parsable draft")

        flow = _draft_to_flow(draft, bundle)
        problems = validate_flow(flow)
        if problems:
            raise RuntimeError("drafted flow invalid: " + "; ".join(problems))

        usage = getattr(response, "usage", None)
        tin = getattr(usage, "input_tokens", None) if usage else None
        tout = getattr(usage, "output_tokens", None) if usage else None
        cost = (tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT) if tin and tout else None

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
            model_used=settings.openai_audit_model,
            input_tokens=tin,
            output_tokens=tout,
            cost_usd=cost,
            latency_ms=latency,
            drafted_flow=flow,
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
