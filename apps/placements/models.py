"""
Placements and reviews — the part that makes a badge mean behaviour.

WHY THIS IS THE MOST IMPORTANT SPRINT
-------------------------------------
Everything before this verifies *identity*: that an email reaches somebody, that
a number rings, that an ID matched a name. None of it says whether the person
was any good to deal with. A driver picking between two owners with identical
green badges learns nothing from the badges.

A review is the first thing on this site that describes conduct. That makes it
the most valuable field in the database and the most attacked one, which is why
the rules below are strict and should stay strict.

NO FREE-FLOATING REVIEWS. EVER.
-------------------------------
A review exists only against a `Placement` both people confirmed. That single
rule kills the dominant abuse pattern on every classifieds site: accounts that
never transacted leaving reviews — bought praise, and competitors leaving
damage. If you ever find yourself adding a "review someone you dealt with
offline" button, you are rebuilding that problem.

The cost is real and worth paying: a deal arranged entirely over WhatsApp,
after finding each other here, cannot be reviewed unless somebody records the
placement. That is the trade. An unverifiable review is worth less than no
review, because it makes every other review on the site unverifiable too.
"""
import uuid
from datetime import timedelta

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.models import TimeStampedModel


class PlacementQuerySet(models.QuerySet):
    def confirmed(self):
        return self.filter(confirmed_by_owner=True, confirmed_by_driver=True)

    def involving(self, user):
        return self.filter(models.Q(owner=user) | models.Q(driver=user))

    def with_display_data(self):
        return self.select_related(
            "owner__profile", "driver__profile",
            "vehicle_listing__suburb__city",
        ).prefetch_related("reviews")


class Placement(TimeStampedModel):
    """
    A record that these two people actually did the deal.

    BOTH SIDES CONFIRM, AND ONLY THEN
    ---------------------------------
    One person asserting a placement is an assertion. Two people asserting it is
    a fact neither can quietly deny later, and it is the only thing standing
    between the review system and somebody inventing a working relationship in
    order to review a stranger. The side that creates it is confirmed by that
    act; the other side has to actually agree.

    IT MUST COME FROM AN APPROVED INTRODUCTION
    ------------------------------------------
    You can only record a placement with somebody you were introduced to
    through the site. That is a deliberate narrowing: it means every review in
    the database traces back to two people who each pressed a button agreeing
    to be put in touch, and it makes a fake review require a co-conspirator
    with a verified phone rather than a spare email address.
    """

    class EndReason(models.TextChoices):
        AGREED = "agreed", "We agreed to end it"
        DRIVER_LEFT = "driver_left", "The driver moved on"
        OWNER_ENDED = "owner_ended", "The owner ended it"
        PAYMENT = "payment", "Payment problems"
        VEHICLE = "vehicle", "Problems with the car"
        CONDUCT = "conduct", "Conduct"
        OTHER = "other", "Something else"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    vehicle_listing = models.ForeignKey(
        "listings.VehicleListing", on_delete=models.PROTECT, related_name="placements"
    )
    owner = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="placements_as_owner"
    )
    driver = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="placements_as_driver"
    )
    intro = models.ForeignKey(
        "intros.IntroRequest", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="placements",
    )

    started_on = models.DateField()
    ended_on = models.DateField(null=True, blank=True)
    end_reason = models.CharField(max_length=20, choices=EndReason.choices, blank=True)

    confirmed_by_owner = models.BooleanField(default=False)
    confirmed_by_driver = models.BooleanField(default=False)

    objects = PlacementQuerySet.as_manager()

    class Meta:
        ordering = ["-started_on", "-created_at"]
        constraints = [
            # One live record per pair per car. Re-hiring the same driver on the
            # same car later is a second placement, but only once the first has
            # an end date on it — otherwise a fortnight of confusion produces
            # two open placements and two review invitations for one job.
            models.UniqueConstraint(
                fields=["vehicle_listing", "driver"],
                condition=models.Q(ended_on__isnull=True),
                name="one_open_placement_per_driver_per_car",
            ),
            models.CheckConstraint(
                condition=~models.Q(owner=models.F("driver")),
                name="placement_needs_two_people",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "-started_on"]),
            models.Index(fields=["driver", "-started_on"]),
        ]

    def __str__(self):
        return f"{self.driver_id} driving {self.vehicle_listing_id} from {self.started_on}"

    def get_absolute_url(self):
        return reverse("placements:detail", args=[self.uuid])

    # ------------------------------------------------------------- reading

    @property
    def is_confirmed(self):
        return self.confirmed_by_owner and self.confirmed_by_driver

    @property
    def is_open(self):
        return self.ended_on is None

    def is_owner(self, user):
        return user.is_authenticated and user.pk == self.owner_id

    def other_party(self, user):
        return self.driver if self.is_owner(user) else self.owner

    def confirmed_by(self, user):
        return self.confirmed_by_owner if self.is_owner(user) else self.confirmed_by_driver

    def review_by(self, user):
        if not user.is_authenticated:
            return None
        return next((r for r in self.reviews.all() if r.author_id == user.pk), None)

    def can_review(self, user):
        """
        Reviewable once both sides agree it happened.

        Deliberately NOT gated on the placement having ended. Somebody three
        months into a working relationship has plenty to say and is more likely
        to say it than somebody remembering it a year later, and the review is
        dated so a reader can weigh it.
        """
        if not self.is_confirmed or not user.is_authenticated:
            return False
        if user.pk not in (self.owner_id, self.driver_id):
            return False
        return self.review_by(user) is None

    # ------------------------------------------------------------ writing

    def confirm(self, user):
        if self.is_owner(user):
            self.confirmed_by_owner = True
            field = "confirmed_by_owner"
        else:
            self.confirmed_by_driver = True
            field = "confirmed_by_driver"
        self.save(update_fields=[field, "updated_at"])

    def end(self, *, on, reason=""):
        self.ended_on = on
        self.end_reason = reason
        self.save(update_fields=["ended_on", "end_reason", "updated_at"])


class ReviewQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True)

    def due_for_publication(self):
        """
        Reviews the timer has caught up with.

        A review publishes when the other side has also written one — handled
        at write time — or when the wait has run out, which is this queryset.
        """
        cutoff = timezone.now() - timedelta(days=Review.BLIND_DAYS)
        return self.filter(is_published=False, created_at__lte=cutoff)


class Review(TimeStampedModel):
    """
    One person's account of working with another.

    DOUBLE-BLIND PUBLICATION, AND WHY IT IS NOT OPTIONAL
    ----------------------------------------------------
    Neither review appears until both are in, or 14 days have passed. Without
    it, everybody waits to see what the other person said before writing, and
    every review becomes a response to a review. Retaliation does not even have
    to happen for the damage to be done — the *fear* of it is enough to make
    people write nothing but bland praise, and within six months the data is
    worthless.

    The 14-day escape is what stops one silent party freezing the other's
    review forever.

    THE FIELDS DIFFER BY ROLE BECAUSE THE RISKS DIFFER
    ---------------------------------------------------
    An owner wants to know whether the driver paid on time and looked after the
    car. A driver wants to know whether the owner was fair, fixed things, and
    gave the deposit back. Asking both sides the same generic questions would
    throw away exactly the information each is looking for. `deposit_returned`
    is a boolean rather than a rating on purpose: it either came back or it did
    not, and it is the single most common thing that goes wrong.
    """

    BLIND_DAYS = 14
    RATING = dict(validators=[MinValueValidator(1), MaxValueValidator(5)])

    placement = models.ForeignKey(
        Placement, on_delete=models.CASCADE, related_name="reviews"
    )
    author = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="reviews_written"
    )
    subject = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="reviews_received"
    )

    overall = models.PositiveSmallIntegerField(**RATING)
    communication = models.PositiveSmallIntegerField(**RATING)

    # owner → driver
    payment_reliability = models.PositiveSmallIntegerField(null=True, blank=True, **RATING)
    vehicle_care = models.PositiveSmallIntegerField(null=True, blank=True, **RATING)

    # driver → owner
    fairness = models.PositiveSmallIntegerField(null=True, blank=True, **RATING)
    maintenance_response = models.PositiveSmallIntegerField(null=True, blank=True, **RATING)
    deposit_returned = models.BooleanField(null=True, blank=True)

    body = models.TextField(max_length=1500, blank=True)

    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)

    objects = ReviewQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["placement", "author"], name="one_review_per_person_per_placement"
            ),
            models.CheckConstraint(
                condition=~models.Q(author=models.F("subject")),
                name="cannot_review_yourself",
            ),
        ]
        indexes = [
            models.Index(fields=["subject", "is_published", "-published_at"]),
        ]

    def __str__(self):
        return f"{self.overall}★ for {self.subject_id} by {self.author_id}"

    @property
    def is_about_the_driver(self):
        return self.subject_id == self.placement.driver_id

    @property
    def publishes_at(self):
        return self.created_at + timedelta(days=self.BLIND_DAYS)

    def publish(self):
        """
        Make it visible, fold it into the subject's aggregate, and tell them.

        The notification lives here rather than only at the call site in the
        view, because publication has two triggers — the second review landing
        (handled live, in the view) and the 14-day timer running out (handled
        by the `publish_reviews` cron, with nobody watching). Put it here once
        and both paths get it for free, which matters more for this one than
        for most: the timer case is exactly the scenario where the recipient
        has no other way of finding out a review appeared.
        """
        if self.is_published:
            return
        self.is_published = True
        self.published_at = timezone.now()
        self.save(update_fields=["is_published", "published_at", "updated_at"])
        recalculate_rating(self.subject)

        from apps.notifications.models import Notification
        from apps.notifications.services import notify

        notify(
            recipient=self.subject,
            kind=Notification.Kind.REVIEW_PUBLISHED,
            message=f"{self.author.get_short_name() or 'Someone'} left you a "
                    f"{self.overall}★ review",
            url=self.placement.get_absolute_url(),
            actor=self.author,
        )


def publish_pair_if_ready(placement):
    """
    Publish both reviews the moment the second one lands.

    Called from the review form rather than left to the nightly job, because a
    person who has just written a review expects to see the other one — waiting
    until 2am for a result the rules say is already due reads as broken.
    """
    # Queried fresh rather than through `placement.reviews`: the placement was
    # very likely loaded with `with_display_data()`, whose prefetch cache still
    # holds the state from before the review that just triggered this call. Ask
    # the database, not the object.
    reviews = list(Review.objects.filter(placement=placement))
    if len(reviews) < 2:
        return []
    published = [review for review in reviews if not review.is_published]
    for review in published:
        review.publish()
    return published


def recalculate_rating(user):
    """
    Refresh a member's aggregate from their published reviews.

    Denormalised onto `Profile` rather than averaged per render: the rating
    appears on every card in a list, and a browse page that computes an average
    per card is a query per card. Recalculated whole rather than adjusted
    incrementally — it runs on publication, which is rare, and an incremental
    counter that drifts is worse than a slightly more expensive query nobody
    notices.
    """
    profile = user.profile
    aggregate = Review.objects.filter(subject=user, is_published=True).aggregate(
        average=models.Avg("overall"), total=models.Count("id")
    )
    profile.rating_avg = aggregate["average"]
    profile.rating_count = aggregate["total"] or 0
    profile.save(update_fields=["rating_avg", "rating_count", "updated_at"])
    return profile
