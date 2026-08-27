# TrustHailer — Sprints 0 & 1

Django foundation plus phone-OTP identity for the e-hailing owner/driver platform.

Everything here runs. `python manage.py test apps --settings=config.settings.test`
gives 57 passing tests.

---

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # dev works without editing this

python manage.py migrate
python manage.py seed_geo --launch-market johannesburg --launch-market ekurhuleni
python manage.py createsuperuser        # email address
python manage.py runserver
```

Then open `http://127.0.0.1:8000/` and sign up. **The code prints to your
terminal** — dev uses Django's console email backend.

```bash
python manage.py test apps --settings=config.settings.test
python manage.py check --deploy --settings=config.settings.prod
```

---

## What's built

**Sprint 0 — foundations**

- Settings split: `base` / `dev` / `prod` / `test`. Production reads everything
  sensitive from the environment and passes `check --deploy` clean.
- Custom `User` model with phone as the username field, in place before the
  first migration.
- `geo` app with 174 seeded suburbs across Gauteng, Harare and Bulawayo, plus
  an `is_launch_market` flag so you can open one metro at a time.
- Image pipeline: every upload re-encoded to WebP, resized, EXIF stripped.
- Rate limiting utility.
- Base template, bottom nav, design tokens, PWA manifest.

**Sprint 1 — identity**

- **Email OTP signup and login.** One door for both, no password, no SMS bill.
- Codes stored hashed, expire in 15 minutes, die after 5 wrong attempts,
  invalidated when a new one is issued.
- Three-layer send throttle: cooldown, per-address daily cap, per-IP hourly cap.
- Three-step onboarding wizard (role → location → profile + phone) with
  middleware that funnels half-finished users back into it.
- **Deferred phone verification** — collected at onboarding, verified only at
  the moments trust carries weight. Manual (WhatsApp) or SMS, by config.
- Public profile at `/u/<handle>/` with the verification ladder.
- Verification model, document upload model, admin review queue, nightly purge.
- Pluggable channels: email (Django), SMS console/memory/Clickatell stub.
- `apps/core/pricing.py` — everything free, monetisation behind one flag.

---

## Decisions worth not undoing

**Email carries signup; the phone number is deferred.** Email costs nothing to
send, so the funnel scales for free. SMS runs about R0.25 a message, which means
an SMS-at-signup design bills you for every visitor who never comes back. The
phone number is still collected at onboarding — deals in this market happen on
the phone, not by email — but it is verified only when someone lists a car or
approves an introduction. Roughly 5% of signups reach one of those, so SMS spend
tracks activity instead of curiosity.

The trade-off, stated plainly: email is a weaker identity signal here than a
phone number. Plenty of drivers have a Gmail address only because Android asked
for one. That is why phone verification is deferred rather than dropped, and why
the verification ladder puts email at the bottom in near-grey.

**While `PHONE_VERIFICATION_CHANNEL` is `manual`, no SMS is sent at all.** Users
WhatsApp the support number and staff tick them off in the admin. At seed scale,
when you are hand-onboarding fifty owners anyway, that costs nothing and is a
better signal than an automated SMS — you have actually spoken to the person.
Switch to `sms` when the queue outgrows you.

**Email deliverability is the real price of free.** An SMS arrives; an email
lands in spam unless SPF, DKIM and DMARC are configured on the sending domain.
A sign-in code in a spam folder is a user lost silently. Set all three up before
launch and send from a subdomain such as `mail.trusthailer.co.za`, never a free
mailbox.

**Phone numbers are stored in E.164, always.** `apps/core/phone.py` normalises
on the way in. Never store what the user typed. Empty phones store as NULL, not
`""`, or the second user without a number collides on the unique index.

**Everything is free, but the plumbing for money already exists.** Chargeable
actions route through `apps/core/pricing.py` and will write a ledger entry at a
price of zero. Turning on monetisation is a settings flag plus a payment
provider, not a rebuild. Drivers return zero regardless of any flag, and a test
holds that line. Say "free while we are building", never "free forever" — users
told the truth up front churn far less than users who feel ambushed.

**OTP codes are hashed.** A dump of the `PhoneOTP` table gets an attacker
nothing. The `attempts` counter is what stops a six-digit code being
brute-forced — five guesses out of a million is not a meaningful attack.

**Rate limits are a budget control, not just a security control.** At roughly
R0.25 an SMS, an unthrottled send endpoint is a way for a stranger to spend your
money. The three limits in `_guard_otp_send` each close a different hole:
cooldown stops double-taps, the per-number cap stops one number being farmed,
the per-IP cap stops someone walking a list of numbers.

**EXIF is stripped from every upload.** Phone cameras write GPS coordinates into
photos. A car photographed in the owner's driveway would otherwise publish their
home address. This is a POPIA obligation, not a nicety.

**Identity documents are deleted on review.** Approving in the admin sets the
flag on `Verification` and deletes the file in the same action. `purge_kyc`
sweeps up anything abandoned in the queue. A permanent archive of ID scans is
the largest legal exposure this project can create and it buys nothing once the
flag is set.

**Contact numbers are masked everywhere until an introduction is approved.**
`/u/<handle>/` shows `082 *** 4567`. A test asserts the raw number never appears
in that response — keep that test.

**Roles are checkboxes, not a single choice.** Owner-drivers are common. A radio
group would force them to misrepresent themselves on the first screen.

**Suburb is required at signup, and only launch-market suburbs are offered.**
Suburb-level location is the entire advantage over a Facebook group. The form
re-filters the queryset against the posted city so a crafted POST can't attach a
profile to an unlaunched area — there's a test for that too.

**Handles upgrade once, then freeze.** `/u/user-4821/` becomes
`/u/thabo-mokoena/` when the name arrives, and never changes again, because
someone may already have shared the link.

**No webfont.** The system stack costs the user zero kilobytes. On a prepaid
data bundle that's the correct answer, not a compromise. Character comes from
the type scale and tabular figures on money and ratings.

---

## Design language

One accent: signal amber (`--signal: #E07A0C`), from indicators and road
signage, used only on primary actions. Deliberately not blue — this shouldn't
read as a Facebook clone.

The signature element is the **verification chip**. Trust is the product, so the
badge system gets the only full colour ladder in the stylesheet and looks
identical everywhere a person appears. Everything else stays quiet so it reads
loudly.

Bottom nav is five items, permanently. A sixth belongs in the avatar menu. That
constraint is what keeps this legible to someone using a smartphone app for the
first time.

---

## Before you go further

- [ ] Vendor Bootstrap and HTMX into `static/` — don't ship CDN links to users
      on slow or filtered connections.
- [ ] Pick an email provider and configure SPF, DKIM and DMARC. Brevo (300/day)
      and Resend (3,000/month) both have permanent free tiers that cover launch;
      Amazon SES is cheapest beyond them. SendGrid dropped its free plan in 2025.
- [ ] Implement `ClickatellSMSBackend.send()` only when you switch
      `PHONE_VERIFICATION_CHANNEL` to `sms`. It deliberately raises
      `NotImplementedError` rather than shipping a snippet I couldn't verify.
- [ ] Generate real PWA icons at 192px and 512px into `static/img/`.
- [ ] Geocode the 135 suburbs with null coordinates before enabling radius
      search. Don't guess them — a wrong coordinate silently puts a car in the
      wrong part of the city and nobody can tell.
- [ ] Switch to Postgres. SQLite is the dev default only.
- [ ] Cron `purge_kyc` nightly.
- [ ] Register as information officer with the Information Regulator; publish
      the privacy notice and PAIA manual.

Next up is Sprint 2: vehicle listings, photos, and suburb search.
