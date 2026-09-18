# Feature tour

Every capability, what it does, and how to exercise it. Each section says what
to run and what you should see, so you can verify any claim in the README
yourself.

```bash
./demo reset            # 304 backdated events, flows to v1, nothing learned
./demo sync-profiles    # create those members as Klaviyo profiles
./demo doctor           # Klaviyo reachable · provider · background job state
./serve                 # http://localhost:8000
```

`doctor` should report `Klaviyo OK`, `LLM openai`, and `business fitness`.
If Klaviyo fails or the provider reads `rules`, a key is missing — everything
below still works, but the model-driven paths degrade to deterministic ones.

---

## 1 · Getting events in

### Three mapping strategies, cheapest first

`config → llm → heuristic`. The UI badges which one ran.

| strategy | when | cost |
|---|---|---|
| **config** | a YAML mapping exists for this tool | free |
| **llm** | unfamiliar payload — a model infers the mapping | ~$0.002 once |
| **heuristic** | model unreachable — structural inference | free, offline |

```bash
./demo sources          # the tools, and which have a Klaviyo connector already
./demo map door_access  # "lock.unlock" -> Gym Check-In, on an existing member
./demo map tutoring     # picks the guardian, not the student
./demo map body_scan    # machine output from an InBody scanner
```

**What to look for:** the badge reads `llm` because no config exists for these.
The `tutoring` payload contains two people — a learner and a guardian — and it
picks the guardian, because that's who receives email and pays. The
`personal_training` payload says `price_paid_cents: 96000` and comes back as
**$960**, not $96,000.

**In the UI:** `Event ingest` tab. Each tool button shows the raw payload, the
inferred mapping, a field-by-field trace, and a link to the created Klaviyo
profile.

### The model returns paths, never values

`universal_events/mapping/llm_mapper.py`. The model says *"the email lives at
`patient.owner.contact_email`"* — it never repeats the address. Extraction
happens in code. So it cannot put a wrong value into a marketing profile, and
every decision is inspectable.

### Connecting a tool, and why the model is a one-time cost

```bash
./demo connect-door
```

One inference, saved as a YAML config, then three weeks of check-ins replayed
through that config — no further model calls.

**What to look for:** the win-back audience goes **9 → 8**. Chidi Mbeki drops
out: he looked silent for 33 days because he stopped *booking classes*, but the
door system shows he never stopped turning up. A system built on Mindbody alone
would have emailed him "we miss you" while he was in the gym.

Clicking it twice is safe — identical events are deduplicated, the same way
Klaviyo deduplicates on `(profile, metric, unique_id)`.

### Events reach Klaviyo for real

`POST https://a.klaviyo.com/api/events`, revision `2026-07-15`, `202 Accepted`.
202 means *queued*, not stored, which is why the profile lookup retries.

---

## 2 · Noticing things

### Signals are computed, not generated

```bash
./demo signals
```

Four findings, all SQL over the event mirror. No model is involved — *"the
5:30pm no-show rate is 3.0× the prior 14 days (14/54 vs 4/46)"* is arithmetic,
and arithmetic shouldn't be probabilistic.

Per-vertical thresholds matter: a gym member books weekly, a donor gives twice
a year, so one global "inactive" threshold would hide exactly the lapsed donors
worth finding.

### Two execution patterns, and why both are needed

```bash
./demo send fitness class_no_show   # trigger: reacts to one arriving event
./demo sweep                        # recurring: scans everyone, all windows
./demo runs                         # both, side by side
```

| | sees | finds |
|---|---|---|
| ⚡ **trigger** | one profile, instantly | *"Sam has 3 no-shows in 30 days"* |
| 🕐 **sweep** | everyone, across time windows | *"the 5:30pm rate tripled"*, *"Jordan went quiet"* |

They differ by **scope of data**, not timing. A trigger can only react to
something that *arrives* — and nothing fires when a member loses interest.
Finding Jordan requires looking at the gap between his last event and now,
which only a recurring scan can do.

Set `AUTO_SWEEP=true` and the recurring job runs on a background thread. Watch
the `Activity` tab fill on its own; the `created=0` rows at ~30ms are the point
— it costs nothing when there's nothing new, because proposals deduplicate.

---

## 3 · Proposing

Three kinds, chosen by what the account actually needs:

| kind | when | shows |
|---|---|---|
| **patch** | a flow exists but mishandles the signal | a real diff |
| **create** | no flow covers this signal at all | a drafted automation |
| **campaign** | people are *already* in the bad state | a message and its audience |

The campaign kind exists because a new flow only catches future cases. It does
nothing for the eight members who already left.

### The model returns edit operations, never a rewritten flow

`composer/patch.py`. Three operations only:

```python
"set_field"      # change a scalar on a step
"insert_after"   # add a new step after an existing one
"fill_branch"    # put steps into a split's empty branch
```

Two others existed — `remove_step` and `set_trigger` — and across measured runs
the model never chose either, while both produced the only bad patches seen.
Removing a capability beat policing it.

**The diff is produced by applying the patch** (`composer/proposals.py`, in
`_revision_from_audit`) and diffing the result — not by the model describing its
own change. That's what makes the approve button trustworthy.

### Context is assembled before it reasons

`composer/context.py`. Shown in the UI as chips: how many events for this
profile, lifetime value, which flow, how many learned corrections apply. The
agent reads history before forming an opinion, and you can see what it read.

### Notifications have to earn the interruption

Triggers always interrupt — something just happened to someone. A scheduled
sweep only interrupts for high-severity findings; everything else waits in the
inbox with a count. An agent that interrupts eight times has become the push
notification it was meant to replace.

---

## 4 · Approving, rejecting, and learning

```bash
./demo inbox
./demo show <id>
./demo approve <id>
./demo reject <id> "don't send these immediately, schedule for 9am"
./demo corrections
```

### Approve

A **patch** versions the flow (`v1 → v2`, check `./demo flows`). A **create**
adds a new flow as a draft. A **campaign** creates a **real Klaviyo campaign in
Draft status** with the audience attached — and sends nothing.

### Reject, and it revises

Feedback in plain language produces a new revision rather than discarding the
work. The UI keeps both, so you can click between `revision 1` and
`revision 2 · after your feedback`.

### It learns — and this is the part worth verifying

Reject a campaign with:

```
Don't send these the moment you write them — schedule for 9am local. And don't tell members how many visits they've had, it reads like surveillance.
```

Two sentences become two rules, each scoped independently — one to this
business, one to every business. Then **approve the revision** (that lifts the
duplicate block) and **sweep again**.

**What to look for in the new proposal:**
1. A `2 learned corrections applied` badge
2. `channel` reads `email · 9am local time`, not `now`
3. No visit-count line in the copy
4. The corrections now read `used 1×`
5. Its own audit text citing the constraint as the reason for its choice

Same signal, same people, nothing said to it — and the output differs in
exactly the two ways you corrected. That's a controlled experiment, not a claim.

---

## 5 · The reactive path, for contrast

`Chat · today` tab, or `POST /api/chat`. Describe a campaign and it builds one.
The generation is good; that isn't the problem.

It reports what it **couldn't verify** — attendance thresholds, audience size —
and guesses a percentage where the proactive path counted eight people and
named them. The real difference: to get value from the chat you have to already
know the 5:30pm class has a problem.

---

## Reference

### Everything the CLI does

```
doctor          preflight: keys, connectivity, data, background job state
reset           wipe and reseed — identical starting state every time
sync-profiles   create the seeded members as Klaviyo profiles
sources         the mock tools, and which Klaviyo already integrates
map <tool>      map one payload and send it
connect-door    infer a mapping once, save it, replay the history
signals         what it notices, computed by query
sweep           run the recurring job once
send <src> <ev> fire one event and let the trigger react
inbox / show    the proposal inbox, and one proposal in full
approve / reject
corrections     what it has been taught, and how often it's been used
flows           automations and their versions
runs            both execution patterns, side by side
```

### What's real and what isn't

| | |
|---|---|
| Events reaching Klaviyo | **real** — live API, 202, profiles you can open |
| Signals and audience counts | **real** — SQL over the event mirror |
| Audits, copy, revisions | **real** — live model calls, cost shown per proposal |
| Learned corrections | **real** — persisted, scoped, re-applied |
| Campaigns | **real** — approving creates a Klaviyo Draft |
| Campaign *sending* | **never** — `send-jobs` is deliberately not implemented |
| Flows | **mock** — simplified stand-ins, not Klaviyo flow definitions |

### Measured, not assumed

- **model choice:** `gpt-5-nano` took 77s and missed the obvious finding;
  `gpt-5-mini` took 23s and cost *more* than `gpt-5.4-mini`, which answers in
  ~5s. Per-token price is not cost when a model reasons more.
- **~$0.0035** per proposal. ~$350/day for a daily proposal across 100k
  businesses.
- Prompt caching would cut input cost ~90% on the repeated system prompts; not
  implemented, because the absolute numbers are trivial at this scale.

### If something looks wrong

| symptom | cause |
|---|---|
| inbox full on open | the background sweeper ran — set `AUTO_SWEEP=false` |
| sweep creates nothing | proposals already open for those signals; `reset` |
| yellow `deterministic fallback` | the model call failed; it degraded on purpose |
| a proposal looks thin | output varies per run; try another |
