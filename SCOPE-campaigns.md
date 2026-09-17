# Scope: making campaign approval create a real Klaviyo campaign

Today, approving a campaign proposal records it locally and the UI says so.
This is what it would take to make it real, and why the plan deliberately
stops one step short of sending.

---

## The hard constraint: the free plan auto-upgrades

> "Klaviyo's billing system automatically upgrades your account when you
> exceed limits. Cross 250 contacts or try sending email #501? You're suddenly
> on a paid plan. **No grace period.**"

Two limits, two very different risks:

| limit | where we are | risk |
|---|---|---|
| 250 profiles | ~15 created so far | low — but every `map`/`send` adds one |
| 500 emails/month | **0 sent** | **this is the one that bills you** |

**Therefore: never call `send-jobs`.** That is the only endpoint that
transmits email, and it is the only thing standing between this demo and a
surprise invoice.

## The good news

Campaigns created via the API land in **draft** and stay there:

> "Once the campaign has been created, it will be in 'draft' status until it
> is scheduled to be sent."

So we can create a **real campaign, visible in the real Klaviyo UI, with real
audience and real copy, that sends nothing.** That is not a compromise — it is
a better demo. "The agent drafted it, you approved it, and it's sitting in
Klaviyo waiting for a human" *is* the human-in-the-loop story, and Draft is
Klaviyo's own default for API-created objects.

---

## What it takes: four calls, in order

### 1. A list to target
`POST /api/lists` — campaigns need `audiences.included` to be list or segment
IDs; we can't pass raw emails.

```json
{"data":{"type":"list","attributes":{"name":"Quiet members · 21+ days"}}}
```
Then add our resolved audience via the list-profiles relationship endpoint.

*Rate limit worth knowing: **150 lists/day**. Fine for a demo, but reuse one
list per audience rather than creating a new one per approval.*

### 2. A template for the body
`POST /api/templates` with `editor_type: "html"`, plus `name`, `html`, `text`.
Our drafted campaign body is plain text, so it needs wrapping in minimal HTML.
Limit is 1,000 templates per account.

### 3. The campaign
`POST /api/campaigns` with the audience, a send strategy, and a
`campaign-messages` object carrying channel, label, subject and from address.
Lands in **draft**.

### 4. Attach the template
`POST /api/campaign-messages/{id}/assign-template`
Email campaigns cannot be scheduled without this — which is fine, because we
never schedule.

### 5. ~~Send~~ — deliberately not implemented
`POST /api/campaigns/{id}/send-jobs` is where email would actually go out.
**We do not call it.** The UI should link to the draft in Klaviyo instead and
say plainly that a human presses send.

---

## Work involved

| | |
|---|---|
| `klaviyo.py` — four new methods | ~120 lines |
| Text → minimal HTML wrapper | ~25 lines |
| List reuse / lookup by name | ~30 lines |
| Wire into `proposals.approve()` for `kind == "campaign"` | ~30 lines |
| UI: link to the draft, state that nothing sent | ~20 lines |
| **Total** | **~225 lines, roughly half a day** |

## Risks, honestly

- **Profile ceiling.** Adding our 9 quiet members to a list doesn't create
  profiles (they exist), but repeated `map`/`send` demos do. At ~15 of 250
  there's room, but `./demo reset` does not delete Klaviyo profiles — only
  local ones. Worth a `--purge` that deletes test profiles if this gets used
  heavily.
- **A verified sender address is required** for a campaign's `from_email`.
  Klaviyo may require domain verification before a campaign is valid, which
  could block step 3 on a free account. **This is the most likely blocker and
  should be tested first, before building anything else.**
- **Four sequential calls** means four ways to half-succeed. Needs cleanup on
  partial failure, or you leave orphaned lists and templates behind.

## Recommendation

**Do it, but test step 3 first.** Spend fifteen minutes confirming a campaign
can be created at all on a free account with an unverified sender. If it can,
the rest is mechanical. If it can't, stop — the simulated send with an honest
label costs the demo very little, and "I didn't wire up sending because it
would have auto-upgraded the account" is a perfectly good answer to give a
Klaviyo engineer.

Either way: **do not implement `send-jobs`.** There is no version of this demo
that is improved by actually emailing 9 fictional people.
