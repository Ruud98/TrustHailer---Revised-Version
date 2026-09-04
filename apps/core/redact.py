"""
Strip contact details out of text we did not write.

WHY THIS EXISTS
---------------
Imported adverts are typed by somebody else, in a Facebook group, and every
one of them ends with a phone number. Two separate reasons we must not
republish it:

1. POPIA. The person posting in a group consented to that group seeing their
   number. They did not consent to us putting it on a public website they have
   never heard of. Republishing it is processing personal information without
   a lawful basis, and "an admin pasted it by accident" is not a defence.

2. The whole introduction model. Contacts are released when both sides agree.
   A phone number sitting in a description is a hole straight through that,
   and the first one that appears teaches everybody to look there instead.

The importer already asks staff to summarise rather than paste. This is the
belt to that pair of braces — it runs on the way in, unconditionally, so a
tired admin at 11pm cannot publish a number by pasting one line too many.

DELIBERATELY BLUNT
------------------
It over-redacts before it under-redacts. A mangled sentence in an advert
summary is a cosmetic problem; a published phone number is a legal one.
"""
import re

PLACEHOLDER = "[contact removed]"

# Southern African numbers, anchored on a real dialling prefix rather than on
# "nine or more digits in a row". Anchoring is what stops "R2 500 deposit"
# being eaten as the front half of a phone number — the run has to begin with
# 0, +27/27 (South Africa) or +263/263 (Zimbabwe) to match at all.
_PHONE = re.compile(
    r"""
    (?<![\w])                       # not mid-word
    (?:\+?(?:27|263)|0)             # dialling prefix
    [\s.\-()]{0,2}
    \d
    (?:[\s.\-()]{0,2}\d){7,11}      # 8 to 12 more digits, separators allowed
    (?![\w])
    """,
    re.VERBOSE,
)

_EMAIL = re.compile(r"(?<![\w])[\w.+-]+@[\w-]+\.[\w.-]+(?![\w])")

# Messaging deep links. wa.me carries a number in the path, so the phone
# pattern alone would leave a working link behind with the digits blanked out.
_MESSAGING_LINK = re.compile(
    r"(?:https?://)?(?:www\.)?(?:wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com|t\.me|m\.me)/\S*",
    re.IGNORECASE,
)


def redact_contacts(text: str) -> str:
    """Return `text` with phone numbers, emails and chat links replaced."""
    if not text:
        return text
    cleaned = _MESSAGING_LINK.sub(PLACEHOLDER, text)
    cleaned = _EMAIL.sub(PLACEHOLDER, cleaned)
    cleaned = _PHONE.sub(PLACEHOLDER, cleaned)
    return cleaned


def contains_contact_details(text: str) -> bool:
    """True when `text` still carries something we would strip."""
    if not text:
        return False
    return bool(_MESSAGING_LINK.search(text) or _EMAIL.search(text) or _PHONE.search(text))
