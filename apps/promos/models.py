"""
The side rails — the two columns of promotional material that flank the main
column on a wide screen.

WHY THIS EXISTS AT ALL
----------------------
Every page on this site is one 640px column, which is the right answer on the
phone most members use and leaves a desktop browser two-thirds empty. Empty
space on a marketplace reads as an empty marketplace. The rails put the things
we would otherwise have to interrupt the feed with — what the product does,
why the badges mean something, what members have said, which businesses are
in the directory — beside the column instead of inside it.

ONE RAIL, ON THE LEFT
---------------------
Promos occupy the left rail only. The right one is navigation — see
templates/partials/_sidenav.html. Two rails of moving adverts either side of
the column read as a page covered in adverts, which is what the first version
of this looked like and why it changed.

THE PROMO RAIL IS FURNITURE, NOT CONTENT
----------------------------------------
It is hidden below 1280px and it never carries anything a member needs. If a
thing has to be read, it belongs in the column. This matters more than it
sounds: the day somebody puts the only link to a real feature in here, the
entire mobile userbase loses it silently. (The nav rail opposite is the other
way round — it is real navigation, so it is focusable and in the
accessibility tree.)

TWO SLOTS, DELIBERATELY DIFFERENT
---------------------------------
`Slot.AD` is the box at the top: one item at a time, swapped with a visible
animation, and the slot a paid advert or a Google ad unit will eventually
occupy. It is meant to catch the eye once.

`Slot.RAIL` is the carousel underneath: a slow, continuous left-to-right
crawl of images that is meant to be *ignorable*. Two attention-grabbing
elements stacked on top of each other cancel each other out, so the carousel
moves steadily and the ad box is the only thing that ever surprises you.

THE THIRD PARTY SLOT
--------------------
Google AdSense (or any ad network) renders itself from a script tag; it has no
image or title for us to store. When that switches on, the markup goes in
`templates/promos/_adslot_network.html` and `services.rail()` grows a flag
that returns a network slot instead of a `Promo`. Nothing in this model needs
to change for that, which is the point of keeping the two slots separate.

BUSINESS LISTINGS ARE PULLED IN LIVE, NOT COPIED
------------------------------------------------
A directory listing already has a name, a logo, a suburb and a URL — every
field a carousel slide needs. `services.rail()` wraps live `BusinessListing`
rows rather than asking staff to re-type them into a `Promo`, so a business
that pauses or gets hidden leaves the rail the same moment it leaves the
directory. There is no second copy to go stale.
"""
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class PromoQuerySet(models.QuerySet):
    def live(self):
        """
        Active, and inside its window if it has one.

        Both date fields are optional and mean "no bound on this end", so an
        evergreen slide is the default and a campaign is the special case.
        """
        now = timezone.now()
        return self.filter(
            models.Q(is_active=True)
            & (models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now))
            & (models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now))
        )

    def for_slot(self, slot):
        return self.filter(slot=slot)


class Promo(TimeStampedModel):
    """
    One item in a side rail: an ad box card, or a carousel slide.

    One model rather than two because the fields are the same set — image,
    heading, a line of body, somewhere to go — and the difference is entirely
    in how it is rendered. Keeping them together means a slide that starts
    pulling its weight can be promoted to the ad box by changing one dropdown.
    """

    class Slot(models.TextChoices):
        AD = "ad", "Ad box (top of the rail)"
        RAIL = "rail", "Carousel (under the ad box)"

    class Kind(models.TextChoices):
        """
        Purely editorial — it sets the little label on the card and lets staff
        filter the list. It carries no behaviour, so adding one later is a
        migration and nothing else.
        """
        FEATURE = "feature", "Feature"
        BENEFIT = "benefit", "Benefit"
        TESTIMONIAL = "testimonial", "Testimonial"
        BUSINESS = "business", "Business advert"
        NOTICE = "notice", "Notice"

    class Animation(models.TextChoices):
        """
        How an ad box card arrives when it takes its turn.

        Stored per promo rather than picked at random in the browser so that a
        card can be written to suit its entrance — a one-word headline reads
        well flipping in, a three-line testimonial does not. `SHUFFLE` is the
        escape hatch for staff who do not want to think about it.
        """
        SHUFFLE = "shuffle", "Surprise me"
        FADE = "fade", "Fade through"
        SLIDE = "slide", "Slide up"
        ZOOM = "zoom", "Zoom in"
        FLIP = "flip", "Flip"
        SWIPE = "swipe", "Swipe across"

    slot = models.CharField(max_length=6, choices=Slot.choices, default=Slot.RAIL)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.FEATURE)
    animation = models.CharField(
        max_length=10, choices=Animation.choices, default=Animation.SHUFFLE,
        help_text="Ad box only. Carousel slides always crawl sideways.",
    )

    title = models.CharField(max_length=80)
    body = models.CharField(
        max_length=180, blank=True,
        help_text="One sentence. A rail is read out of the corner of an eye.",
    )
    # Slides read as a stack of pictures and mostly want one; ad box cards work
    # either way. Blank is allowed for both so a text-only notice can go up in
    # a minute without somebody having to find artwork first.
    image = models.ImageField(upload_to="promos/", blank=True)
    image_alt = models.CharField(
        max_length=140, blank=True,
        help_text="What the picture shows. Leave blank if it is decoration.",
    )

    url = models.CharField(
        max_length=300, blank=True,
        help_text="Where the card goes when clicked. Site path or full URL. "
                  "Blank makes it a non-clickable card.",
    )
    cta_label = models.CharField(max_length=40, blank=True)

    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    # Low sorts first. Left at 100 by default so there is room to push
    # something to the front without renumbering everything behind it.
    sort_order = models.PositiveSmallIntegerField(default=100)

    objects = PromoQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "-created_at"]
        indexes = [
            models.Index(fields=["slot", "is_active", "sort_order"]),
        ]

    def __str__(self):
        return f"{self.get_slot_display()} — {self.title}"

    @property
    def is_external(self):
        return self.url.startswith(("http://", "https://"))
