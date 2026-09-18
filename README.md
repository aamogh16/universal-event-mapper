# Composer — a proactive agent prototype

An agent that watches a business's event stream, notices problems on its own,
and hands you a **specific fix to approve or reject**. When you reject it, it
revises — and remembers, so you never have to say the same thing twice.

Built against the **real Klaviyo API** (events land in a live account) and
**OpenAI** for the reasoning.

---

## The premise, stated accurately

Klaviyo already has hand-built connectors for Mindbody, Toast and Bloomerang —
Mindbody is part of a named product line, **Klaviyo for Wellness**. So the
interesting problem is *not* "Klaviyo can't read non-ecommerce data." It can.

The real constraint is narrower and still real:

> Every connector is a bespoke build Klaviyo's own team ships and maintains.
> That works for partners big enough to justify it. It does not scale to the
> long tail. A business whose tool has no connector has no path at all unless
> it employs engineers — and the businesses Klaviyo wants next are exactly the
> ones that don't.

Composer today also already *generates* campaigns and flows from a prompt, and
Klaviyo's own materials describe it as proactive with recurring audits. So the
thing this prototype explores is the part that isn't there yet:

**an agent that gets told no, is right the second time, and doesn't need
telling again.**

---

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add KLAVIYO_PRIVATE_API_KEY and OPENAI_API_KEY
./demo reset                  # seed 300+ backdated events, flows to v1
./demo sync-profiles          # create those members as Klaviyo profiles
./serve                       # dashboard at http://localhost:8000
```

Or drive it from the terminal:

```bash
./demo --help
```

Both keys are optional. Without OpenAI it falls back to deterministic
inference; without Klaviyo it prints the exact HTTP request instead of sending.

---

## What it does

### 1. Get events in from a tool nobody integrated

Three strategies, tried cheapest-first. The UI shows which one ran.

| strategy | when | cost |
|---|---|---|
| **config** | a YAML mapping exists for this tool | free |
| **llm** | unfamiliar payload — a model infers the mapping | ~$0.002 |
| **heuristic** | model unavailable — structural inference | free, offline |

```bash
# The same gym's other tools -- none have a Klaviyo connector
./demo map door_access        # "lock.unlock" -> Gym Check-In, on an existing member
./demo map personal_training  # catches price_paid_cents and converts it
./demo map body_scan          # machine output from the InBody scanner

# Other industries, for breadth
./demo map tutoring           # picks the guardian, not the student
./demo map veterinary         # picks the pet's owner, not the pet
./demo map physical_therapy   # pulls the patient out of a FHIR telecom array
```

### 2. Notice things, and propose fixes

Signals are computed **by query, never by a model** — "the 5:30pm no-show rate
is 3.0× the prior 14 days" is arithmetic, and arithmetic should not be
probabilistic. The model's job starts at deciding what to *do*.

Three kinds of proposal:

- **patch** — a flow exists but mishandles the signal. Shows a real diff.
- **create** — nothing covers this signal, so it drafts a new automation.
- **campaign** — people are *already* in the bad state. A new flow only
  catches future cases, so it drafts a one-off send to clear the backlog.

```bash
./demo sweep                 # the recurring job
./demo send fitness class_no_show    # the trigger pattern
./demo show <id>
./demo approve <id>
./demo reject <id> "our donors are older, don't use SMS"
./demo corrections           # what it has learned
```

### 3. Learn from rejection

Reject with plain language and it extracts **every** durable rule, scopes it
per-vertical, and applies it to future proposals unprompted.

> *"Our donors are older, don't propose SMS. And never ask for another gift
> within 30 days."*

becomes

```
[nonprofit] Do not propose SMS for older donors.
[nonprofit] Never ask for another gift within 30 days.
```

---

## Design decisions worth knowing

**The model returns paths, never values.** For mapping, it says the email
lives at `patient.owner.contact_email` — it never repeats the address.
Extraction happens in code. It cannot put a wrong value into someone's
marketing profile, the mapping is auditable, and it can be saved as a YAML
config so the model becomes a one-time onboarding cost rather than a
per-event one.

**The model returns edit operations, never a rewritten flow.** It says
`set s1.hours = 24`, not "here is the new flow." It cannot silently drop a
step, and **the diff you approve is produced by actually applying the patch** —
not by the model describing its own change.

**Deterministic where it's arithmetic, model-driven where it's judgment.**
Signal detection, audience counts, patch application, diffing and validation
are all plain code. Auditing, copy and revision are the model.

**Model choice was measured, not assumed.** `gpt-5-nano` took 77 seconds,
burned ~11k reasoning tokens and still missed the obvious finding.
`gpt-5.4-mini` answers in ~5s and gets it right, for a third of the cost.

---

## What's real and what isn't

| | |
|---|---|
| Events reaching Klaviyo | **real** — live API, 202 Accepted, profiles you can open |
| Signals and audience counts | **real** — SQL over the local event mirror |
| Audits, copy, revisions | **real** — live model calls, cost shown per proposal |
| Learned corrections | **real** — persisted, scoped, re-applied |
| Flows | **mock** — simplified JSON, not Klaviyo flow definitions |
| Campaigns | **real** — approving creates a Draft campaign in Klaviyo |
| Campaign *sending* | **never** — `send-jobs` is deliberately not implemented |

Flows staying mock is deliberate — they are a simplified stand-in so the
reasoning is legible. A real Klaviyo flow definition is a graph of linked
actions with a separate entry filter; an ordered list keeps the diff readable.

Campaign *sending* is deliberate too. `POST /api/campaigns/{id}/send-jobs` is
the only endpoint that transmits email, and it is intentionally not
implemented: on a free plan, email #501 auto-upgrades the account with no grace
period, and nothing about this is improved by emailing nine fictional people.

### A note on profiles

Seeded history is written to the local mirror only, so `reset` does not push
300 events into a 250-profile free account. That means the seeded members do
not exist in Klaviyo until you run `./demo sync-profiles`, which creates them
as identities (no events). In production every event flows through to Klaviyo,
so those profiles would already be there and a campaign would simply
reference them — this makes the demo match that.

---

## Layout

```
universal_events/          the input layer
  sources.py               mock payloads: 4 configured tools, 3 of the gym's
                           unintegrated tools, 3 other verticals
  mapping/
    pipeline.py            strategy selection: config -> llm -> heuristic
    config_mapper.py       deterministic, YAML-driven
    llm_mapper.py          infers a mapping; returns PATHS, never values
    heuristic.py           offline structural inference, no model
    base.py  paths.py      shared types and dotted-path resolution
  connect.py               infer a tool's mapping once, save it as a config
  klaviyo.py               Events API client + real campaign creation
  store.py  seed.py        local event mirror and deterministic demo history
  config.py                settings, and the pinned Klaviyo API revision
  api.py  cli.py  web/     dashboard and CLI

composer/                  the agent
  signals.py               what changed, computed by query
  audience.py              who it affects, counted not guessed
  context.py               what the agent knows before it reasons
  auditor.py               audit, draft, revise — every model call
  patch.py                 the three edit ops, validation, diffing
  proposals.py             approve / reject / revise lifecycle
  feedback.py              learned corrections
  flow_store.py            flow versions and signal-to-flow matching
  rules.py                 deterministic auditor, the offline fallback
  runners.py               trigger and recurring patterns
  chat.py                  the reactive path, for contrast
  flows/                   mock automations (6 for the gym, 3 elsewhere)
```

## Configuration

Everything lives in `.env` — see `.env.example`. Notable knobs:

- `DEMO_VERTICAL` — which business the demo is about (`fitness`); `""` for all
- `SWEEP_INTERVAL_SECONDS` — 60 for demos, 86400 in production
- `AUTO_SWEEP` — run the recurring job on a thread at startup
- `LLM_PROVIDER` — `openai`, `gemini`, or `rules` to force the offline path
- `CREATE_REAL_CAMPAIGNS` — approving a campaign creates a Klaviyo Draft
- `CAMPAIGN_FROM_EMAIL` / `CAMPAIGN_FROM_LABEL` — sender on created campaigns
- `KLAVIYO_DRY_RUN` — build requests without sending them
