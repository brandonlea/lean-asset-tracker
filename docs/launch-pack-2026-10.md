# Launch pack — Lean Asset Tracker v0.6.1

Written 2026-10-27 for Brandon to post himself. Nothing here has been published.

Target: **10 people running it within the first month.**

---

## 1. The rules, and what's actually verified

### Hacker News — VERIFIED, fetched from `news.ycombinator.com/showhn.html`

These are quoted, not paraphrased, because three of them change what we do:

- *"Show HN is for something you've made that other people can play with."*
- *"Please make it easy for users to try your thing out, ideally without barriers such as signups or emails. You'll get more feedback that way."*
- **"Don't post landing pages or fundraisers."**
- *"The project should be non-trivial... Share something that is deeply personal and interesting to you. Explain how and why."*
- *"The project must be something you've worked on personally and which you're around to discuss."*
- *"Please don't ask friends to upvote or comment. That's not ok on HN."*
- *"New features and upgrades ("Foo 1.3.1 is out") generally aren't substantive enough."* — a first public release is fine.

**Consequences for us:**

1. **Do NOT submit `hallveckdigital.co.uk`.** It is a landing page with pricing on it. That rule is explicit and it will get flagged. Submit the **GitHub repo**.
2. Our demo needs **no login** — I checked, there is no password field on it. That satisfies the "no barriers" line better than most Show HNs manage, so it goes in the first line of your first comment.
3. You must be **around to answer comments**. See §5; this matters more than the copy.

### Reddit — NOT VERIFIED, and I won't pretend otherwise

r/selfhosted and r/homelab both block this server's IP, and the Internet Archive was down when I tried the fallback. I could not read either subreddit's rules.

So I have **not** written to a ruleset I can't see, and I have **not** repeated the "9-to-1 self-promotion ratio" thing — that's folklore that SEO blogs invent, and I'm not citing it.

Instead the drafts below are deliberately conservative, so they should survive almost any ruleset:

- authorship disclosed in the first sentence
- the free AGPL self-hosted version is the subject; **no pricing anywhere in the post body**
- links go to GitHub and the demo, **never to the sales site**
- competitors credited honestly
- written as "here's a thing and why", not as an advert

**Before you post: open each subreddit, read the sidebar rules and check for a self-promotion day or a required flair.** Two minutes. If either requires contacting mods first, do that instead — a removed post plus a ban costs you the whole channel, and this audience is small enough that you only get one reputation.

---

## 2. Show HN (post this one first)

**Submit URL:** `https://github.com/brandonlea/lean-asset-tracker`

**Title** (80 char limit; no superlatives, no "revolutionary", states what it is):

```
Show HN: Lean Asset Tracker – self-hosted inventory for homelabs, with QR labels
```

Alternatives if you prefer:
```
Show HN: Self-hosted asset tracker for homelabs – scan a QR label, see the history
Show HN: I built a lightweight asset tracker for my homelab because Snipe-IT was too much
```

**First comment — post this immediately after submitting:**

```
Live demo, no signup: https://demo.hallveckdigital.co.uk

I look after a small rack and a cupboard of spare kit, and I could never answer
two questions quickly: where is that switch, and when did we last touch it.
Snipe-IT and NetBox both answer them properly, but they're built for an org with
an IT department and they were more than I wanted to run and maintain. A
spreadsheet was the other extreme — it doesn't scan, doesn't know whether a
server is up, and doesn't remember who changed what.

So this sits in between. Stick a QR label on a device, scan it with your phone,
and you get its location, its full history, any maintenance window, and whether
it's currently alive.

What's in it:
- QR labels you can print on normal sheet labels
- Hierarchical locations (Home > Office > Rack A > Shelf 2)
- An append-only history log, so every move and change is attributable
- Maintenance windows with timezone handling and clash detection
- An optional read-only agent you drop on a box for live CPU/RAM/disk/uptime
- CSV import that tries to map messy spreadsheet headers for you

Stack is FastAPI + Postgres + HTMX, which is also why it's light — no build
step, no SPA, pages render server-side. Install is a docker compose up.

It's AGPL-3.0 and the self-hosted version is complete and free — no feature
gating, no user cap, no telemetry, nothing phones home. I fund it with optional
cloud hosting for people who don't want to run it themselves, but that's
genuinely the same software.

Honest about the state of it: it's v0.6.1, I'm the only developer, and it has
no users yet, so I'd value being told what's wrong with it. Alerting is
half-built. Known rough edges are in the README roadmap.

Happy to answer anything.
```

**Why this is shaped the way it is:** it opens with the thing you can try (HN's explicit ask), the "why" is a real problem rather than a market gap, it credits the incumbents instead of trashing them, it states the licence and the no-telemetry position up front because that is the trust test for this audience, it mentions money once in a way that explains funding rather than selling, and it volunteers the project's weaknesses. On HN, pre-empting the criticism is what buys you a civil thread.

**When:** Tuesday–Thursday, roughly 13:00–15:00 UK (morning US Eastern). Avoid Friday and the weekend.

---

## 3. r/selfhosted

**Check the sidebar first.** Flair as a project/self-promo post if those exist.

**Title:**
```
I built a lightweight self-hosted asset tracker for homelabs — QR labels, full history, AGPL
```

**Body:**
```
I'm the developer, so flagging that up front.

I could never quickly answer "where is that switch and when did we last touch
it" for my own rack. Snipe-IT and NetBox do this properly but are aimed at
organisations with an IT department, and they were more than I wanted to run.
Spreadsheets don't scan and don't know if a box is up.

Lean Asset Tracker is the middle: docker compose up, then stick a printed QR
label on a device and scan it with your phone to see where it lives, what
changed, and whether it's alive.

- QR labels on standard sheet labels
- Nested locations (Home > Office > Rack A > Shelf 2)
- Append-only history — every move is attributable
- Maintenance windows with timezones and clash detection
- Optional read-only agent for live CPU/RAM/disk/uptime
- CSV import that maps messy headers for you

FastAPI + Postgres + HTMX, server-rendered, no build step, no SPA.

AGPL-3.0. The self-hosted version is the whole thing — no feature gating, no
user limit, no telemetry, nothing phones home. I keep it going with optional
paid hosting for people who'd rather not self-host, and that's the same code.

Demo (no signup): https://demo.hallveckdigital.co.uk
Source: https://github.com/brandonlea/lean-asset-tracker

It's v0.6.1 and has no users yet, so I'm after honest criticism more than
anything. Alerting is half-finished. What would stop you using this?
```

**Why:** discloses authorship immediately, the no-telemetry/AGPL/no-gating triplet is the thing this subreddit actually checks for, the funding sentence is honest rather than hidden, and it ends with a question — which is what turns a post into a thread instead of a billboard.

---

## 4. r/homelab

r/homelab skews more hardware-focused and is generally stricter about anything commercial, so this draft is shorter, mentions no money at all, and leads with the physical-labels angle.

**Read the sidebar before posting. If it prohibits self-promotion outright, skip it** and come back when you have users who will talk about it for you.

**Title:**
```
Printed QR labels for every device in my rack, so I can scan one and see its whole history
```

**Body:**
```
Developer here, so take it with that in mind.

The thing I actually wanted was a label on each box that I could scan with my
phone and immediately get: what is this, where does it live, what's been done
to it, and is it up right now. I was tracking it in a spreadsheet and kept
finding kit I'd forgotten I owned.

So I wrote a small self-hosted thing that generates printable QR labels and
keeps an append-only history per device. Nested locations down to the shelf,
maintenance windows with clash detection, and an optional read-only agent you
drop on a box for live CPU/RAM/disk/uptime.

FastAPI + Postgres + HTMX. Install is docker compose up. AGPL-3.0, no
telemetry, nothing phones home.

Demo, no signup: https://demo.hallveckdigital.co.uk
Source: https://github.com/brandonlea/lean-asset-tracker

Mostly posting because I want to know what I've missed — what do you track per
device that I haven't thought of?
```

---

## 5. What actually gets you to 10 — the honest arithmetic

The copy is maybe 20% of the outcome. The numbers that matter:

| Stage | Realistic range |
|---|---|
| One r/selfhosted post that gets traction | ~500–1,500 clicks |
| A Show HN that clears the threshold onto /show | ~200–2,000 clicks |
| Click → actually installs a self-hosted tool | **1–3%** |

So **one** post landing well ≈ 5–30 installs. Ten is around the median of a single decent post — so the target is realistic, but it depends on at least one of three posts landing, and most posts don't. That's why we post three, staggered, rather than betting everything on one.

**The single biggest lever is you being present in the comments for the first 2–3 hours.** On both HN and Reddit, early reply velocity drives ranking, and for an unknown solo project the author answering every question is the entire credibility story. A post you fire and walk away from will do a fraction of the numbers. **Don't post if you can't sit with it for a couple of hours.**

Suggested stagger, so one flop doesn't end the launch:

- **Day 1 (Tue–Thu)** — Show HN, 13:00–15:00 UK. Sit with it.
- **Day 3–4** — r/selfhosted, weekday morning UK. Sit with it.
- **Day 7+** — r/homelab, only if the first two went civilly and its rules allow it.

Do not post the same day in multiple places. You cannot be present in two threads at once, and cross-posting simultaneously reads as a campaign.

### What to expect, so it doesn't knock you
- Blunt criticism is engagement, not failure. "Why not just use Snipe-IT" will come up — answer it plainly, don't get defensive, and don't argue with anyone.
- Expect a licence question (AGPL and the cloud version). The true answer is simple: same code, hosting is the paid convenience.
- Expect someone to ask about auth, backups and multi-user. All three exist; point at the README.
- A post getting ~20 upvotes and 8 comments is not a failure if two of those people install it.

### Never do these
- **Don't ask anyone to upvote.** HN says so explicitly and Reddit will shadow-remove you for it. It's also the fastest way to lose the only reputation you have.
- Don't post the pricing page anywhere in these threads.
- Don't edit the post to add marketing after it gains traction.

---

## 6. Pre-flight, ~10 minutes before you post

- [ ] Open the **demo on your phone** and add an asset. It's the first thing anyone clicks, and a 500 there wastes the whole launch.
- [ ] `https://github.com/brandonlea/lean-asset-tracker` loads and the README gif plays (verified working 2026-10-27).
- [ ] Follow your own README quick-start on a clean machine and confirm it installs. **This is the one that bites** — a broken install on launch day converts interest into a bad first impression you don't get to retake.
- [ ] Read the sidebar rules of whichever subreddit you're posting to.
- [ ] Decide you have 2 hours free. If not, post tomorrow.

## 7. Afterwards

Tell me how each one went and I'll do the follow-through: triage any GitHub issues that arrive, fix what's reported, and keep the uptime watch on the demo so it survives a traffic spike. `github_watch.py` already polls issues/PRs/stars every 15 minutes and will ping you.

One thing worth saying plainly: **10 installs is a realistic target, 10 paying customers in month one is not.** The free self-hosted version is what spreads; paid hosting converts later, from people who've already run it. If you judge month one on revenue you'll conclude a successful launch failed.
