# TrustHailer — Sprints 0 to 8, plus advert imports

Django foundation, phone-OTP identity, vehicle listings, the driver side of the
marketplace with search across both, a one-tap "I am interested" that opens a
conversation about a specific listing, document verification, reporting and
blocking, double-blind reviews anchored to real placements, a feed that
replaces the Facebook group, in-app notifications, a business directory, and a
staff tool for carrying Facebook rental adverts across so the browse page is
not empty on day one.

Everything here runs. `python manage.py test apps --settings=config.settings.test`
gives 384 passing tests.

**Milestone reached: every product surface named in the original build spec
now exists.** What is left after this sprint is operational — legal pages,
monitoring, backups tested by actually restoring one — not new features. Per
the build spec, this is still closed-beta territory: keep it to the 30–50
hand-onboarded owners before announcing anything.

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

**Advert imports — the cold-start answer**

- Staff-only two-step import at `/cars/import/`: paste the post link and the
  advert, check what we read out of it, publish. `apps/listings/importer.py`
  guesses make, model, year, transmission, price, deposit, platforms, PrDP and
  who-pays-what off the paste, so it is two minutes an advert instead of ten.
- Imported listings have **no owner**. `VehicleListing.owner` is nullable, and a
  check constraint holds the invariant: a listing has an owner, or it says where
  it came from. Never neither.
- Every imported listing links back to the original post and says plainly that
  nobody behind it is on the site — no introduction button, no masked number,
  because we hold no number at all.
- **Claim flow** at `/cars/<uuid>/claim/`. A member says the car is theirs,
  staff check it against the original post in the admin, approval hands the
  listing over and closes every other claim on it.
- `expire_imports` archives unclaimed imports after 21 days. Cron it nightly
  alongside `purge_kyc`.
- `apps/core/redact.py` strips phone numbers, emails and WhatsApp links out of
  anything typed into an import, unconditionally.
- Staff queue at `/cars/imports/`; claim review lives in the Django admin.

**Sprint 4 — introductions, since replaced by interest**

> Superseded. Nothing creates an `IntroRequest` any more — "I am interested"
> opens a conversation instead, see the interest section below. The model, the
> inbox and the approve/decline pages all remain, because the requests already
> filed are still answerable and an approved one still releases both numbers
> and still opens a thread. What follows describes those records.

- Double opt-in at `/requests/`. Ask about one specific listing, the other side
  decides, and **both numbers release to both people at the same moment**.
- Inbox with received and sent tabs, approve, decline and withdraw.
- Seven-day expiry. `IntroRequest.is_expired` tells the truth on the page
  whether or not the cron has run; `expire_intros` makes the stored status
  agree. Cron it nightly.
- Emails on approval and decline. **No phone number is ever put in an email**
  — the mail says "they said yes, open the request". The request email and
  the form that sent it are gone with the create view.
- One open request per person per listing, enforced by a partial unique index,
  and exactly one listing per request, enforced by a check constraint.
- The intro admin is strictly read-only. Staff can see that an introduction
  happened; nobody can approve one on somebody else's behalf.

**No wallet, no credits, no payment provider.** The spec had this sprint as
"intros and credits". The platform is free, so all of that would have been
unreachable code — see the decisions below.


**Interest — how a conversation actually starts**

- **"I am interested"** on a car or driver listing. One POST, and it opens the
  thread with the other person, creates an `Interest` tagging the listing, and
  sends an opening line that names it. `apps.messaging.services.express_interest`.
- The thread shows an **About** strip linking the listing, so an owner with four
  adverts can tell eleven conversations apart.
- **One interest per person per listing**, by unique constraint. That is the
  brake the old "one pending request" rule used to provide; pressing the button
  again reopens the chat rather than writing the same line twice.
- **Why the order changed.** The old flow asked an owner to release a phone
  number to somebody who had not yet said a word, and the commonest answer to a
  question asked that way is no. Now people talk first and exchange numbers
  when they want to.
- **What it costs.** An interest is one-sided, so a listing is an invitation
  anybody may answer — a genuinely more open inbox than before. Blocking,
  the per-listing limit, and taking the advert down are what hold it.
- **"We are working together"** sits at the top of the chat, for the owner of a
  car the thread is about. It creates the `Placement` **confirmed by the owner
  only**; the driver confirms from a button in the same thread. That second
  signature is the entire basis of the review system — see `Placement`.

**Sprint 5 — verification and safety**

- **Identity documents now go to private storage.** They did not before: the
  field used the default storage, which is `/media/` in development and a
  public bucket in production. Fixed, with a storage class that raises rather
  than ever returning a URL, and a boot-time guard against pointing the private
  bucket at the public one.
- Reviewers read a document through `accounts.views.kyc_document`, the single
  staff-gated path, which logs every read. The admin links to that, not to
  storage.
- **Member-facing verification at `/me/verification/`** — the ladder, and the
  upload form. Images are re-encoded at review-readable quality to strip EXIF;
  what happens to the file is stated above the button, not in the terms.
- **Report flow** on listings and profiles, into a staff queue, rate limited to
  ten a day. One report per person per thing.
- **Block flow.** The record is one-directional; the effect runs both ways —
  browse, search, detail pages, profiles and introductions, in both directions.
  Nobody is told they have been blocked.
- **Safety page at `/safety/`**, public, linked from the avatar menu and from
  the verification page.

**Sprint 6 — placements and reviews**

- **`Placement`** — a record that these two people did this deal, created from
  an approved introduction and confirmed by both sides. "Mark driver placed" on
  a car now goes through this, and takes the listing off the market.
- **`Review`, double-blind.** Neither side's review appears until both are
  written, or 14 days have passed. `publish_reviews` handles the timer; the
  pair publishes immediately when the second one lands.
- **The questions differ by role.** An owner is asked about payment and how the
  car was looked after; a driver about fairness, whether things got fixed, and
  whether the deposit came back.
- **No review without a confirmed placement.** No exceptions, no "review
  someone you dealt with offline" button.
- Aggregate ratings denormalised onto `Profile`, shown on both card types, the
  profile, and a public page at `/u/<handle>/reviews/`.
- Cron `publish_reviews` nightly, with the other three.

**Sprint 7 — the feed**

- **`Post`, `Comment`, `Like`.** Newest-first, no ranking algorithm — see the
  decisions below for why that is deliberate rather than a stopgap.
- **Composer at `/feed/new/`, no phone-verification gate.** Saying something
  does not precede handing over keys the way listing a car does, and a wall in
  front of the compose box would empty the feed on day one.
- **Topic is a fixed list of six** (general, advice, road alert, scam warning,
  earnings, maintenance) rather than free tags — the six things these groups
  actually organise around. **City, not suburb** — a warning is worth reading
  city-wide; a car listing is not.
- **True infinite scroll**, not a "load more" button: a sentinel element with
  `hx-trigger="revealed"` requests the next page and replaces itself with more
  posts and a fresh sentinel. `feed/_posts.html` renders both the first page
  (wrapped in `#posts` by the parent template) and every continuation (bare,
  with no wrapper of its own) — the same partial, so the two can never drift
  into different markup.
- **Comments nest one level, however the input is attacked.** `Comment.save()`
  re-points anything deeper at the top of its thread rather than rejecting it.
- **Contact details are refused in posts and comments**, same rule and same
  redactor as everywhere else that publishes text. The composer points at the
  listing form instead of silently eating what was typed — see the decisions
  below, because this is the one call in the sprint worth arguing with.
- **Hiding (staff moderation) and deleting (the author withdrawing their own
  words) are different operations that keep different things.** Hiding keeps
  the row for the trail; deleting removes it, because a "soft-deleted" copy the
  author cannot see is not honouring what they asked for.
- Posts join global search as a third section, matching the shape the cars and
  drivers sections already use.
- `/` now serves the feed when logged in — the Sprint-3-era placeholder page is
  gone.

**Sprint 8 — notifications and the business directory**

- **`Notification`.** Every message is composed and stored as plain text at
  the moment the event happens, not rendered live from a stored relation —
  see the model docstring for why. Eleven kinds, all tied to an existing
  trigger point: an introduction requested, approved or declined; a placement
  recorded or confirmed; a review published (at write time *and* off the
  14-day timer); a comment or reply; a claim approved or rejected; a business
  verified.
- **The unread count is a badge on the avatar, not a sixth top-bar icon or a
  sixth bottom-nav item.** Both are fixed by the interface principles this
  project has followed since Sprint 0. "Notifications" is a line in the
  avatar dropdown, same as Verification and Placements.
- **`/me/notifications/`** — opening it marks everything read in one bulk
  `UPDATE`, and still shows which rows were unread for this one render.
- **The business directory at `/directory/`.** The one listing type on the
  site whose phone number is shown in full, immediately, with no
  introduction step — see the model docstring for why that is the correct
  rule here and nowhere else.
- `apps.core.phone.normalise()` gained a `mobile_only=False` option so a
  business landline is not rejected by the same check that exists to stop
  someone entering a number that cannot receive an OTP.
- **"Verified" on a business means staff phoned the number and it reached a
  real business** — a different claim from the person-facing verification
  ladder, and the detail page says so in the same breath as the badge.
- Paid tier is data and plumbing (`is_paid`, `paid_until`), not a live
  feature — same "the seam exists, the price is zero" pattern as boosts and
  everything else `apps.core.pricing` touches.
- Businesses can be reported as themselves — `apps.safety` gained a
  `?business=` target alongside `?car=`, `?driver=` and `?user=`, since a
  business does not always have a linked account to route the report through.

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

**The platform is free, and `MONETISATION_ENABLED` is no longer an env var.**
Nothing costs anything: not listing, not browsing, not being introduced, not a
feature slot. The flag lives in `config/settings/base.py` as a hardcoded
`False`, deliberately out of the environment — charging for something is a
product decision that should arrive as a code change somebody reviewed, not as
a variable somebody flipped on a Friday.

`apps/core/pricing.py` stays anyway. It is the seam every chargeable action
already runs through, it answers "free" for everything while the flag is off,
and deleting it would only mean a data migration to put it back if the position
ever changes. Drivers return zero regardless of any flag, and a test holds that
line. The copy still says "free while we are building" rather than "free
forever" — the second is a promise no one should make on the platform's behalf,
and users told the truth up front churn far less than users who feel ambushed.

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

**Contact numbers are masked everywhere, and travel only when their owner
sends them.** `/u/<handle>/` shows `082 *** 4567`; car and driver detail show
no number at all, because there is no longer a request that releases one.
Inside a conversation, `redact_contacts` strips numbers out of what you type
until you press **Share my number**, which sends yours and lifts the strip for
you — not for the other person, and not in your other threads. Tests assert
the raw number never appears in any of those responses — keep them.

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

### From the notifications and directory work

**A notification stores its own sentence rather than a `GenericForeignKey`,
and that is the opposite choice from `Report.target` a few sprints earlier.**
The two look like the same kind of problem — "this row is about that other
row" — and are not. A report is read by staff soon after it is filed, against
whatever the target currently is; rendering it live is fine and saves nothing
by precomputing it. A notification can sit unread for weeks and has to keep
making sense after the thing it describes has changed or gone — "Thabo asked
about your Corolla" should still read correctly even if Thabo has since
deleted his account. Composing the sentence once, at the moment the event
happens, and storing the plain text is what keeps that promise; a live render
from a generic relation would leave either a broken link or a sentence with a
hole in it. It is also the cheaper query shape for a view that gets opened
often and skimmed fast — one row read, not one-plus-N resolved to build a
sentence.

**The unread badge lives on the avatar, not in a new icon, because the
interface principles fixed in Sprint 0 do not have room for one.** Three
things in the top bar, five in the bottom nav — both are stated constraints
this project has held since the beginning, and a notification bell is
genuinely the kind of feature that tempts you to make an exception "just this
once." The badge on the avatar and a line in the dropdown honour both rules
exactly, at the cost of the badge being slightly less discoverable than a
dedicated bell would be. That is the trade the existing design already made
for Placements and Verification, and notifications do not get to be special.

**A review's notification lives in the model, not only at the call site in
the view — because publication has two triggers, not one.** The live path (the
second review lands) is easy to remember to notify from, because a person is
sitting in the request-response cycle waiting for feedback. The 14-day-timer
path runs from `publish_reviews` in a management command with nobody watching,
and it is exactly the case where the recipient has no other way of finding out
a review appeared. Putting `notify()` inside `Review.publish()` means both
triggers get it for free and a future third trigger would too, rather than
depending on every call site remembering to add it — the same reasoning that
put `recalculate_rating()` in the same method a sprint earlier.

**A business listing shows its phone number in full, immediately, and that is
a deliberate reversal of the rule everywhere else on the site.** A car
listing masks the owner's number until an introduction is approved because
releasing it is a decision two people make together — and abusing that,
publishing a number nobody agreed to release, is the thing the redactor and
the masking exist to prevent everywhere else content gets published. A
business is different in kind, not degree: the entire reason to be in a
directory is that a stranger can find the number and phone it right now. Not
masking it here is not an exception to the site's privacy posture — it is
what the business asked for by listing itself.

**`phone.normalise()` gained a keyword-only `mobile_only` flag instead of a
second function.** Every existing call site sends an OTP to the number it
validates, so mobile-only was never wrong until this sprint — a business
landline is the first legitimate case for a number that only ever gets
*called*. Extending the one function with a default-True flag means every
existing call site is byte-for-byte unchanged and the new one opts in
explicitly, rather than two normalisation functions quietly drifting apart
the day someone fixes a bug in one and not the other.

**"Verified" on a business answers a different question than "verified" on a
person, and the detail page says so next to the badge rather than trusting
the label to carry the distinction on its own.** Everywhere else, verified
means an ID or a licence or a phone number checked out. Here it means staff
picked up the phone and it rang a real business — nothing about whether the
work is any good. A badge that means two different things depending on where
you see it, with nothing on the page explaining which one this is, teaches
people to stop trusting the badge at all — which is the one thing this whole
project cannot afford, on either side of that distinction.

**Businesses report as themselves, not through an owner account.** Staff can
hand-enter a business with nobody signed up yet — the same cold-start move the
Facebook advert importer makes for cars — so routing a report through
`owner_user` would 404 on exactly the listings newest to the site and least
vetted. `apps.safety._target_from_query` gained a `?business=` branch
alongside `car`, `driver` and `user`, using `Report`'s existing
`GenericForeignKey` the way it was always meant to be used — a business
listing is just one more kind of thing that can be reported, not a special
case.

### From the feed work

**Everything here is a deliberate step down from what the Facebook group
offers, and that is the pitch, not a limitation.** No algorithmic ranking, no
reshares, no reaction palette, one level of comment nesting. The complaint that
sends people looking for an alternative to a 40,000-member group is noise —
an infinite scroll tuned for engagement, a wall of reactions that let people
pile on without writing anything, threads six levels deep that are a column of
four characters on a phone. Building a quieter version of the thing people are
tired of is the actual product decision in this sprint, and it is worth
resisting the urge to add any of that back in as the site grows.

**Newest-first, with no ranking model, and that stays true even at scale.**
Two reasons. The practical one: at launch volume there is not enough data for
a ranking model to be anything but noise wearing the costume of judgement. The
one that does not go away with scale: half of what belongs here is
time-critical. A roadblock warning is worth reading for about four hours and
worthless after that, and any ranking that would surface a popular three-day-old
post over a fresh one has that exactly backwards. The day this needs pagination
beyond "load more automatically," the answer is still chronological order —
reach for a second, explicitly time-decayed section before reaching for a
black-box score.

**No phone verification to post, and that is not a smaller version of the same
rule that gates listing a car.** It is closer to the opposite: browsing,
signup, and now posting all stay open because the funnel and the room both have
to be full before anything else matters. Verification gates the moments where
trust starts to cost somebody money — a stranger taking your car keys, a number
being released. Saying something in the feed does neither, and a wall here
would produce an empty room, which is the one failure state this sprint cannot
recover from. Nobody comes back to check whether a quiet room got louder.

**Contact details are refused in posts and comments, and this is the one call
in the sprint most worth someone disagreeing with.** "Car available, call
082…" is the single most common post in the groups this replaces, and allowing
it here would let anyone route around the entire structured side of the
product — the filterable terms, the masked numbers, the double opt-in — with a
paragraph. It would also make everyone else's number fair game to post about
them without asking. The cost is real: somebody who wants to say "PM me" for an
honestly personal reason cannot, and the form points them at the listing form
instead, which is not always the right answer for what they were trying to say.
If this stops feeling right as real usage comes in, the fix is a narrower
carve-out — WhatsApp group invite links, say — not deleting the rule; the
redactor is shared with the advert importer, the review body and the
introduction message, and loosening it here alone would still leave three other
surfaces enforcing it.

**The infinite-scroll partial has exactly one job and no wrapper of its own,
which is the fix for a bug that would otherwise ship.** `feed/_posts.html`
renders bare — no `id="posts"` — because it is returned twice: once included
inside `feed.html`'s own `#posts` div for the first page, and once directly to
HTMX for every page after that, where the swap target is the sentinel element,
not `#posts`. Give the fragment its own wrapper and the second load nests a
new `#posts` inside the first, and the one after that nests a third — invisible
in a screenshot, and it would have quietly broken every future
`document.getElementById("posts")` on the page. Caught by reasoning about what
the swap target actually was, not by a browser.

**The first `hx-post` on the site was also the first thing to hit Django's CSRF
check, and it failed.** Every earlier `hx-*` attribute here was `hx-get` —
browse filters, the suburb typeahead — which carries no CSRF requirement, so
nothing had ever needed to put the token on an HTMX request. The like button
does, and manual testing in a real browser (not the Python test client, which
talks to the view directly and never exercises this) caught the 403 that
resulted. `app.js` now attaches an `htmx:configRequest` listener that reads the
`csrftoken` cookie and sets `X-CSRFToken` on any non-GET HTMX request, which
covers the like button today and whatever the next `hx-post` on the site turns
out to be. Confirmed after the fix by stress-clicking the same button
repeatedly through real DOM events and then checking the database directly —
`Post.like_count` matched `COUNT(*)` on `Like` afterwards, which is the
invariant an `F()`-based toggle under a rapid double-click is actually supposed
to protect.

**Hiding and deleting are different operations, kept deliberately separate.**
Deleting is a real delete — an author withdrawing their own words, and a
soft-deleted row they cannot see is not honouring that. Hiding is staff
moderation and keeps the row, because a moderation trail with the evidence
removed from it is not a trail. `Post.recount()` exists so a hidden or deleted
comment does not leave `comment_count` silently wrong on its parent.

**Django templates cannot look a dict up by a loop variable, so replies are
attached as an attribute instead of routed through one.** The detail view
builds `replies_by_parent` once, then walks the top-level comments and sets
`comment.child_replies` on each — a plain Python loop rather than a custom
template filter whose only job would be `dict.get`. One extra loop in the view
beats one more filter in the template tag registry that exists to work around
a language limitation.

### From the placements and reviews work

**This is the sprint that makes a badge mean behaviour.** Everything before it
verifies identity: an email reaches somebody, a number rings, an ID matched a
name. None of that says whether the person was any good to deal with, and a
driver choosing between two owners with identical green badges learns nothing
from the badges. A review is the first thing here that describes conduct, which
makes it the most valuable field in the database and the most attacked one.

**No review exists without a placement both people confirmed. Ever.** That one
rule kills the dominant abuse pattern on every classifieds site — accounts that
never transacted leaving reviews, bought praise on one side and competitor
damage on the other. The cost is real: a deal arranged entirely over WhatsApp
after meeting here cannot be reviewed unless somebody records the placement.
Pay it. An unverifiable review is worth less than no review, because it makes
every other review on the site unverifiable too. If a "review someone you dealt
with offline" button ever appears, this is what it is undoing.

**The placement dropdown is the security model, not a convenience.** You can
only record a placement with somebody you were introduced to through the site,
chosen from a list of approved introductions — never a free text field, never a
search over every member. That is what ties each review back to two people who
each pressed a button agreeing to be put in touch, and it makes a fake review
require a co-conspirator with a verified phone rather than a spare email
address.

**Both sides confirm, and one side creating it is not enough.** One person
asserting a placement is an assertion; two is a fact neither can quietly deny
later. Creating it confirms your own side only. Until the other person agrees,
neither of you can write anything.

**Double-blind publication is not optional and the 14-day escape is the load-
bearing half.** Without the blind, everybody waits to see what the other said
and every review becomes a reply to a review; retaliation does not even have to
happen for the damage to be done, because the fear of it is enough to produce
nothing but bland praise. Without the timer, one silent party freezes the
other's review forever — a free veto for anybody who suspects theirs is
unflattering, and the most motivated person to use it is exactly the one worth
reading about.

**Publication happens at write time as well as in the cron.** Somebody who has
just written the second review expects to see the first one. Making them wait
until 2am for a result the rules say is already due reads as broken, so
`publish_pair_if_ready` runs in the view and `publish_reviews` only ever handles
the one-sided case.

**That function queries reviews fresh instead of using `placement.reviews`.**
The placement was loaded with `with_display_data()`, whose prefetch cache still
holds the state from before the review that triggered the call — so the related
manager reports one review when there are two, and nothing publishes. It cost a
test failure to find, and it is exactly the sort of thing that would otherwise
have shipped as "the second review sometimes does not publish".

**The questions differ by side because the risks do.** An owner wants to know
whether the driver paid and looked after the car. A driver wants to know whether
the owner was fair, fixed things, and gave the deposit back. Asking both the
same generic questions throws away exactly the information each is looking for.
`deposit_returned` is a boolean rather than a rating because it either came back
or it did not, and it is the single most common thing that goes wrong.

**Ratings are denormalised onto `Profile` and recalculated whole.** The rating
sits on every card in a browse list, so averaging per render is a query per
card. It is recomputed from scratch on publication rather than nudged
incrementally — publication is rare, and a counter that drifts is worse than a
query nobody notices.

**A review refuses contact details, like everything else that gets published.**
A review outlives the deal and stays on a public page; a phone number in one is
somebody's number republished to strangers permanently.

**Staff can read unpublished reviews, and cannot edit any review.** The blind
protects people from each other, not from us — abuse reported in a review has
to be readable before the timer runs out, or the report button is useless for
the fortnight it matters most. The only staff remedy is removing a review
entirely, which keeps the temptation to quietly soften somebody's words off the
table. Placements themselves are read-only in the admin: editing one would
silently change who is allowed to review whom.

**Nobody outside a placement can open it, staff included.** It carries two
sealed reviews, and there is no support question that needs to read one before
it publishes.

**Django's date formatter, not `strftime`.** `%-d` is a glibc extension and
raises `ValueError: Invalid format string` on Windows, which is where this was
first run. Use `date_format` for anything user-facing.

### From the verification and safety work

**Identity documents were in the public bucket, and now they are not.** This is
the one thing in this sprint that was a live problem rather than a missing
feature. `VerificationDocument.file` used the default storage: `MEDIA_ROOT`
served at `/media/` in development, and in production the R2 bucket configured
with `querystring_auth=False` — public by design, because signing a URL for
every car thumbnail on a browse page would be absurd. The filenames are random
hex, so nothing was enumerable, but "nobody will guess the URL" is not access
control, and the URL was being written into an admin page and every reviewer's
browser history.

Documents now go to a separate `kyc` storage alias: a directory outside
`MEDIA_ROOT` in development, a second bucket with public access off in
production.

**`base_url=None` does not mean "no URL", which is why there is a storage
subclass.** `FileSystemStorage.base_url` falls back to `settings.MEDIA_URL`
when the value is None, so `document.file.url` happily returned `/media/kyc/…`
— a path that 404s today only because `runserver` serves `MEDIA_ROOT` and the
file is not in it. Accidental safety stops being safety the moment somebody
adds a route. `PrivateFileSystemStorage.url()` raises instead, so any code that
tries to render a link to an ID fails loudly in development rather than quietly
emitting a path in production. A test holds it.

**Reading a document goes through a view, not a link.** `kyc_document` is the
only path to a stored file: it checks staff status on every read, sets
`Cache-Control: no-store`, and writes a line to the log. Looking at somebody's
ID is an event worth having a record of, and a view is the only place that
record can be made. Not even the person who uploaded it can re-read it — there
is no reason to, and every extra path to a stored ID is another way to leak
one.

**Production refuses to boot if the private bucket is the public one.**
Everything would keep working if `R2_PRIVATE_BUCKET` were set to the media
bucket, and identity documents would be sitting somewhere served without
authentication. That is exactly the class of mistake that is invisible until it
is a news story, so it is a `RuntimeError` at startup.

**The promise about the file is above the upload button, in full size.**
Somebody about to photograph their ID for a website they found through a
Facebook group is right to hesitate, and that hesitation deserves an answer
rather than a smaller font. The page says what happens: a person reads it, the
file is destroyed in the same action, what survives is a date and a tick, and
anything abandoned in the queue is swept after 30 days. All of that was already
true in the code — the page just says so.

**KYC images are re-encoded at higher quality than car photos.** 2000px at
quality 90, not 1400 at 78. The EXIF stripping is the reason to re-encode at
all — a phone photo of an ID taken at home has the home address in it — but a
reviewer has to read an ID number off the result. A document nobody can read
gets rejected, re-uploaded, and reviewed twice, which costs the user data and us
double the work.

**A block is one row and a two-way effect.** Only the person who blocked is
recorded, and only they are told. But neither can see the other's listings in
browse, search or on a detail page, neither profile resolves, and neither can
request an introduction. A one-way block would leave the person who blocked
still visible to, and reachable by, the person they blocked — worse than
useless, because it feels like protection and is not.

**Nobody is told they have been blocked.** Every refusal is the same 404 an
unknown listing gives. A distinct message would turn a quiet exit into a
confrontation, which is the thing the person reaching for the button was trying
to avoid.

**Reports and blocks are different tools and stay separate.** A report says
somebody should look at this; a block says leave me alone. Collapsing them
serves neither: people who want to be left alone should not have to accuse
anybody, and people reporting a scam should not have to hide the evidence from
themselves to do it. The block page links to the report page, and says plainly
that blocking alone tells us nothing.

**There is still no public "known scammers" board, and there must not be.**
Under South African defamation law truth alone is not a complete defence — the
publication must also be in the public benefit — and the platform can be joined
to the claim. A page where users name individuals is a standing invitation to
be sued over somebody else's sentence, and it is trivially weaponised against a
competitor. Bad actors are handled by staff-reviewed suspension with evidence on
file and a right of reply. The report queue is where accusations live. The
safety page says this out loud so nobody has to guess why the board is missing.

**The safety page is public.** A driver being pressured for a deposit right now
has to be able to open it from a WhatsApp link without signing in, and somebody
deciding whether to trust the site at all should be able to read how it handles
a scam before creating an account.

### From the introductions work

**The credits half of Sprint 4 was not built, and that was the point of making
the platform free.** The spec called for a wallet, an append-only ledger, credit
packs, a payment provider and an idempotent webhook. None of it would be
reachable: `pricing.price_for` returns zero for every action, so a debit path
could never run and a payments integration nobody can spend money through is
just code rotting between the day it is written and the day it might be wanted.

What was kept is the seam. `IntroRequest.credits_charged` is written on approval
from `pricing.price_for(INTRO_APPROVE)` rather than assumed to be zero, so the
day there is ever a price it lands in a column that already exists and every
approval in history reads back consistently. That is one line and a field, not
a payments stack.

**Both numbers release at the same instant, or the whole thing is pointless.**
An exchange where only one person becomes reachable is not an introduction, it
is a lead — and a site that generates leads out of people who thought they were
agreeing to a conversation is the thing we are supposed to be better than. This
is also why asking requires a verified phone, not only approving: an unverified
asker would take a number and give nothing back.

That extends the rule stated further up — verification at listing a car and at
approving an introduction — to the other half of the same moment. Browsing,
posting and listing yourself as a driver stay open, so the funnel is still not
walled.

**Contact details are refused in the request message, with a reason shown.**
Left alone, the first thing everybody types is their number, and within a week
the double opt-in is a formality people route around by writing "call me on
082…". That hands a number to somebody who has not agreed to receive it and
makes approval meaningless for the person on the other end, who now gets phoned
whether they said yes or not. The importer's redactor does the detection; the
form refuses rather than silently eating what was typed, because somebody who
is quietly edited assumes the site is broken and sends it again.

**No phone number goes in an email, ever.** The approval mail says they said yes
and links to the request. An inbox is not a safe place for somebody else's
number — mail gets forwarded, screenshotted, synced to a shared tablet, left
signed in on a resold phone — and, unlike a number on the site, a number in
fifty inboxes cannot be pulled when an account turns out to be a scammer. There
is a test that reads every outgoing message and asserts neither party's number
is in it.

**Release is per-introduction, not a switch that unmasks a listing.** After an
approval the car page still shows `082 *** 4567`. The number lives on the
request page, which is the record of who agreed to what and the one place it can
be withdrawn from. The existing tests that assert a raw number never appears on
a listing page still pass, and they should keep passing forever.

**The intro admin is read-only, including for staff.** Staff need to see that an
introduction happened — for a dispute, a scam report, a "they never called me".
Approving one on somebody's behalf would release a number they did not agree to
release, which is the single thing this flow exists to prevent. There is no
support question that needs it, so there is no path to it, and a test checks
that a staff account gets a 404 on somebody else's request.

**A request dies of something after seven days.** Owners go quiet — they place
a driver and forget the inbox, they lose the phone, they cannot face typing no.
A request that stays open forever tells the person who sent it nothing, and the
tenth silent one teaches them the site does not work. After a week the honest
reading is no. `is_expired` is a property as well as a cron sweep, so a night
without the command running is cosmetic rather than misleading.

**Declining asks for no reason, and is not gated on verification.** A required
reason box turns a no into a small confrontation, and the result is people
answering nothing at all — which is worse for the person waiting. Saying no
releases nothing, so putting a verification wall in front of it would only push
more people into silence.

### From the import work

**Imports are scaffolding, and the code says so in three places.** They rank
below members' own listings in `ranked()`, they wear a grey flag on the card
rather than the signal amber, and they archive themselves after 21 days. The
measure of success is imports falling as a share of the cars page, not rising.
An imported advert is a listing nobody can be introduced through, and
introductions are the actual product.

**A stale import is worse than an empty page.** There is no owner on the site
to pause an imported listing when the car goes, and the car usually goes within
a fortnight. A driver who spends data opening it and airtime chasing it, then
hears the car went a month ago, has learned something true about the site and
will not check back. `expire_imports` is the fix and it is not optional — cron
it, or do not import.

**We copy the facts, never the words or the photos.** Make, year, price, who
pays for the fuel: facts, unprotected, and exactly what the filters run on. The
paragraph somebody wrote around those facts is theirs, and so are the photos.
So the summary is written in our own words, capped at 600 characters, and the
link to the original is the attribution. The confirmation checkbox on the
import form is not stored anywhere — it exists to make a person read one
sentence before publishing somebody else's advert.

**The phone number never comes across, and that is enforced twice.** Once by
policy — staff are asked to summarise, not paste — and once by
`apps/core/redact.py`, which strips numbers, emails and WhatsApp links out of
the summary on the way in whatever anyone typed. Two reasons, both real: under
POPIA, consent to a Facebook group seeing your number is not consent to a
website you have never heard of publishing it; and a number sitting in a
description is a hole straight through the introduction flow, which is the only
thing on this site anybody would ever pay for.

**The redaction pattern is anchored on a dialling prefix, not on a run of
digits.** "Nine or more digits in a row" also matches `R2 500 deposit 5000`,
and a redactor that eats the rent is worse than none. Matching only what starts
with `0`, `+27` or `+263` keeps prices, years and kilometre limits intact.
There is a test for exactly that.

**Owner is nullable, and a check constraint holds the line.** An imported
advert has nobody behind it, so `owner` is null until somebody claims it. The
constraint — owner is not null, or `source_url` is not empty — is at the
database rather than in `clean()`, because the ordinary create path attaches
the owner *after* the form validates; a model-level check would fire on every
owner listing on its way in and be wrong every time.

**Claims are staff-reviewed, and they need a verified phone.** A one-tap claim
button is an open door to taking over somebody else's advert and collecting
deposits under a photo of a car you have never seen — the exact scam this site
exists to design out. Approving is what moves ownership, and it rejects every
other claim on that listing in the same step. The phone-verification bar is the
same one `/cars/new/` sets, because approval produces the same thing: a person
who owns a live car listing.

**A claim never rewrites where a listing came from.** After approval the
listing still says it was originally posted on Facebook and still links to the
post. Ownership answers who is responsible for it now; the provenance answers
where it came from, and quietly dropping the second would make the first look
like something it is not.

**Imports publish without a photo; owner listings still cannot.** The draft step
on the owner side exists only to enforce the photo rule, and the photo rule
exists because a listing with no picture gets scrolled past. We deliberately do
not copy photos off the original post, so applying the same rule to imports
would mean no imports at all. The card placeholder plus a "From Facebook" flag
is the honest version of that trade.

**If somebody asks for their advert to come down, take it down.** It is on the
import screen in those words. There is no version of arguing about this that
ends well.

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

Everything in this section blocks launch. Ideas that do NOT block anything live
in [FUTURE.md](FUTURE.md) — keep the two apart, or the checklist stops being a
checklist.

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
- [ ] Cron `publish_reviews` nightly. Without it a one-sided review never goes
      up, which hands a silent party a permanent veto over the other's review.
- [ ] Cron `expire_intros` nightly, next to the others. Requests nobody
      answered have to close, or the received tab fills with dead ones and
      people stop opening it.
- [ ] Create the private R2 bucket and set `R2_PRIVATE_BUCKET` to it, with
      public access switched off at the provider. Production refuses to start
      if it matches `R2_BUCKET`, but it cannot check the bucket policy for you
      — go and look.
- [ ] Add the report queue and the block list to whatever you check daily. A
      moderation queue nobody opens is worse than no report button, because the
      button is a promise.
- [ ] Set `SITE_URL` in the environment. Every link in an introduction email is
      built from it, so a wrong value makes the mail useless.
- [ ] Cron `expire_imports` nightly too. Without it the cars page slowly fills
      with adverts for cars that went weeks ago, which teaches drivers the site
      wastes their airtime.
- [ ] Write the takedown line into the privacy notice: what we copy from a
      public post, why, and the address to write to. Then honour it same-day.
- [ ] Agree who does imports and how many. This is a hand-cranked tool on
      purpose; if it needs a rota, the answer is more owners onboarded, not
      more imports.
- [ ] Register as information officer with the Information Regulator; publish
      the privacy notice and PAIA manual. Add rating screenshots to the
      retention schedule.
- [ ] `work_suburbs` is a `SelectMultiple`, which is poor on a phone. Replace it
      with a chip picker over the existing `/geo/suburb-search/` typeahead
      before the beta.
- [ ] Watch how often the feed's "leave contact details out" refusal actually
      fires once real owners are posting. If people keep hitting it trying to
      say something legitimate a phone number was never needed for, that is
      the signal to design a narrower carve-out — not to loosen the rule
      everywhere it is enforced.
- [ ] Bootstrap and HTMX are still CDN-linked in `base.html` (see the first
      item on this list) — feed pages now depend on both loading before the
      like button or the topic filter will work at all, which raises the cost
      of that item slightly.
- [ ] Give somebody the job of phoning new directory listings before ticking
      "verified" — see `BusinessListingAdmin`. The badge is worth nothing the
      first time it is granted to a number nobody actually called.
- [ ] Saved-search digests still don't send — see Sprint 3. In-app
      notifications now exist and cover the events that matter more (a request,
      a placement, a review), so this has slipped further down the list on
      purpose rather than by accident.
- [ ] PWA manifest and service worker, legal pages (T&Cs, privacy, POPIA
      notice, PAIA manual), Sentry, and backups tested by actually restoring
      one — the operational half of Sprint 8, still outstanding.

Next up: launch prep. Every product surface the build spec named now exists,
so what is left is deliberately not a features list — PWA manifest and service
worker, the legal pages, Sentry, backups tested by actually restoring one, and
the two checklist items just above. Give it to real owners in one metro before
any of that, per the spec's own instruction after Sprint 6: this is closed
beta, not a launch.
