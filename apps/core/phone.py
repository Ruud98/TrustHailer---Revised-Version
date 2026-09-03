"""
Phone number normalisation for South Africa and Zimbabwe.

Everything is stored in E.164 (+27821234567). Users type whatever they like.

This is deliberately hand-rolled rather than pulling in `phonenumbers`: we only
support two countries at launch, the rules are short, and the dependency is
several megabytes. If you add a third country, swap this module's internals for
`phonenumbers` and keep the same function signatures.
"""
import re

from django.core.exceptions import ValidationError

DIAL_CODES = {
    "ZA": "27",
    "ZW": "263",
}

# National significant number length (excluding leading 0 / dial code)
NSN_LENGTH = {
    "ZA": 9,
    "ZW": 9,
}

MOBILE_PREFIXES = {
    # first two digits of the NSN
    "ZA": {"60", "61", "62", "63", "64", "65", "66", "67", "68", "69",
           "71", "72", "73", "74", "76", "78", "79",
           "81", "82", "83", "84"},
    # Econet 77/78, NetOne 71, Telecel 73
    "ZW": {"71", "73", "77", "78"},
}

_NON_DIGIT = re.compile(r"[^\d+]")


class PhoneError(ValidationError):
    pass


def normalise(raw: str, default_country: str = "ZA", *, mobile_only: bool = True) -> str:
    """
    Return an E.164 string, or raise PhoneError.

    Accepts: 082 123 4567 · 0821234567 · +27 82 123 4567 · 0027821234567

    `mobile_only` defaults True because every existing call site is about a
    number we send an OTP to, and a landline cannot receive one — that is the
    reason the check exists at all, and the default error message says so.
    Pass `mobile_only=False` for a number that is only ever going to be
    *called*, such as a business directory listing's phone field, where a
    shop's landline is not a mistake.
    """
    if not raw:
        raise PhoneError("Enter a phone number.")

    cleaned = _NON_DIGIT.sub("", str(raw).strip())
    if not cleaned:
        raise PhoneError("Enter a valid phone number.")

    # 0027... -> +27...
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if cleaned.startswith("+"):
        digits = cleaned[1:]
        country = _country_for_dial_code(digits)
        if country is None:
            raise PhoneError("We currently support South African and Zimbabwean numbers only.")
        nsn = digits[len(DIAL_CODES[country]):]
    else:
        country = (default_country or "ZA").upper()
        if country not in DIAL_CODES:
            raise PhoneError("Unsupported country.")
        nsn = cleaned.lstrip("0")

    _validate_nsn(nsn, country, mobile_only=mobile_only)
    return f"+{DIAL_CODES[country]}{nsn}"


def _country_for_dial_code(digits: str):
    # Longest dial code first, so 263 is tested before 27 can mis-match.
    for country, code in sorted(DIAL_CODES.items(), key=lambda kv: -len(kv[1])):
        if digits.startswith(code):
            return country
    return None


def _validate_nsn(nsn: str, country: str, *, mobile_only: bool = True) -> None:
    expected = NSN_LENGTH[country]
    if len(nsn) != expected:
        raise PhoneError(
            f"That number doesn't look right — we expected {expected} digits after the "
            f"country code."
        )
    if mobile_only and nsn[:2] not in MOBILE_PREFIXES[country]:
        raise PhoneError("Please enter a mobile number — we need to send you an SMS code.")


def country_of(e164: str) -> str | None:
    if not e164 or not e164.startswith("+"):
        return None
    return _country_for_dial_code(e164[1:])


def display(e164: str) -> str:
    """+27821234567 -> 082 123 4567 (national format, easier to read aloud)."""
    country = country_of(e164)
    if not country:
        return e164 or ""
    nsn = e164[1 + len(DIAL_CODES[country]):]
    return f"0{nsn[:2]} {nsn[2:5]} {nsn[5:]}"


def mask(e164: str) -> str:
    """+27821234567 -> 082 *** 4567. Used before the number is unlocked."""
    country = country_of(e164)
    if not country:
        return "***"
    nsn = e164[1 + len(DIAL_CODES[country]):]
    return f"0{nsn[:2]} *** {nsn[-4:]}"


def whatsapp_link(e164: str, text: str = "") -> str:
    from urllib.parse import quote

    number = (e164 or "").lstrip("+")
    base = f"https://wa.me/{number}"
    return f"{base}?text={quote(text)}" if text else base
