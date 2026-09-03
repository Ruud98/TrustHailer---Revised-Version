from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render

from apps.feed.models import Post
from apps.listings.models import DriverListing, VehicleListing

# One search box, both sides of the marketplace. Anything shorter than this
# matches half the database and costs a full table scan to say so.
MIN_QUERY_LENGTH = 2

# Deliberately few. This page is a signpost, not a results page — its job is to
# work out which of the two browse pages the person actually wanted and send
# them there with their query intact.
PREVIEW_COUNT = 4


def home(request):
    """Landing page when logged out, the feed when logged in."""
    if request.user.is_authenticated:
        from apps.feed.views import feed

        return feed(request)
    return render(request, "pages/landing.html")


def healthz(request):
    """Liveness probe. Keep it cheap — no database call."""
    return JsonResponse({"status": "ok"})


def search(request):
    """
    Global search across cars and drivers.

    WHY THIS SHOWS A HANDFUL OF EACH RATHER THAN ONE MERGED LIST
    ------------------------------------------------------------
    A car and a driver are not comparable results, so ranking them against each
    other means inventing a score that says a 2019 Corolla beats a driver with
    six years on Bolt. Nobody searching wants that answer. Showing a few of
    each with a count and a way through to the real filter panel is both
    honest about what we found and one tap from the page that can narrow it.

    Posts joined in Sprint 7, as a third section rather than folded into either
    side — a scam warning is not competing with a Corolla for relevance either.

    ON `icontains`
    --------------
    Fine at launch scale and portable to the SQLite dev database. When the
    listing count makes it slow, the upgrade is Postgres full-text search
    (`SearchVector` on make/model/description and headline/about) behind a GIN
    index. Reach for that when it's actually slow, not before — a trigram index
    on a thousand rows buys nothing.
    """
    term = (request.GET.get("q") or "").strip()

    cars = VehicleListing.objects.none()
    drivers = DriverListing.objects.none()
    posts = Post.objects.none()
    car_count = driver_count = post_count = 0

    if len(term) >= MIN_QUERY_LENGTH:
        car_matches = (
            VehicleListing.objects.live()
            .hide_blocked(request.user)
            .filter(_car_query(term))
            .with_display_data()
            .ranked()
        )
        driver_matches = (
            DriverListing.objects.searchable()
            .hide_blocked(request.user)
            .filter(_driver_query(term))
            .distinct()
            .with_display_data()
            .ranked()
        )
        post_matches = (
            Post.objects.for_feed(request.user)
            .filter(body__icontains=term)
        )
        car_count = car_matches.count()
        driver_count = driver_matches.count()
        post_count = post_matches.count()
        cars = car_matches[:PREVIEW_COUNT]
        drivers = driver_matches[:PREVIEW_COUNT]
        posts = list(post_matches[:PREVIEW_COUNT])

    liked_posts = set()
    if posts and request.user.is_authenticated:
        from apps.feed.models import Like

        liked_posts = set(
            Like.objects.filter(user=request.user, post__in=posts)
            .values_list("post_id", flat=True)
        )

    return render(
        request,
        "pages/search.html",
        {
            "term": term,
            "too_short": bool(term) and len(term) < MIN_QUERY_LENGTH,
            "cars": cars,
            "drivers": drivers,
            "posts": posts,
            "liked_posts": liked_posts,
            "car_count": car_count,
            "driver_count": driver_count,
            "post_count": post_count,
            "total": car_count + driver_count + post_count,
        },
    )


def _car_query(term):
    return (
        Q(make__icontains=term)
        | Q(model__icontains=term)
        | Q(description__icontains=term)
        | Q(suburb__name__icontains=term)
    )


def _driver_query(term):
    return (
        Q(headline__icontains=term)
        | Q(about__icontains=term)
        | Q(driver__full_name__icontains=term)
        | Q(home_suburb__name__icontains=term)
        | Q(work_suburbs__name__icontains=term)
    )
