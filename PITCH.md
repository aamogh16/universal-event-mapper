# Positioning — read this before demoing

## The frame for the video

Lead with the conversation, not with a claimed gap.

> "I talked to Elias. He described wanting Composer to move from reactive to
> proactive — business notifications rather than push notifications — and an
> agent that learns from its mistakes. So I built that."

That is the pitch. It does not depend on anything being missing from the
product, which matters because Composer has shipped features that overlap
with parts of this ("Discover missing flows", "Audit my segments" are both
suggestion chips on the Klaviyo home screen today).

**Overlap is validation, not a problem.** Building toward the same place the
team is already heading is a good sign, not a redundancy. Do not claim novelty
you cannot defend; claim that you listened and then shipped something working
against their real API. For a co-op, execution speed and listening are the
things being evaluated, and both are demonstrable here.

If someone says "Composer already does some of this", the honest answer is:
"Good — then I was building in the right direction. The part I found most
interesting was the bit Elias mentioned that I haven't seen anywhere: being
told no and not needing to be told twice."

---


## The claim to never make

> "Klaviyo can't read data from a gym, restaurant, or nonprofit."

This is **false**, and it is false about the exact tools this demo mocks:

| Tool | Reality |
|---|---|
| Mindbody | Native connector. Syncs appointments, classes, memberships, purchases. Part of a named product line, **Klaviyo for Wellness** (with Zenoti and Boulevard). |
| Toast | Native connector. Syncs order events plus **3 years of history**. Enabled from Toast's own integrations page. Docs specifically cover Toast Tables. |
| Bloomerang Fundraising | Documented connector, API-token setup. Personalize on donation and website activity. |

Garret and Luke work on Composer. They will know this — one of these is a
product line. Opening with a false premise ends the conversation before the
demo starts.

## The accurate claim

> Klaviyo has hundreds of connectors, and every one is a bespoke build its own
> team designed, shipped, and now maintains. That model works for tools big
> enough to justify a partnership. It does not scale to the long tail. A
> business whose tool has no connector has no path at all unless it employs
> engineers — and the businesses Klaviyo wants next are exactly the ones that
> don't.
>
> So growth into new verticals is rate-limited by engineering time per
> integration. This demo shows a generic path that needs no per-tool build.

This is also **closer to what Elias actually said** — that the blocker is
customers "who don't have engineers to build a custom integration." That is a
per-integration-cost argument, not a can't-read-the-data argument.

## Why the accurate version is the better pitch

1. **It survives scrutiny.** Nothing in it can be fact-checked into collapse.
2. **It shows homework.** Naming Klaviyo for Wellness signals you studied the
   product instead of assuming a gap.
3. **It flatters the existing work** rather than ignoring it, then asks a
   question that work doesn't answer.
4. **It is a bigger market.** "The tools too small to ever integrate" is a
   larger TAM than "non-ecommerce", and it's a CPO-shaped framing: cost per
   integration versus verticals reached.

## Opening lines to actually say

> "Klaviyo already has great connectors for Mindbody, Toast, and Bloomerang —
> there's a whole Klaviyo for Wellness line. Each one is hand-built and
> maintained by your team, which makes sense for partners that size.
>
> My question was what happens for the ten-thousandth tool, the one that will
> never justify that build. So I pointed this at a veterinary clinic's booking
> system, which nobody has integrated and nobody will."

Then paste a payload they've never seen and let it land in Klaviyo.

## What this changes about the demo order

The old plan led with Mindbody. That now demonstrates **something Klaviyo
already does**, which is the weakest possible opening.

**Lead with an unintegrated tool instead.**

- `UNKNOWN_PAYLOADS` (veterinary, tutoring, physical therapy) are the proof.
  No connector exists and none is coming. These go first.
- The four configured sources (Mindbody / Toast / Bloomerang / Calendly)
  become the **parity** point, shown second and briefly: the same result from
  a YAML file instead of a Klaviyo engineering project.
- **Strongest version:** let them hand you a payload live. Unfakeable.

The code records which is which — `Source.klaviyo_connector` is `"native"`,
`"none"`, or `"unverified"` — so this distinction can't be forgotten later.

## Pushback to expect, and honest answers

**"We already integrate Mindbody."**
Yes, and that connector is better than mine for Mindbody specifically. I'm not
proposing replacing it. I'm asking what a vet clinic does — there are ~28,000
in the US and no connector will ever be built for their booking software.

**"How is this different from Zapier or our public Events API?"**
Both require someone to decide what maps to what. The API needs an engineer;
Zapier needs a human to wire fields. Here the mapping is *inferred* from the
payload's shape, so the setup step itself disappears. That's the difference.

**"What if it maps something wrong?"**
It shows you the mapping before sending, and the config path is deterministic
for anything you've locked in. Inference is for onboarding, not for steady
state — once inferred, a mapping can be saved as a config and never involves a
model again.

**"Isn't inference unreliable?"**
Deterministic structural inference handles the current cases with no model at
all, offline. The model is for genuinely ambiguous payloads, and its output is
constrained to field *paths*, not values, so it can't invent a customer's
email address.
