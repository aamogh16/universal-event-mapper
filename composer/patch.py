"""Flow edit operations: the only way a flow ever changes.

The agent never rewrites a flow. It returns a short list of EditOps, which we
apply here in plain Python. Three consequences that matter:

  1. The agent cannot silently drop or invent a step -- it can only name a
     change to an existing one.
  2. Every op is validated before anything is shown, so an unappliable patch
     is caught rather than displayed.
  3. The diff the user approves is produced by ACTUALLY APPLYING the patch and
     diffing the result. It is not the agent's description of its own change.

That third point is what makes the approve button trustworthy.
"""

from __future__ import annotations

import copy
import difflib
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field

OpName = Literal[
    "set_field",      # change a scalar on a step (delay hours, subject, body)
    "insert_after",   # add a new step after an existing one
    "remove_step",    # delete a step
    "fill_branch",    # put steps into a split's empty branch
    "set_trigger",    # change what starts the flow
]

STEP_TYPES = {"delay", "email", "sms", "split", "update_profile"}


class EditOp(BaseModel):
    """One change to a flow. Deliberately small and explicit."""

    op: OpName
    rationale: str = Field(default="", description="Why this specific change")
    step_id: str | None = None
    field: str | None = None
    value: Any = None
    step: dict[str, Any] | None = None
    branch: Literal["true", "false"] | None = None

    def describe(self) -> str:
        if self.op == "set_field":
            return f"{self.step_id}: set {self.field} = {self.value!r}"
        if self.op == "insert_after":
            kind = (self.step or {}).get("type", "step")
            return f"insert {kind} after {self.step_id}"
        if self.op == "remove_step":
            return f"remove {self.step_id}"
        if self.op == "fill_branch":
            n = len(self.value or [])
            return f"{self.step_id}: fill {self.branch} branch with {n} step(s)"
        if self.op == "set_trigger":
            return f"trigger -> {self.value!r}"
        return self.op


class PatchError(BaseModel):
    op_index: int
    message: str


class PatchResult(BaseModel):
    flow: dict[str, Any]
    applied: list[str] = Field(default_factory=list)
    errors: list[PatchError] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.applied)


# --------------------------------------------------------------------- lookup


def walk_steps(steps: list[dict]) -> Iterator[dict]:
    """Yield every step, descending into split branches."""
    for step in steps:
        yield step
        if step.get("type") == "split":
            yield from walk_steps(step.get("true_branch") or [])
            yield from walk_steps(step.get("false_branch") or [])


def find_step(flow: dict, step_id: str) -> dict | None:
    for step in walk_steps(flow.get("steps") or []):
        if step.get("id") == step_id:
            return step
    return None


def _find_container(steps: list[dict], step_id: str) -> tuple[list[dict], int] | None:
    """Locate the list holding `step_id` and its index, for insert/remove."""
    for index, step in enumerate(steps):
        if step.get("id") == step_id:
            return steps, index
        if step.get("type") == "split":
            for branch in ("true_branch", "false_branch"):
                found = _find_container(step.get(branch) or [], step_id)
                if found:
                    return found
    return None


def next_step_id(flow: dict) -> str:
    """Allocate an unused step id."""
    used = {s.get("id", "") for s in walk_steps(flow.get("steps") or [])}
    n = len(used) + 1
    while f"s{n}" in used:
        n += 1
    return f"s{n}"


# ---------------------------------------------------------------------- apply


def apply_patch(flow: dict, ops: list[EditOp]) -> PatchResult:
    """Apply ops to a COPY of flow. Collects errors instead of raising."""
    draft = copy.deepcopy(flow)
    applied: list[str] = []
    errors: list[PatchError] = []

    for index, op in enumerate(ops):
        try:
            if op.op == "set_field":
                step = find_step(draft, op.step_id or "")
                if step is None:
                    errors.append(PatchError(op_index=index, message=f"no step {op.step_id!r}"))
                    continue
                if not op.field:
                    errors.append(PatchError(op_index=index, message="missing field"))
                    continue
                step[op.field] = op.value

            elif op.op == "insert_after":
                located = _find_container(draft.get("steps") or [], op.step_id or "")
                if located is None:
                    errors.append(PatchError(op_index=index, message=f"no step {op.step_id!r}"))
                    continue
                new_step = dict(op.step or {})
                if new_step.get("type") not in STEP_TYPES:
                    errors.append(
                        PatchError(op_index=index, message=f"bad step type {new_step.get('type')!r}")
                    )
                    continue
                new_step.setdefault("id", next_step_id(draft))
                container, position = located
                container.insert(position + 1, new_step)

            elif op.op == "remove_step":
                located = _find_container(draft.get("steps") or [], op.step_id or "")
                if located is None:
                    errors.append(PatchError(op_index=index, message=f"no step {op.step_id!r}"))
                    continue
                container, position = located
                container.pop(position)

            elif op.op == "fill_branch":
                step = find_step(draft, op.step_id or "")
                if step is None or step.get("type") != "split":
                    errors.append(PatchError(op_index=index, message=f"{op.step_id!r} is not a split"))
                    continue
                key = f"{op.branch or 'true'}_branch"
                new_steps = []
                for raw in op.value or []:
                    entry = dict(raw)
                    if entry.get("type") not in STEP_TYPES:
                        errors.append(
                            PatchError(op_index=index, message=f"bad step type {entry.get('type')!r}")
                        )
                        continue
                    entry.setdefault("id", next_step_id(draft))
                    new_steps.append(entry)
                    # Register the id so the next allocation doesn't collide.
                    step.setdefault(key, []).append(entry)
                step[key] = new_steps

            elif op.op == "set_trigger":
                draft.setdefault("trigger", {})["metric"] = op.value

            else:
                errors.append(PatchError(op_index=index, message=f"unknown op {op.op!r}"))
                continue

            applied.append(op.describe())

        except Exception as exc:  # a bad op must not crash the demo
            errors.append(PatchError(op_index=index, message=f"{type(exc).__name__}: {exc}"))

    errors.extend(
        PatchError(op_index=-1, message=problem) for problem in validate_flow(draft)
    )
    return PatchResult(flow=draft, applied=applied, errors=errors)


def validate_flow(flow: dict) -> list[str]:
    """Structural checks. Empty list means the flow is coherent."""
    problems: list[str] = []
    steps = flow.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["flow has no steps"]

    seen: set[str] = set()
    for step in walk_steps(steps):
        sid = step.get("id")
        if not sid:
            problems.append("a step is missing an id")
            continue
        if sid in seen:
            problems.append(f"duplicate step id {sid!r}")
        seen.add(sid)
        kind = step.get("type")
        if kind not in STEP_TYPES:
            problems.append(f"{sid}: unknown step type {kind!r}")
        elif kind == "delay" and not isinstance(step.get("hours"), (int, float)):
            problems.append(f"{sid}: delay needs numeric `hours`")
        elif kind in ("email", "sms") and not str(step.get("body") or "").strip():
            problems.append(f"{sid}: {kind} has no body")
        elif kind == "split":
            cond = step.get("condition")
            if not isinstance(cond, dict):
                problems.append(
                    f"{sid}: split condition must be an object with field/op/value, "
                    f"got {type(cond).__name__}"
                )
            elif not cond.get("field"):
                problems.append(f"{sid}: split condition has no field")
            if not (step.get("true_branch") or step.get("false_branch")):
                problems.append(f"{sid}: split has no steps on either branch")
    return problems


# ----------------------------------------------------------------------- diff


def _fmt_delay(hours: float) -> str:
    if hours >= 48 and hours % 24 == 0:
        return f"{int(hours // 24)} days"
    if hours >= 24 and hours % 24 == 0:
        return f"{int(hours // 24)} day"
    return f"{int(hours)}h" if float(hours).is_integer() else f"{hours}h"


def render_outline(flow: dict) -> list[str]:
    """A canonical, human-readable outline. Diffs are computed over this."""
    trigger = flow.get("trigger") or {}
    # A flow can be started by an event OR by entering a segment. An absence
    # ("stopped booking") fires no event, so segment triggers are normal and
    # must render as something other than "None".
    if trigger.get("metric"):
        trigger_label = f"event = {trigger['metric']}"
    elif trigger.get("segment"):
        trigger_label = f"segment = {trigger['segment']}"
    else:
        trigger_label = "(none)"
    lines = [
        f"flow: {flow.get('name')}  (v{flow.get('version')}, {flow.get('status')})",
        f"trigger: {trigger_label}",
        "",
    ]

    def emit(steps: list[dict], indent: int) -> None:
        pad = "  " * indent
        if not steps:
            lines.append(f"{pad}(empty -- recipients exit here)")
            return
        for step in steps:
            sid = step.get("id")
            kind = step.get("type")
            if kind == "delay":
                lines.append(f"{pad}[{sid}] wait {_fmt_delay(step.get('hours', 0))}")
            elif kind in ("email", "sms"):
                label = "email" if kind == "email" else "SMS"
                subject = step.get("subject")
                head = f"{pad}[{sid}] {label}"
                if subject:
                    head += f'  "{subject}"'
                lines.append(head)
                body = str(step.get("body") or "").strip()
                if body:
                    lines.append(f"{pad}      {body}")
                if step.get("cta"):
                    lines.append(f"{pad}      CTA: {step['cta']}")
                if step.get("send_if"):
                    lines.append(f"{pad}      only if: {step['send_if']}")
            elif kind == "split":
                cond = step.get("condition")
                if isinstance(cond, dict):
                    label = (
                        f"{cond.get('field')} {cond.get('op')} {cond.get('value')!r}"
                    )
                elif cond:
                    label = str(cond)
                else:
                    label = "(no condition)"
                lines.append(f"{pad}[{sid}] split if {label}")
                lines.append(f"{pad}  yes:")
                emit(step.get("true_branch") or [], indent + 2)
                lines.append(f"{pad}  no:")
                emit(step.get("false_branch") or [], indent + 2)
            else:
                lines.append(f"{pad}[{sid}] {kind}")

    emit(flow.get("steps") or [], 0)
    return lines


def render_diff(before: dict, after: dict, context: int = 3) -> str:
    """Unified diff between two flow versions."""
    return "\n".join(
        difflib.unified_diff(
            render_outline(before),
            render_outline(after),
            fromfile=f"{before.get('name')} v{before.get('version')}",
            tofile=f"{after.get('name')} v{after.get('version', 0)}",
            lineterm="",
            n=context,
        )
    )


class DiffLine(BaseModel):
    kind: Literal["context", "add", "remove", "meta"]
    text: str


def diff_lines(before: dict, after: dict, context: int = 3) -> list[DiffLine]:
    """Structured diff for the web UI to colourise."""
    out: list[DiffLine] = []
    for raw in difflib.unified_diff(
        render_outline(before), render_outline(after), lineterm="", n=context
    ):
        if raw.startswith(("---", "+++")):
            continue
        if raw.startswith("@@"):
            out.append(DiffLine(kind="meta", text=raw))
        elif raw.startswith("+"):
            out.append(DiffLine(kind="add", text=raw[1:]))
        elif raw.startswith("-"):
            out.append(DiffLine(kind="remove", text=raw[1:]))
        else:
            out.append(DiffLine(kind="context", text=raw[1:] if raw.startswith(" ") else raw))
    return out


def has_changes(before: dict, after: dict) -> bool:
    return render_outline(before) != render_outline(after)
