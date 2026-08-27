# TrustHailer — Sprints 0 to 3

Django foundation, phone-OTP identity, vehicle listings, and the driver side of
the marketplace with search across both.

Everything here runs. `python manage.py test apps --settings=config.settings.test`
gives 160 passing tests.

**Milestone reached: the marketplace is browsable.** Per the build spec, this is
the point to stop and show it to ten owners you know. Watch them use it. Fix what
confuses them before starting Sprint 4.

---

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # dev works without editing this

python manage.py migrate
python manage.py seed_geo --launch-market johannesburg --launch-market ekurhuleni
python manage.py seed_platforms
python manage.py createsuperuser        # email address
python manage.py runserver
```

Then open `http://127.0.0.1:8000/` and sign up. **The code prints to your
terminal** — dev uses Django's console email backend.

```bash
python manage.py test apps --settings=config.settings.test
python manage.py check --deploy --settings=config.settings.prod
```

Django 5.2 rather than 5.1: it is the LTS, and it is the earliest release that
runs on Python 3.14. On 3.14, Django 5.1 raises `AttributeError` inside
`BaseContext.__copy__` while rendering any template, so every view test errors.
If you are pinned to 5.1 for another reason, run on Python 3.12 or 3.13.

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

**Sprint 2 — vehicle listings**

- Full CRUD at `/cars/`, with structured commercial terms: arrangement, rates,
  deposit, and five separate `*_paid_by` fields.
- Multi-photo upload (up to 8), cover selection, reordering, deletion. Every
  file goes through the shared pipeline.
- Browse with filters in a bottom offcanvas — suburb, city, platform,
  arrangement, max weekly price, who-covers-what, no-PrDP — swapped over HTMX
  so filtering never reloads the images already on screen.
- Boosted listings rank first, then newest. Boost is free and already routed
  through `pricing.py`.
- `Platform` model carries per-platform vehicle age limits as **data**, and
  warns on a listing when a car exceeds a limit somebody has actually confirmed.

**Sprint 3 — driver listings and search**

- Driver listing CRUD at `/drivers/`: headline, experience, licence code, PrDP,
  platform history, preferred arrangement, weekly rate ceiling, home suburb and
  up to 8 work areas.
- **Platform rating proof.** A driver claims an Uber or Bolt rating and uploads
  the screen behind it. Staff check it in the admin; the screenshot is deleted
  in the same action.
- Browse and filter drivers — suburb (home *or* work areas), platform,
  arrangement, experience band, PrDP, phone-verified only, has-a-verified-rating.
- **Global search at `/search/`**, across cars and drivers, wired to the top bar.
- Saved searches at `/me/saved-searches/`, stored from validated filter forms.
  Not yet alerting, and the page says so.
- Public profiles now show what a person has on the marketplace, on both sides.

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

**OTP codes are hashed.** A dump of the `OTPChallenge` table gets an attacker
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
`/u/<handle>/`, car detail and driver detail all show `082 *** 4567`. Tests
assert the raw number never appears in any of those responses — keep them.

**Roles are checkboxes, not a single choice.** Owner-drivers are common. A radio
group would force them to misrepresent themselves on the first screen.

**Suburb is required at signup, and only launch-market suburbs are offered.**
Suburb-level location is the entire advantage over a Facebook group. Every form
that takes a suburb — onboarding, car listing, driver listing — re-filters the
queryset against the posted city so a crafted POST can't attach a record to an
unlaunched area. There are tests for all three.

**Handles upgrade once, then freeze.** `/u/user-4821/` becomes
`/u/thabo-mokoena/` when the name arrives, and never changes again, because
someone may already have shared the link.

**No webfont.** The system stack costs the user zero kilobytes. On a prepaid
data bundle that's the correct answer, not a compromise. Character comes from
the type scale and tabular figures on money and ratings.

### From the listings work

**The `*_paid_by` fields are the product, not paperwork.** Who buys fuel, who
fixes the clutch, whose name the insurance is in — that is the substance of
every deal here and the source of most disputes. In a Facebook group it is
buried in free text, so nobody can filter on it and half the thread is people
re-establishing the same five facts. Structuring them is what makes "maintenance
covered" a filter, and that filter is a real reason to leave the group.

**Vehicle age limits are data, not constants.** Every platform sets its own
rule, the rules differ by city, and they change without announcement. A number
hardcoded in the seed command would quietly mislead owners the day it went
stale — and an owner who lists a car on a wrong limit, then gets turned away at
the inspection centre, blames you. `max_vehicle_age_years` starts null, which
means we simply don't warn. That is the honest failure mode. Confirm each rule
from the platform and set it in the admin.

**`ranked()` needs `nulls_last`.** `boost_expires_at` is null for almost every
row. Without `nulls_last` on a descending order, Postgres sorts nulls first and
every unboosted listing outranks every boosted one — a bug that is invisible in
SQLite and silent in production.

**Publishing a car is blocked without a photo.** A listing with no picture gets
scrolled past, and one ignored listing teaches an owner the whole site doesn't
work. Better to stop them at the gate with a reason.

**Django 5 refuses `multiple` on a plain file input, for a good reason.** The
default `FileField` cleans one file and would silently discard the rest. The
pair of `MultipleFileInput` / `MultipleFileField` in `listings/forms.py` is the
supported way round it — and the second half is the part that's easy to forget,
because a plain `ImageField` handed a list rejects the whole submission with
"No file was submitted" before any `clean_<field>` runs.

### From the driver side

**Listing a car needs a verified phone. Listing yourself as a driver does not.**
This asymmetry is deliberate. Listing a car is the moment an owner offers to
hand a stranger the keys to a R200 000 asset, so it is worth an SMS. Publishing
a driver profile gives nothing away — contacts release only when an introduction
is approved, and approval is itself gated. A verification wall in front of the
driver form would only thin out the supply owners come here to browse. The
incentive is applied as a pull instead: `DriverListing.objects.ranked()` puts
verified drivers above unverified ones, which is visible on the first screen.

**Unverified ratings are never shown as ratings.** Cards and search results
carry only ratings staff have checked against a screenshot. Self-reported
figures appear on the detail page alone, in a separate block, greyed and
labelled. An unchecked 4.98 rendered next to a checked 4.72 in the same typeface
teaches owners that the badge means nothing — and the badge is the entire
product.

**Rating screenshots are deleted on review, exactly like ID documents.** A
screenshot of the Uber or Bolt driver app carries a photo, a legal name, a trip
history and often earnings. That is ID-grade personal information, so it gets
ID-grade handling: `PlatformRatingProof.review()` records the decision and
deletes the file in one step, the admin has no path that skips it, and
`purge_kyc` sweeps anything abandoned in the queue after 30 days. What survives
is a number and the fact that someone checked it. That is all the product needs.

**`hide_from_search` has to be honoured on the driver list, not only on
profiles.** A driver listing is a person advertising themselves. Someone who
switches themselves off must actually disappear, or the setting is a lie — and
under POPIA it becomes an objection we recorded and then ignored. That is what
`DriverListingQuerySet.searchable()` is for; use it instead of `live()` on every
browse surface, and note that hidden is not the same as paused.

**A driver's suburb filter matches home *or* work areas.** An owner in Midrand
wants the Tembisa driver who works Midrand. Filtering on home suburb alone
hides exactly the person they are looking for. Work areas are capped at eight,
because a driver who ticks every suburb tells an owner nothing and makes the
filter useless for everyone else.

**One driver listing per driver.** The same person appearing twice in one list
helps nobody and makes every count on the site wrong, so `/drivers/new/` sends
you to your existing listing instead. The model keeps a plain foreign key rather
than a one-to-one, because a driver with a history of archived listings is a
reasonable thing to want later.

**Driver listings go live immediately; car listings start as drafts.** The draft
step exists on the car side only because a car cannot be published without a
photo. Nothing on the driver side needs that wait, and a second step is a second
place to lose someone.

**`trust_rank` approximates `Verification.level` on purpose.** The real property
applies licence and PrDP expiry rules that belong in Python. Reproducing that
date arithmetic inside a `CASE` expression would leave two definitions of
verification to drift apart. Ordering only needs the coarse shape — the badge on
the card still renders from the real property.

**Global search shows cars and drivers in separate sections, never one merged
list.** Ranking them against each other means inventing a score that claims a
2019 Corolla beats a driver with six years on Bolt. Nobody searching wants that
answer. The page is a signpost: a few of each, a count, and one tap through to
the browse page that can actually narrow it. Posts join in Sprint 7, and the
shape makes that an addition rather than a rewrite.

**Search uses `icontains`, and that is fine for now.** It is portable to the
SQLite dev database and fast enough at launch scale. The upgrade is Postgres
full-text search — a `SearchVector` over make/model/description and
headline/about behind a GIN index. Reach for it when it is actually slow. A
trigram index over a thousand rows buys nothing.

**Saved searches are written from a validated form, never from `request.GET`.**
`FilterFormMixin.as_saved_params()` serialises `cleaned_data`, so anything the
form doesn't recognise is dropped on the way in. A saved search is replayed
against a queryset weeks later; that round-trip through a form is the only thing
that makes it safe. There is a test that posts junk and checks it never lands.

**Saved searches store a frequency but send nothing yet.** Collecting it now
means the digest job has an audience the day it is written. The page says
plainly that alerts aren't switched on — a user who saves a daily alert and
hears nothing for a week concludes the site is broken.

---

## Design language

One accent: signal amber (`--signal: #E07A0C`), from indicators and road
signage, used only on primary actions. Deliberately not blue — this shouldn't
read as a Facebook clone.

The signature element is the **verification chip**. Trust is the product, so the
badge system gets the only full colour ladder in the stylesheet and looks
identical everywhere a person appears. Everything else stays quiet so it reads
loudly. The one exception added in Sprint 3 is the verified rating chip, which
borrows the same green — it is the only other number on the site that has been
checked by a human.

Driver cards are the same object as car cards, with a portrait where the photo
goes. The two lists sit one tap apart in the bottom nav, so they have to read
as the same kind of thing.

Bottom nav is five items, permanently: Feed · Cars · List · Drivers · Me. A
sixth belongs in the avatar menu — which is where the driver listing, platform
ratings and saved searches live. That constraint is what keeps this legible to
someone using a smartphone app for the first time.

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
- [ ] Confirm each platform's current vehicle age limit and set it in the admin.
      Until then the site doesn't warn, which is the honest default.
- [ ] Switch to Postgres. SQLite is the dev default only.
- [ ] Cron `purge_kyc` nightly. It now sweeps rating screenshots as well as ID
      documents, so this is more load-bearing than it was.
- [ ] Register as information officer with the Information Regulator; publish
      the privacy notice and PAIA manual. Add rating screenshots to the
      retention schedule.
- [ ] `work_suburbs` is a `SelectMultiple`, which is poor on a phone. Replace it
      with a chip picker over the existing `/geo/suburb-search/` typeahead
      before the beta.

Next up is Sprint 4: introductions and credits — the wallet and ledger, the
double opt-in request flow, contact release, and one payment provider with an
idempotent webhook. The disabled "Request an introduction" buttons on car and
driver detail are where it plugs in.
