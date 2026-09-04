"""
Reading a Facebook rental advert well enough to pre-fill a form.

WHAT THIS IS FOR
----------------
Cold start. A browse page with four cars on it teaches the first fifty drivers
that the site is empty, and they do not come back to check. The answer is that
staff carry across the adverts already circulating in the groups, by hand, with
a link back to the original, until real owners are listing here themselves.

Doing that from scratch is about forty fields of typing per advert, which means
it stops happening at advert nine. This module reads a pasted advert and
guesses the structured fields so the human is correcting rather than
transcribing. Two minutes an advert instead of ten.

WHAT IT IS EXPLICITLY NOT FOR
-----------------------------
Automation. Nothing here writes a listing. `parse_advert` returns *initial form
data* that a member of staff then reads, fixes and submits, and every guess it
makes is one a human sees before anything is published. Group adverts are
written in shorthand, in three languages, by people typing on a phone — any
parser over them will be wrong regularly, and a wrong price on a listing is
worse than no listing at all.

The description is never pre-filled from the paste. That is the one field where
guessing would mean republishing somebody else's words, and the import policy
is a summary in our own words with a link to the original. The raw text sits
beside the form for the person to read, never inside it.
"""
import re
from datetime import date

from .models import Arrangement, FuelType, PaidBy, Transmission

# The cars that actually appear in these groups. A longer list would match more
# adverts and mis-match more of them; anything not here gets typed by hand.
KNOWN_MAKES = [
    "Toyota", "Volkswagen", "VW", "Suzuki", "Nissan", "Hyundai", "Kia",
    "Honda", "Ford", "Renault", "Datsun", "Chery", "Haval", "Mahindra",
    "Mazda", "Opel", "Isuzu", "Peugeot", "Citroen", "Proton", "Tata",
    "BMW", "Mercedes-Benz", "Mercedes", "Audi", "Chevrolet", "Fiat",
]

# Facebook says "VW"; the platforms and the licence disc say Volkswagen.
MAKE_ALIASES = {"VW": "Volkswagen", "MERCEDES": "Mercedes-Benz"}

_AMOUNT = r"(?:R|ZAR|USD|\$)?\s*(\d{1,3}(?:[\s,]\d{3})+|\d{3,6})"

_WEEKLY = re.compile(
    _AMOUNT + r"\s*(?:p\.?/?\s*w\b|per\s*week|a\s*week|weekly|/\s*week|pw\b)",
    re.IGNORECASE,
)
_DAILY = re.compile(
    _AMOUNT + r"\s*(?:p\.?/?\s*d\b|per\s*day|a\s*day|daily|/\s*day|pd\b)",
    re.IGNORECASE,
)
_DEPOSIT_AFTER = re.compile(r"deposit\D{0,12}" + _AMOUNT, re.IGNORECASE)
_DEPOSIT_BEFORE = re.compile(_AMOUNT + r"\s*deposit", re.IGNORECASE)
_SHARE = re.compile(r"(\d{2})\s*%")
_YEAR = re.compile(r"\b(199\d|20[0-4]\d)\b")

_PLATFORM_WORDS = {
    "uber": "uber",
    "bolt": "bolt",
    "indrive": "indrive",
    "in drive": "indrive",
    "didi": "didi",
}


def _to_int(raw):
    return int(re.sub(r"[\s,]", "", raw))


def _first(pattern, text):
    match = pattern.search(text)
    return _to_int(match.group(1)) if match else None


def parse_advert(text):
    """
    Best-effort structured reading of a pasted advert.

    Returns a dict shaped for `ImportedListingForm(initial=...)`. Keys are
    omitted rather than set to None when nothing was found, so the form's own
    defaults survive.
    """
    if not text:
        return {}

    lowered = text.lower()
    guess = {}

    # --- the car
    year = _year(text)
    if year:
        guess["year"] = year

    make, model = _make_and_model(text)
    if make:
        guess["make"] = make
    if model:
        guess["model"] = model

    if re.search(r"\bauto(?:matic)?\b", lowered):
        guess["transmission"] = Transmission.AUTO
    elif re.search(r"\bmanual\b", lowered):
        guess["transmission"] = Transmission.MANUAL

    if re.search(r"\bdiesel\b", lowered):
        guess["fuel_type"] = FuelType.DIESEL
    elif re.search(r"\bhybrid\b", lowered):
        guess["fuel_type"] = FuelType.HYBRID
    elif re.search(r"\belectric\b|\bev\b", lowered):
        guess["fuel_type"] = FuelType.ELECTRIC

    # --- money
    weekly = _first(_WEEKLY, text)
    daily = _first(_DAILY, text)
    deposit = _first(_DEPOSIT_AFTER, text)
    if deposit is None:
        deposit = _first(_DEPOSIT_BEFORE, text)

    if weekly:
        guess["weekly_rate"] = weekly
    if daily:
        guess["daily_rate"] = daily
    if deposit is not None:
        guess["deposit_amount"] = deposit

    share = _SHARE.search(text)
    if weekly:
        guess["arrangement"] = Arrangement.WEEKLY
    elif daily:
        guess["arrangement"] = Arrangement.DAILY
    elif share and re.search(r"\b(share|split|percentage|commission)\b", lowered):
        guess["arrangement"] = Arrangement.SHARE
        guess["earnings_share_pct"] = int(share.group(1))

    if re.search(r"rent\s*(?:to|2)\s*own", lowered):
        guess["arrangement"] = Arrangement.RENT2OWN

    # --- who pays what. Set only where the advert says so plainly; the model's
    # default is a better answer than a guess off an ambiguous sentence.
    if re.search(r"owner\s+(?:pays|covers|does)\s+(?:the\s+)?(?:fuel|petrol|diesel)", lowered):
        guess["fuel_paid_by"] = PaidBy.OWNER
    elif re.search(
        r"(?:driver|you)\s+(?:pays?|covers?|buys?)\s+(?:own\s+|your\s+own\s+)?(?:fuel|petrol|diesel)",
        lowered,
    ):
        guess["fuel_paid_by"] = PaidBy.DRIVER

    if re.search(
        r"(?:maintenance|service|servicing|repairs?)\s+(?:is\s+|are\s+)?"
        r"(?:covered|included|on\s+(?:the\s+)?owner)",
        lowered,
    ):
        guess["maintenance_paid_by"] = PaidBy.OWNER

    if re.search(r"(?:fully\s+)?insured|insurance\s+(?:is\s+)?(?:included|covered)", lowered):
        guess["has_insurance"] = True
    if "tracker" in lowered:
        guess["has_tracker"] = True

    # --- requirements
    if re.search(r"no\s+pr?dp|pr?dp\s+not\s+(?:required|needed)|without\s+pr?dp", lowered):
        guess["requires_prdp"] = False
    elif re.search(r"\bpr?dp\b", lowered):
        guess["requires_prdp"] = True

    experience = re.search(r"(\d)\s*(?:\+|or\s+more)?\s*years?\s+(?:of\s+)?experience", lowered)
    if experience:
        guess["min_experience_years"] = int(experience.group(1))

    platforms = [slug for word, slug in _PLATFORM_WORDS.items() if word in lowered]
    if platforms:
        guess["platform_slugs"] = sorted(set(platforms))

    return guess


def _year(text):
    """
    The model year, not the 2 500 in the price.

    Both look like four digits, so the pattern is bounded to 1990-2049 and we
    take the latest plausible match — an advert that mentions a service history
    usually names an older year first and the car's own year last.
    """
    years = [int(match) for match in _YEAR.findall(text)]
    plausible = [year for year in years if year <= date.today().year + 1]
    return max(plausible) if plausible else None


def _make_and_model(text):
    """
    Find a known make, then take the words after it as the model.

    Stops at a digit, a comma, a newline or a joining word, which is where the
    make/model phrase ends in practice: "Toyota Corolla Quest 1.6" gives
    "Corolla Quest".
    """
    stop_words = {
        "for", "available", "with", "auto", "automatic", "manual", "rental",
        "rent", "hire", "is", "in", "on", "to", "and", "or", "at",
    }
    for candidate in sorted(KNOWN_MAKES, key=len, reverse=True):
        match = re.search(rf"\b{re.escape(candidate)}\b", text, re.IGNORECASE)
        if not match:
            continue
        make = MAKE_ALIASES.get(candidate.upper(), candidate)
        tail = text[match.end():]
        model_match = re.match(r"[ \t]*([A-Za-z][A-Za-z \-]{1,40})", tail)
        model = ""
        if model_match:
            kept = []
            for word in model_match.group(1).split():
                if word.lower() in stop_words:
                    break
                kept.append(word)
            model = " ".join(kept[:3]).strip(" -")
        return make, model
    return None, ""
