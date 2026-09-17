"""The reactive path: Composer as it works today.

You type what you want, it builds it. This exists in the demo purely as a
CONTRAST, and the contrast is not "chat is bad" -- the generation is genuinely
good. It is that the chat cannot tell you WHAT TO ASK FOR.

Two differences are worth showing on screen:

  grounding   A prompt-built campaign's audience is whatever the model infers
              from your sentence. The proactive path's audience is a number
              returned by a query over real events. One is a guess, one is
              counted.

  initiative  To get value from the chat you must already know the 5:30pm
              class has a problem. Knowing that is the agent's job.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from universal_events.config import settings

CHAT_SYSTEM_PROMPT = """\
You are Klaviyo Composer. A marketer describes a campaign in plain language and \
you produce a launch-ready draft: audience, channel, subject and body.

Rules:
- Write complete, on-brand copy. No placeholders like "[insert offer]".
- Use {{ first_name }} for personalisation, never a real name.
- Describe the audience as a segment definition the marketer could build.
- If the request is vague, make a sensible choice and say what you assumed \
in `assumptions` rather than asking a question back.
- Be honest in `grounding_note` about what you could NOT verify: you are \
working from the prompt alone and have not queried the account's data, so you \
do not know how many people match, or whether the premise is even true."""


class ChatCampaign(BaseModel):
    """What the reactive path returns."""

    name: str
    channel: Literal["email", "sms"]
    audience_description: str = Field(
        description="The segment you would target, as a definition"
    )
    estimated_reach: str = Field(
        description="Your guess at size, and say plainly that it is a guess"
    )
    subject: str
    body: str
    assumptions: list[str] = Field(
        description="What you had to assume because the prompt didn't say"
    )
    grounding_note: str = Field(
        description="What you could not verify without querying the account"
    )


def generate(prompt: str) -> dict[str, Any]:
    """Build a campaign from a prompt. Never raises."""
    if settings.active_provider != "openai" or not settings.openai_configured:
        return {
            "ok": False,
            "error": "No OpenAI key configured, so the reactive path is unavailable.",
        }

    import openai

    started = time.monotonic()
    try:
        response = openai.OpenAI(api_key=settings.openai_api_key).responses.parse(
            model=settings.openai_audit_model,
            input=[
                {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text_format=ChatCampaign,
        )
        draft = response.output_parsed
        if draft is None:
            raise RuntimeError("no parsable campaign returned")
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}

    usage = getattr(response, "usage", None)
    tin = getattr(usage, "input_tokens", None) if usage else None
    tout = getattr(usage, "output_tokens", None) if usage else None
    from .auditor import _cost

    cost = _cost(settings.openai_audit_model, tin, tout)

    return {
        "ok": True,
        "campaign": draft.model_dump(),
        "model": settings.openai_audit_model,
        "cost_usd": cost,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


SUGGESTED_PROMPTS = [
    "Build a win-back campaign for members who haven't been to class in a while",
    "Write a re-engagement email for lapsed donors",
    "Create a campaign for people who keep missing their classes",
]
