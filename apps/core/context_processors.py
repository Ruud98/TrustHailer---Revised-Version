"""
Values every page can rely on.
"""
from django.conf import settings

RAIL_ITEMS = 3


def site(request):
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_TAGLINE": settings.SITE_TAGLINE,
        "SUPPORT_WHATSAPP": settings.SUPPORT_WHATSAPP,
    }


def rails(request):
    """
    The activity shown in the side rails, and on the landing page.

    WHY THIS IS A CONTEXT PROCESSOR AND NOT A VIEW'S JOB
    ----------------------------------------------------
    The rails sit in the app shell, so they appear beside every page. Passing
    the data in from each view would mean every new view remembering to, and
    the first one that forgot would render a rail that is silently empty —
    exactly the "this site is dead" impression the rails exist to fix. One
    place, always populated, is the trade worth making.

    THE COST, AND WHY THE TOTALS ARE NOT CACHED
    -------------------------------------------
    Four small queries per request: two `LIMIT 3` reads on an indexed ordering
    and two counts, in the same spirit as the unread badge next door.

    The totals were cached for five minutes at first, and it was wrong within a
    minute of trying it: the rail read "0 cars listed" directly above three
    cars. A number that contradicts the list beside it is worse than no number,
    because the whole job of this panel is to be evidence — so the count is
    read fresh and the two always agree. Revisit when a COUNT here actually
    shows up in a profile, not before.

    Blocked members are filtered per viewer, which is the other reason none of
    this is shared between users — a rail is a smaller surface than the browse
    page, but it is still a way to put someone in front of a person who
    blocked them.
    """
    # Imported here rather than at module scope: `apps.core` is imported by the
    # listings models themselves, so a top-level import would close the loop.
    from apps.listings.models import DriverListing, VehicleListing

    cars = (
        VehicleListing.objects.live()
        .hide_blocked(request.user)
        .with_display_data()
        .order_by("-created_at")[:RAIL_ITEMS]
    )
    drivers = (
        DriverListing.objects.searchable()
        .hide_blocked(request.user)
        .with_display_data()
        .order_by("-created_at")[:RAIL_ITEMS]
    )

    return {
        "rail_cars": cars,
        "rail_drivers": drivers,
        "rail_total_cars": VehicleListing.objects.live().count(),
        "rail_total_drivers": DriverListing.objects.searchable().count(),
    }
