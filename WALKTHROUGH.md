# Test run — hit everything

A complete pass through every feature, in an order that builds. Roughly 15
minutes at a relaxed pace. Each step has a **✓ check** so you know it worked.

---

## Part 0 · Setup (2 min)

```bash
cd ~/dev/projects/agencyDemo
```

**Turn off the background sweeper for this run.** It fires on a timer and will
interrupt you mid-walkthrough. You'll switch it on deliberately at the end.

```bash
# in .env, set:
AUTO_SWEEP=false
```

Then:

```bash
./demo reset            # ~0.5s  · 304 backdated events, flows back to v1
./demo sync-profiles    # ~7s    · the 29 members become Klaviyo profiles
./demo doctor           # confirm everything is live
```

**✓ check** — `doctor` should show:
```
Klaviyo    OK  Connected to Klaviyo account: amogh
LLM        openai  model=gpt-5.4-mini
events     304 in local store
proposals  {'total': 0, ...}
```

If Klaviyo says FAIL or LLM says `rules`, stop — a key is missing.

---

## Part 1 · The CLI, to see the machinery (3 min)

You won't use these in the video, but they show what's underneath.

```bash
./demo signals
```
**✓** 7 signals, with real evidence. Note `kind`, `scope` and `origin` on each.
These are **pure SQL** — no model involved. This is the raw material.

```bash
./demo flows
```
**✓** **6** automations, all **v1**, all the gym's — the other verticals are
filtered out by `DEMO_VERTICAL=fitness`. Read `Class No-Show Win-Back`: trigger,
`wait 3 days`, one email. Remember the 3 days.

**✓** Note that none of the six has `handles_signals` covering a member who
goes quiet. Six sensible flows and still a real gap.

```bash
./demo map door_access
```
**✓** Watch the badge: **`llm`**, because no config exists. The metric comes
back **`Gym Check-In`** from a payload that literally says `lock.unlock`, and
it lands on Kwame Osei, who is already in this account from Mindbody.

```bash
./demo connect-door
```
**✓** The audience goes **9 → 8** and it tells you who came out and why. One
model call, then 12 events in ~7ms for free.

```bash
./demo map tutoring
```
**✓** A payload from a tutoring company. Watch the badge: **`llm`**, because no
config exists. Then check the identity — it picked **Danielle Brooks, the
guardian**, not Ethan the student. Scroll to the field trace:
`guardian.email_address → profile.email`. Ends with `202 Accepted` and a
Klaviyo profile link.

```bash
./demo sources
```
**✓** Note the `Klaviyo connector` column: Mindbody, Toast and Bloomerang say
**native — parity, not novelty**. That's the honesty baked into the code.

---

## Part 2 · The UI (10 min)

```bash
./serve
```
Open **http://localhost:8000**. Also open your Klaviyo account in a second tab.

### 2a · The opening frame

**✓** Left rail says `Inbox · 0`. The right pane shows **"What it is watching
right now"** — the same 7 signals, severity-coded, with the line *"computed by
query, not by a model."*

Top-right pills: `Klaviyo live` · `model gpt-5.4-mini` · `spent $0.00` ·
`events 304`. Watch **spent** climb as you go.

### 2b · The gym's other tools

**Tab: `Event ingest`.** Read the line at the top — Ironline runs on Mindbody,
which Klaviyo integrates, *and three other tools that it does not*.

**Click `Kisi`** (the door-access system).

**✓** Left: the raw payload — `"type": "lock.unlock"`, a unix timestamp, and the
member buried under `actor.reference`. Right: a violet **⚡ Inferred by a model**
badge. The metric reads **`Gym Check-In`**, *not* "Lock Unlocked" — it
translated machine vocabulary into what a marketer would say.

**✓** The profile is **Kwame Osei**, who already exists in this account from
Mindbody. Click the Klaviyo link and confirm.

**Click `Trainerize`.** **✓** Value reads **$960**, and there's a warning
beneath: the payload said `price_paid_cents: 96000` and it caught the minor
units.

### 2b(ii) · Connect it properly — the payoff

Scroll down to **`Connect the door system for real`** and note the current
win-back audience is **9 people** (you'll see it again in 2e).

**Click `Connect Kisi →`.** ~3 seconds.

**✓** The panel replaces itself with:
```
inferred once    ~3000ms (model)
metric           Gym Check-In
saved as         mappings/kisi_door_access.yaml
then replayed    12 check-ins in 7ms
cost of those    $0.00 — config-driven, no model call
```
**✓** And a green banner:
```
Win-back audience went from 9 to 8.
Chidi Mbeki came out — looked silent to Mindbody, but has been
in the gym the whole time.
```

**This is the strongest moment in Part 1.** Two things just happened:

1. **The model was a one-time cost.** It inferred the mapping once, saved it as
   a config, and the next twelve events cost nothing and took 7ms. A gym with
   four tools pays for four inferences, ever.
2. **A wrong marketing decision was prevented.** Chidi looked lapsed for 33
   days because he stopped *booking classes*. He never stopped turning up. Any
   system relying on Mindbody alone would have emailed him "we miss you" while
   he was standing in the gym.

*Also try `physical_therapy` and `tutoring` further down if you want breadth —
different industries, same machinery.*

### 2c · The trigger, arriving unprompted

**Back to `Proposals`.** Confirm the dropdown reads **`Mindbody · Class
no-show`**.

**Click `Arm notification (20s)`.** The button starts counting down.

**Now go to the `Flows` tab and read `Class No-Show Win-Back` while you wait.**
This is the point — you should be doing something else when it interrupts you.

**✓** ~20–30s later a toast slides in bottom-right with a pulsing amber dot:
```
⚡ BUSINESS NOTIFICATION
Sam Whitfield has 3 class no-shows in 30 days
I looked at Class No-Show Win-Back and made a fix… does this look right?
[✓ Approve]  [Not right]  [See the change]
```

### 2d · Inspect, then approve

**Click `See the change`.** Read the panel top to bottom:

1. **What it noticed** — the signal plus the agent's restatement
2. **Context it pulled first** — chips: *20 events · $159 lifetime value · flow v1*
3. **What it audited** — names the specific steps
4. **The change it made** — the red/green diff
5. Footer: model, cost, latency, confidence

**✓** The diff shows real `[s1]`-style step ids and coloured +/- lines.

**Click `✓ Approve`.**

**✓** Banner turns green: `✓ Applied — flow is now v2`.

**Go to `Flows`.** **✓** `Class No-Show Win-Back` now reads **v2**, and the step
it changed is different. That's a real version bump.

### 2e · The recurring sweep, and all three proposal kinds

**Back to `Proposals` → click `Run sweep now`.** Takes ~10s (five model calls).

**✓** Four or five proposals appear, colour-coded in the left rail:
- 🔵 `patch` — a flow exists but mishandles something
- 🟣 `create` — **nothing** handled it, so it drafted a whole new automation
- 🟢 `campaign` — people are already in the bad state

**Open the 🟣 `create` one.** **✓** Its context chips say *"no flow covers
'lapsed_member'"*. The diff is **all green** — every line is an addition,
because nothing existed. Check the trigger line: it says
`segment = Members with no booking in 21 days`, **not** an event — because
going quiet fires no event.

**Open the 🟢 `campaign` one.** **✓** It shows:
- **8 people** (9 if you skipped 2b(ii)), with the counting rule spelled out
- Actual names: Jordan Avery, Rada Antonova, Chidi Mbeki…
- A complete email with `{{ first_name }}` merge tags

### 2f · Reject it — the important part

In the campaign proposal's feedback box, type **exactly**:

```
Our members are mostly older and hate texts. Never use SMS. And don't send anything before 9am.
```

**Click `✕ Reject with feedback`.** ~5s.

**✓** Four things should happen:
1. Tabs appear: `revision 1` / `revision 2 · after your feedback` — **click
   between them and compare**
2. The revised campaign's **timing moved to after 9am**
3. A violet banner: **"Learned from that — it won't need telling again"**
4. **Left rail** now lists the corrections:
```
· Never use SMS for this account.        (fitness)
· Do not send before 9am local time.     (all verticals)
```

**✓ The scope split is the detail worth checking.** You want SMS on `fitness`
and 9am on `all verticals`. It lands ~4 times in 5. If both say `all
verticals`, that's fine for a test run — just know it's the one beat to
re-check before recording.

### 2g · Prove it remembers  ← the payoff

**First approve revision 2** (it's good now). That matters: while a campaign
proposal is `pending` or `revised`, duplicates are suppressed — approving lifts
the block so a fresh one can be created.

**Then click `Run sweep now`** and open the new campaign proposal.

**✓** Four things to check:
1. A violet **`2 LEARNED CORRECTIONS APPLIED`** badge at the top
2. `channel` reads **`email · tomorrow 9am local time`** — not `now`
3. The copy has **no visit-count line**
4. The left-rail corrections now read **`used 1×`**

**✓** And in its own audit text, something like: *"a gentle win-back note at 9am
local time matches the account's scheduling constraint without mentioning visit
counts."* It is citing your correction as the reason for its choice.

Same signal, same eight people, nothing said to it — and the output differs in
exactly the two ways you corrected. That is the controlled experiment, and it is
the most convincing thing in the demo.

> **Note:** sweeping *without* approving first produces nothing, and that is
> correct — proposals already exist for those signals. An earlier version of
> this doc told you to just sweep again; that was wrong.

### 2h · Approve the campaign — it becomes real

**Go back to the campaign proposal** (now `revised`) and **click
`✓ Approve & send`**.

**✓** Green banner:
```
✓ Created in Klaviyo as a Draft campaign — 9 profiles added
  open it in Klaviyo →
  Nothing was sent. A human presses send.
```

**Click the link.** **✓** In your Klaviyo tab: a real campaign, status
**Draft**, with a real audience list. Nothing sent.

### 2i · Both patterns, side by side

**`Activity` tab.**

**✓** A table with a **"what it could see"** column:

| pattern | scope |
|---|---|
| ⚡ trigger | one profile |
| 🕐 sweep | all profiles + windows |

That column is the whole argument for building both.

### 2j · The contrast

**`Chat · today` tab.** Click the first suggested prompt.

**✓** It generates a good campaign — then read its own admission in the amber
box: *"I could not verify your actual attendance thresholds, member status
rules, or audience size."*

**✓** Beneath it, a side-by-side: **its guess** (a percentage range) vs **the
agent** (9 people, counted, named).

---

## Part 3 · The background job (2 min)

Now switch it on:

```bash
# in .env
AUTO_SWEEP=true
SWEEP_INTERVAL_SECONDS=60
```

Restart `./serve`, then leave it alone for two minutes and go do something else.

**✓** Come back to the `Activity` tab. Rows have appeared **that you didn't
trigger**:
```
23:26:35  sweep  signals=7  created=0   34ms
23:27:35  sweep  signals=7  created=0   18ms
```

Those `created=0` / `~30ms` rows are the point: it's genuinely running, and it
**costs nothing when there's nothing new**, because proposals dedupe. That's a
real production property.

---

## Reset between runs

```bash
./demo reset && ./demo sync-profiles
```

This clears proposals, corrections and flow versions, and reseeds. It does
**not** delete anything from Klaviyo — profiles, lists and draft campaigns stay
until you delete them there.

## If something looks wrong

| symptom | cause / fix |
|---|---|
| inbox already full on open | background sweeper ran — set `AUTO_SWEEP=false` |
| `Run sweep now` creates nothing | proposals already open for those signals; `./demo reset` |
| toast never arrives | check the `Fire event` dropdown is on `Mindbody · Class no-show` |
| a proposal says `deterministic fallback` | OpenAI call failed; it degraded on purpose — check `doctor` |
| campaign approval errors | you may be near the 250-profile ceiling; check `doctor` |

## Also worth checking once

- **Click `Connect Kisi` twice.** The check-in count should stay at 12 and the
  event counter shouldn't move — identical events are deduplicated, the same way
  Klaviyo deduplicates on (profile, metric, unique_id).
- **Reject a proposal, then reset with it open.** The detail pane should clear
  itself rather than showing a ghost.
- **Watch the `spent` pill.** It should climb by roughly $0.0035 per proposal.

## What you've now demonstrated

- events from an unintegrated tool → real Klaviyo, model **or** offline inference
- 7 problems found by query, no model
- a notification that interrupts you, unprompted
- all three actions: fix a flow · write a new flow · draft a campaign
- approve → a real version bump, and a real Klaviyo draft campaign
- reject in plain English → revised, and two rules learned with correct scopes
- the next proposal honouring those rules unprompted
- both execution patterns, and why neither can do the other's job
- the reactive path, admitting what it can't know
