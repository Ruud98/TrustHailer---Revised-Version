# Future features

Ideas worth building later, with enough written down that the next person does
not have to rediscover why.

**This is not the launch checklist.** `README.md` ends with "Before you go
further", which is the list of things that must happen before real members
arrive — vendored assets, crons, legal pages, Postgres. Nothing in this file
blocks anything. It is the opposite list: things that are only worth doing once
the thing is alive and somebody has asked for them.

Each entry says what it is, why it belongs on THIS product rather than on a
product in general, what in the code it would touch, and what is unresolved.
The last part matters most — an idea with its open questions written down is
one somebody can pick up; an idea without them is one somebody has to start
from scratch.

---

## 1. Cartrack: read fleet mileage instead of asking for it

Let an owner connect their Cartrack account so TrustHailer reads each car's
odometer automatically, rather than waiting for somebody to type it in.

### Why this one is worth doing

The service-reminder system already exists and is built on a reading that
nobody wants to maintain. `VehicleListing.odometer_km` carries a comment
admitting the cost outright: the reading is only as fresh as the last time the
owner updated it, so a reminder fires late if they leave it. `odometer_at`
exists purely so the page can confess how stale the number is, and
`estimated_odometer_km` exists purely to project a stale number forward so the
threshold check means something — capped at `MAX_PROJECTION_DAYS`, past which
its own docstring calls the figure "arithmetic rather than an estimate".

That is three pieces of machinery working around one missing input. A car with
a live feed needs none of it.

The audience overlap is already measurable in the data. `has_tracker` and
`tracker_paid_by` are fields owners fill in today, and tracking is close to
standard on rented vehicles in this market — a car handed to a stranger for
R2,000 a week is a car with a tracker on it. **Before building anything, run
the count**: how many live listings have `has_tracker=True`, and of those, how
many owners are on Cartrack rather than Netstar, Tracker or Matrix. If the
answer is eleven cars, this is not the next thing to build.

### What it would touch

- `VehicleListing.odometer_km` / `odometer_at` / `odometer_by` — the write
  target. Note `odometer_by` is a FK to a user, recorded "so the page can say
  whose figure it is showing". An automatic reading has no user, and null
  already means unknown, so this needs an explicit source field rather than a
  null — otherwise the interface cannot tell "nobody has ever confirmed this"
  from "a machine confirmed it an hour ago", which are opposite facts.
- The service-reminder command — already cron-driven, already sends reminders
  and requests odometer readings. A connected car should stop being asked.
- `km_per_week` — currently derived from the gap between two manual readings.
  With a real feed this becomes a genuine figure, and it is the number a driver
  most wants when judging whether a car is worth taking.

### Shape

An adapter, not a Cartrack client. `TelematicsLink` per owner (provider,
credentials, status, last sync) and a mapping from a listing to the provider's
own vehicle id, with a nightly command that pulls readings. Cartrack is one of
four or five providers here; writing directly against its API means doing this
work again for Netstar. The provider interface should be the smallest thing
that works — "give me an odometer for this vehicle id" — precisely so the
second provider is a day's work.

### Unresolved, and needs answering before any code

**Whether the API is even available on the terms we need.** Cartrack's API is
commercial and account-scoped, not self-serve like a public developer platform.
Find out: what an owner's own account can authorise, whether there is an OAuth
flow or only credentials, and what it costs — per account, per vehicle, or at
all. Do not design against a guess; get one real owner to show you what their
account can do.

**Credentials are a liability we would be choosing to take on.** If there is no
OAuth flow, this means storing something that grants access to a third party's
fleet account. Per-owner API keys, encrypted, never the owner's password, and
revocable from the owner's settings page. If the only way in is storing a
password, that is a good reason not to build this.

**POPIA, and this is the sharp one.** A tracker's data is not only an asset
record — it is a log of where a named person drove, and that person is the
driver, who is not the one connecting the account. This site already strips GPS
out of every uploaded photo, and `apps/core/images.py` calls that "not optional
under POPIA". Pulling live location while stripping it from photographs would
be an obvious contradiction.

The way through is to ingest **the odometer only** — a single integer per
vehicle per day, no trips, no coordinates, no timestamps of movement — and to
say so plainly wherever the feature is described. If location is ever wanted
(handover disputes, recovery), it needs the driver's own separate consent, not
the owner's, and it should be treated as a different feature with a different
argument.

**Scope.** TrustHailer matches owners with drivers and carries the trust
between them. It is not a fleet management product, and Cartrack's own
dashboard is better at being one than we will be. The test for anything built
here: does it make a listing more honest or a handover safer? Mileage does.
Live maps, geofences and driver-behaviour scores do not, however easy they are
to add once a connection exists.

---

## Adding to this file

Copy the shape above:

```markdown
## N. Short name: what it does in one line

### Why this one is worth doing
What in the product today is worse without it. Point at real code or real
numbers, not at a general principle.

### What it would touch
The models, commands and surfaces involved.

### Shape
The smallest thing that would work.

### Unresolved
The questions that have to be answered first, and what would make this a bad
idea. An entry with an empty Unresolved section usually means it has not been
thought about yet.
```

Keep entries that get rejected, with the reason. A written-down no saves the
same conversation happening twice.
