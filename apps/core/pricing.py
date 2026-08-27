"""
One place that answers "what does this cost?"

WHY THIS EXISTS WHILE EVERYTHING IS FREE
----------------------------------------
The platform launches free. The mistake would be to build it as though money
will never appear, then bolt payments on eighteen months later — that means a
data migration, a pricing page written under pressure, and users who feel
ambushed.

Instead every chargeable action already routes through this module and already
writes a ledger entry, at a price of zero. Turning on monetisation becomes a
settings change plus a payment provider, not a rebuild. The introduction that
cost 0 credits in month three and 2 credits in month twelve is the same code
path, and the ledger shows the whole history either way.

WHAT NEVER GETS A PRICE
-----------------------
Drivers. Ever. The driver side is the growth engine and the cash-poor side of
this market; taxing it is what killed the closest comparable business overseas.
`price_for()` returns zero for a driver no matter what the flags say, and there
is a test that holds that line.
"""
from dataclasses import dataclass

from django.conf import settings


class Action:
    INTRO_REQUEST = "intro_request"
    INTRO_APPROVE = "intro_approve"
    LISTING_BOOST = "listing_boost"
    VETTING_REPORT = "vetting_report"
    BUSINESS_LISTING = "business_listing"


# Prices in credits, used only once MONETISATION_ENABLED is True.
FUTURE_PRICES = {
    Action.INTRO_APPROVE: 2,
    Action.LISTING_BOOST: 5,
    Action.VETTING_REPORT: 10,
    Action.BUSINESS_LISTING: 20,
}


@dataclass(frozen=True)
class Price:
    credits: int
    is_free: bool
    reason: str

    @property
    def is_chargeable(self) -> bool:
        return self.credits > 0


FREE_LAUNCH = Price(0, True, "Free while we're getting started.")
FREE_FOR_DRIVERS = Price(0, True, "Always free for drivers.")


def price_for(action: str, *, user=None) -> Price:
    """
    What this action costs this user, right now.

    Call this instead of hardcoding a number anywhere. Every caller then keeps
    working unchanged when pricing switches on.
    """
    # Drivers never pay, regardless of any flag. This is a product commitment,
    # not a launch promotion.
    if user is not None and _is_driver_only(user):
        return FREE_FOR_DRIVERS

    if not getattr(settings, "MONETISATION_ENABLED", False):
        return FREE_LAUNCH

    credits = FUTURE_PRICES.get(action, 0)
    if credits == 0:
        return FREE_LAUNCH
    return Price(credits, False, "")


def _is_driver_only(user) -> bool:
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return profile.is_driver and not profile.is_owner and not profile.is_business


def launch_notice() -> str:
    """
    Copy for the pricing page and anywhere a price would otherwise appear.

    Say "free while we're building", never "free forever" for the owner side.
    Setting the expectation now is what stops the switch to paid feeling like a
    betrayal later — and users who were told the truth up front churn far less
    than users who feel tricked.
    """
    if getattr(settings, "MONETISATION_ENABLED", False):
        return ""
    return (
        f"{settings.SITE_NAME} is free while we're building it out. "
        "Drivers will always be free. When we do start charging car owners, "
        "we'll tell you well before it happens."
    )
