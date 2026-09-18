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
**✓** 4 automations, all **v1**. Read `Class No-Show Win-Back` — trigger, then
`wait 3 days`, then one email. Remember the 3 days.

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

### 2b · An event from a tool nobody integrated

**Tab: `Event ingest` → click `veterinary`.**

**✓** Left: the raw vet-clinic payload. Right: a violet **⚡ Inferred by a
model** badge with tokens and latency. Field-by-field table shows
`patient.owner.contact_email → profile.email`. Green banner with a
**Klaviyo profile link — click it**, and confirm Leah Fitzgerald exists in your
account.

Also click `physical_therapy` — that one pulls the patient out of a FHIR-style
`telecom` array.

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
- **9 people**, with the counting rule spelled out
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

### 2g · Prove it remembers

**Click `Run sweep now` again.** Open any newly created proposal.

**✓** Its context chips now include a violet **`2 learned corrections
applied`**. Nobody reminded it. Every proposal from here on is generated under
those constraints.

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
