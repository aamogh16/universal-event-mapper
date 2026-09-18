# Demo script

**Target: 6 minutes.** One business throughout — Ironline Strength Co., a gym
running on Mindbody.

Lines in `>` are things to actually say. Adapt the wording; keep the substance.

> **Narrate shapes, not exact strings.** Every audit and every piece of copy is
> generated live, so sentences differ each run. "It found the timing problem" is
> safe. "It will say X" will make you look wrong.

---

## Before you record

```bash
./demo reset            # 304 backdated events, flows to v1, nothing learned
./demo sync-profiles    # the 29 members become Klaviyo profiles (once)
./demo doctor           # Klaviyo OK · provider openai · background job off
./serve                 # http://localhost:8000
```

Two browser tabs: the dashboard, and your Klaviyo account.
Land on **Proposals**. Hard-refresh. Confirm the header reads `Klaviyo live`.

---

# Part 1 · The conversation  (0:00 – 1:15)

No slides needed beyond a title card if you want one. Just talk.

> "I'm Amogh, I'm a CS student at Northeastern. A few weeks ago I had a
> conversation with Elias Torres, who's CPO at Klaviyo, about where he wants
> Composer to go. Two things he said stuck with me.
>
> The first was about events. More event types flowing in means more
> addressable business for Klaviyo — the richer the picture of a customer, the
> more there is to market against.
>
> The second was about Composer itself. He wants it to stop being reactive.
> His phrase was **business notifications, not push notifications** — the agent
> notices something worth acting on and comes to you, rather than waiting for a
> prompt. And he mentioned wanting it to learn from its mistakes.
>
> So I built that, against Klaviyo's real API."

**One thing to be careful about — say this, it protects you:**

> "To be clear about the integration side: Klaviyo already integrates Mindbody,
> Toast, Bloomerang. There's a whole Klaviyo for Wellness line. The problem
> isn't that Klaviyo can't read a gym's data. It's that **every new tool is a
> hand-built connector**, so reaching new verticals is bottlenecked by
> engineering time per integration. That's the gap I went after."

---

# Part 2 · How it's built  (1:15 – 1:50)

Keep this short. Two layers and one principle.

> "Two layers. `universal_events` gets events in from any tool. `composer` is
> the agent that acts on them.
>
> And one rule I held to throughout: **deterministic where it's arithmetic,
> model-driven where it's judgment.** Detecting that a no-show rate tripled is a
> SQL query — I don't want a model doing that, it'd be slower and it could
> hallucinate the number. Deciding what to *do* about it is the model's job.
>
> Same idea in how the model returns things. When it edits a flow it doesn't
> hand back rewritten JSON — it returns a short list of edit operations, which
> I apply in code. So it can't silently drop a step, and the diff you're about
> to see is produced by *actually applying* the change, not by the model
> describing its own work."

---

# Part 3 · The product  (1:50 – 5:40)

### 3a · What it's already watching  (20s)

You're on **Proposals** with an empty inbox. The right pane lists the signals.

> "Nothing's been proposed yet. This is what it's watching — four things it
> found on its own. The 5:30pm class no-show rate is three times what it was
> two weeks ago. Fourteen no-shows out of fifty-four bookings, versus four out
> of forty-six. These are queries, not a model — it cites these numbers rather
> than inventing them."

### 3b · A tool nobody integrated  (60s)

**Tab: Event ingest.** Click **`Kisi`**.

> "Ironline runs on Mindbody, which Klaviyo integrates well. It also runs on a
> door-access system, a personal training platform, and a body scanner — and
> none of those have a connector."

Point at the raw payload, then the badge.

> "That payload says `lock.unlock`. It worked out that a door opening means the
> member **checked in** — that's the metric name, not the tool's event code.
> And it landed on Kwame, who's already in this account from Mindbody."

Scroll to **`Connect the door system for real`**. Point at the audience box.

> "Before I connect it — the win-back audience is **nine members**. Chidi Mbeki
> is in that list."

Click **`Connect Kisi →`**. ~3s.

> "One model call to infer the mapping. It saved that as a config file, then
> replayed three weeks of check-ins in seven milliseconds for nothing — because
> the config does the work from then on. **The model is a one-time onboarding
> cost, not a per-event cost.**
>
> And look — nine became eight. Chidi came out. He looked silent for
> thirty-three days because he stopped *booking classes*. He never stopped
> turning up. Every system built on Mindbody alone would have emailed him
> 'we miss you' while he was standing in the gym."

*Optional, if asked how it joins them: "On email. Both tools know the member's
email, and Klaviyo's own identity resolution merges them. Where it gets hard is
a tool that only knows a badge number — I didn't solve that."*

### 3c · It interrupts you  (75s)

Back to **Proposals**. Click **`Arm notification (20s)`**.

**Now go to the Flows tab and talk while you wait** — this matters, the point is
that it interrupts you.

> "Six flows here, all sensible ones a gym would build. Welcome series, first
> class, a milestone, no-show win-back, renewal, payment failure. This is the
> one that matters — someone misses a class, it waits three days, sends one
> email."

**The toast slides in.** Stop and read it.

> "Nobody asked it anything. An event landed, it recognised a pattern in that
> member's history, and it interrupted me. That's the business notification."

Click **`See the change`**. Walk down the panel.

> "What it noticed. Then the context it pulled *before* forming an opinion —
> twenty events, lifetime value, which flow it's looking at. Then the audit,
> naming the specific steps. And the change."

Point at the diff.

> "That diff was produced by applying the patch to the flow. It isn't a
> description of a fix — it's the fix."

Click **`✓ Approve`** → **Flows tab**.

> "Version two. That's a real change."

### 3d · The recurring job  (45s)

Back to **Proposals** → **`Run sweep now`**. ~10s.

> "That was the trigger — one event, one member, instant. The other pattern is
> a recurring job. In production it's daily; I've set it to sixty seconds."

Point at the three colours in the inbox.

> "Three different kinds of proposal. Blue is a fix to an existing flow. Purple
> is 'nothing handles this, so I wrote a new automation' — six flows and not
> one of them covers a member who just stops coming, because going quiet fires
> no event. And green is a campaign."

Open the 🟣 **create** one briefly, then the 🟢 **campaign**.

> "And a campaign exists because a new flow only catches people from now on. It
> does nothing for the eight who already left. It knows exactly who they are —
> counted by query, and it can name them."

### 3e · Told no  (60s)

In the campaign's feedback box, type:

```
Don't send these the moment you write them — schedule for 9am local. And don't tell members how many visits they've had, it reads like surveillance.
```

Click **`✕ Reject with feedback`**. ~5s.

> "Two objections, both real — it was set to send immediately, and the copy
> brought up their visit history."

Click between **`revision 1`** and **`revision 2 · after your feedback`**.

> "Revision two. Scheduled for 9am. The visit-count line is gone. And it pulled
> two rules out of two sentences —"

Point at the left rail.

> "— and scoped them differently. 'Schedule for 9am' it decided applies to any
> business. 'Don't cite visit counts' it kept specific to this gym. I didn't
> tell it which was which."

### 3f · It remembers  ← THE PEAK  (45s)

Click **`✓ Approve`** on revision 2. Then **`Run sweep now`**.

Open the new campaign proposal.

> "Same problem. Same eight members. And I've told it nothing."

Point at the badge, then the channel row, then the audit.

> "Two learned corrections applied. Scheduled for 9am — I didn't ask. No visit
> counts. And read its own reasoning: *'a gentle win-back note at 9am local
> time matches the account's scheduling constraint without mentioning visit
> counts.'* It's citing my correction as the reason for its choice.
>
> I corrected it once. It didn't need telling twice."

*Check the left rail counters read `used 1×`.*

### 3g · It's real  (30s)

Click **`✓ Approve & send`** on that campaign.

> "Approving creates it in Klaviyo for real."

Switch to your **Klaviyo tab**, open the campaign.

> "There it is. Draft status, with those eight members as the audience. Nothing
> was sent — approving drafts it, a human presses send. That's deliberate:
> the only endpoint that actually transmits email is the one I didn't
> implement."

### 3h · Both patterns  (20s)

**Activity tab.** Point at the *what it could see* column.

> "Two patterns, and they're not just different schedules. A trigger reacts to
> something arriving and sees one profile. A sweep sees everyone across time
> windows — which is the only way to find someone who's gone quiet, because
> nothing fires when a member loses interest. Neither can do the other's job."

### 3i · The contrast  (30s)

**Chat · today tab.** Click the first suggested prompt.

> "This is how Composer works now. Describe a campaign, get a good one — the
> writing is genuinely fine."

Point at the amber box, then the comparison.

> "But read what it admits: it couldn't verify the attendance thresholds or the
> audience size. It guessed a percentage. The agent counted eight people and
> named them.
>
> And the real difference — to get anything out of this tab, I have to already
> know the 5:30pm class has a problem. **Knowing that is the agent's job.**"

---

# Part 4 · Close  (5:40 – 6:00)

Say the honest things. They cost nothing and buy credibility.

> "Two things I'd flag. The flows here are simplified stand-ins — a real Klaviyo
> flow definition is a graph of linked actions with separate entry filters. I
> kept mine as an ordered list so the reasoning and the diff stay readable.
> And approving a campaign drafts it rather than sending it, on purpose.
>
> If this were real, next would be running the sweep across every account
> instead of one, and writing to real flow definitions instead of mine. But the
> loop works: it notices on its own, it drafts something specific, you correct
> it once, and it remembers."

---

## Numbers safe to quote — these are computed, not generated

- **3.0×** — the 5:30pm no-show rate vs the prior 14 days (14/54 vs 4/46)
- **9 → 8** — the win-back audience after connecting the door system
- **12 check-ins in 7ms, $0.00** — the replay after one inference
- **~$0.0035** — cost per proposal
- **~$350/day** — a daily proposal for 100,000 businesses

## If something misbehaves

| symptom | do |
|---|---|
| inbox full on open | background sweeper ran — set `AUTO_SWEEP=false` |
| sweep creates nothing | proposals already open for those signals; `./demo reset` |
| yellow `deterministic fallback` line | the model call failed and it degraded — re-take |
| toast never arrives | check the dropdown is `Mindbody · Class no-show` |
| a proposal reads thin | open another, or re-arm; output varies per run |
| need a clean slate | `./demo reset && ./demo sync-profiles` |

## Optional cuts if you're over time

1. **3i, the Chat tab** (30s) — strongest thing to keep if you can
2. **3b's other tools** — just do Kisi, skip Trainerize
3. **3d's create proposal** — mention it, don't open it
4. Never cut **3e or 3f**. That pair is the demo.
