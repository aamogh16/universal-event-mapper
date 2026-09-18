# Demo script

One business throughout: **Ironline Strength Co.**, a gym on Mindbody.
Target runtime **5–6 minutes**. Slides for the first minute, screen for the rest.

> **Narrate the shape, not the wording.** Every audit and every piece of copy is
> generated live, so exact sentences differ on each run. Say "it found the delay
> problem", never "it will say X".

---

## Before you hit record

```bash
./demo reset            # 304 backdated events, all flows back to v1
./demo sync-profiles    # the 29 members become Klaviyo profiles
./demo doctor           # confirm: Klaviyo live, provider openai
./serve                 # http://localhost:8000
```

Open two browser tabs: the dashboard, and your Klaviyo account.
Leave the dashboard on the **Proposals** tab — it opens showing the 7 signals
it is already watching, which is the right first frame.

---

## 0:00 – 1:00 · Slides

**Who you are.** One line. Garret knows you; this is not a resume.

**The conversation with Elias.** Be specific, use his words:

- More event types means more addressable business for Klaviyo
- He wants Composer to go from reactive to proactive — **"business
  notifications, not push notifications"**
- And an agent that **learns from its mistakes**

**What you built.** "So I built that, against your real API."

> Do NOT say Klaviyo can't read gym or restaurant data. It can — Mindbody,
> Toast and Bloomerang all have connectors, and Mindbody is a named product
> line. The accurate version: every new tool needs a hand-built integration,
> so reaching new verticals is bottlenecked by engineering time per
> integration.

---

## 1:00 – 1:30 · An event from a tool nobody integrated

**Tab: Event ingest → click `veterinary`.**

- Left: the raw payload from a vet clinic's booking system
- Right: **⚡ Inferred by a model** — no config existed
- The field trace: `patient.owner.contact_email → profile.email`
- `202 Accepted`, and a link into the real profile

**Say:** "No integration exists for this tool and never will. It worked out
that the marketable person is the pet's *owner*, not the pet — and that's a
real profile in Klaviyo now."

Then move on. This is the supporting act.

---

## 1:30 – 2:15 · It interrupts you · TRIGGER

**Click `Arm notification (20s)`.** Then keep talking — switch to the Flows
tab and walk through `Class No-Show Win-Back` while you wait:

> "This is the gym's existing automation. Someone misses a class, it waits
> three days, then sends one email. That's what a human set up."

**The toast slides in mid-sentence.** Stop and read it:

```
● ⚡ BUSINESS NOTIFICATION
Sam Whitfield has 3 class no-shows in 30 days
I looked at Class No-Show Win-Back and made a fix. …does this look right?
[✓ Approve]  [Not right]  [See the change]
```

**Say:** "Nobody asked it anything. An event landed, it recognised a pattern in
that member's history, and it interrupted me. That's the business notification."

---

## 2:15 – 3:00 · What it actually did · APPROVE

**Click `See the change`.** Walk the panel top to bottom:

1. **What it noticed** — the signal, with real numbers
2. **Context it pulled first** — "20 events for this profile · $159 lifetime
   value · flow v1" → *"it read the history before it formed an opinion"*
3. **What it audited** — names the specific steps it examined
4. **The change it made** — the red/green diff

**Say, pointing at the diff:** "This isn't a description of a fix. That diff
was produced by *applying* the change to the flow. What I approve is exactly
what happens."

**Click Approve.** Banner: `✓ Applied — flow is now v2`.

**Flows tab** → it now reads **v2**. *"That's a real version bump."*

---

## 3:00 – 3:45 · The recurring job · SWEEP

**Say:** "That was the trigger pattern — one event, one member, instantly. The
other pattern is a recurring job. In production it runs daily; I've set it to
60 seconds so you can see it."

**Click `Run sweep now`.** Takes ~10 seconds — talk through it:

> "It's making five model calls, one per finding."

Four or five proposals appear. Point out the three **kinds**:

| | |
|---|---|
| `patch` | a flow exists but mishandles it |
| `create` | **nothing** handles it, so it drafted a whole new automation |
| `campaign` | people are *already* in the bad state |

**Open the `create` one:** *"There was no automation for members who just stop
coming. The only flow triggers on a no-show, and these members didn't
no-show — they just stopped. So it wrote one."*

**Say the key distinction:** "And a new automation only catches people from
*now on*. It does nothing for the nine who already left — which is why the
third one exists."

---

## 3:45 – 5:00 · Told no, and it learns · THE PEAK

**Open the `campaign` proposal.** Note what's grounded:

- **9 people** — counted by query, with the rule shown, and named
- A complete email, ready to send

**Say:** "It wrote the message and it knows exactly who gets it. Nine people,
and it can name them."

**Now reject it.** Type, in the box:

> `Our members are mostly older and hate texts. Never use SMS. And don't send anything before 9am.`

**Click Reject with feedback.** ~5 seconds.

Then show, in order:

1. **`revision 1` / `revision 2 · after your feedback`** tabs — click between them
2. The revised campaign: **timing moved to after 9am**, copy rewritten
3. **Left rail — what it learned:**

```
· Never use SMS for this audience; send email only.   (fitness)
· Do not send before 9am local time.                  (all verticals)
```

**Say:** "Two sentences of feedback became two rules. And notice it scoped
them differently — the SMS rule is specific to this gym, the 9am rule it
decided applies to any business."

> **Check this one before you keep the take.** Scoping is a model judgment and
> it lands ~4 times in 5. You want the SMS rule on `fitness` and the 9am rule
> on `all verticals`. If both come back `all verticals`, reject again on a
> fresh `reset` — it is the one beat worth re-shooting for. If you would rather
> not depend on it, narrate the shape instead: "two sentences became two
> separate rules, each with its own scope" is true every time.

**Then the proof.** Click `Run sweep now` again, open any new proposal, and
point at the context chips:

```
· 2 learned corrections applied
```

**Say:** "Nobody reminded it. Every proposal from here on is generated with
those constraints, and it will never suggest SMS to this gym again. That's the
part Elias described as learning from mistakes."

---

## 5:00 – 5:30 · It's real, and both patterns

**Approve the revised campaign.** Banner:

```
✓ Created in Klaviyo as a Draft campaign — 9 profiles added
  open it in Klaviyo →
  Nothing was sent. A human presses send.
```

**Switch to your Klaviyo tab.** The campaign is there, in Draft, with those
nine members as its audience.

**Say:** "That's a real campaign in a real Klaviyo account. It hasn't sent
anything — approving drafts it, a human sends it. That's deliberate."

**Activity tab.** Point at the *what it could see* column:

| pattern | scope |
|---|---|
| ⚡ trigger | one profile |
| 🕐 sweep | all profiles + windows |

**Say:** "Both patterns, and they're different because they see different
things. A trigger knows Sam missed three classes. Only the sweep could know
the 5:30pm class got worse across ten people."

---

## 5:30 – 6:00 · Close

**Chat · today tab.** Type the suggested prompt. When it answers, point at its
own admission:

> *"I could not verify your actual attendance thresholds, member status rules,
> or audience size."*

And the comparison beneath: **guessed a percentage** vs **9 people, counted,
named**.

**Say:** "This is how Composer works today, and the writing is good. But to
get value out of it I have to already know the 5:30pm class has a problem.
Knowing that is the agent's job."

**Then end forward-looking:**

> "If this were real, next would be running the sweep across every account
> rather than one, and writing to real Klaviyo flow definitions instead of my
> simplified ones. But the loop works: it notices, it drafts, you correct it
> once, and it remembers."

---

## Be honest about these on camera

Each one costs you nothing and buys credibility:

- **"The flows are simplified stand-ins"** — real Klaviyo flow definitions are
  much richer. Say it before anyone wonders.
- **"Approving drafts the campaign; it doesn't send"** — deliberate.
- **"Four verticals are seeded, I'm only showing the gym."**

## If something misbehaves

| problem | do this |
|---|---|
| sweep produces nothing | proposals already exist — `./demo reset` first |
| a proposal looks thin | open a different one, or sweep again; output varies |
| model is slow or erroring | keep narrating; it falls back to the offline auditor and labels itself |
| toast doesn't arrive | `Run sweep now` produces the same proposals |
| you need a clean slate mid-take | `./demo reset && ./demo sync-profiles` |

## Numbers you can quote

- **3.0×** — the 5:30pm no-show rate vs the prior 14 days (14/54 vs 4/46)
- **9** — quiet members, counted not estimated
- **~$0.0035** — cost per proposal
- **~$350/day** — a daily proposal for 100,000 businesses
