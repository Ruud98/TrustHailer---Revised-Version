"""
The names an anonymous post can wear.

WHY A FIXED VOCABULARY RATHER THAN A FREE TEXT BOX
--------------------------------------------------
A box somebody can type into is a box somebody types "anonymous_admin",
"anonymous_trusthailer" or a rival's name into, and every one of those is a
post that looks like it came from us or from them. Offering a short list and
accepting nothing outside it means the display name can never be a claim.

It is also why validation checks membership of this list rather than pattern
matching `anonymous_[a-z]+`: a pattern would accept `anonymous_staff` quite
happily.

WHY ANIMALS
-----------
They carry no status, no ethnicity, no gender and no seniority, which is more
than can be said for most name generators. The set is deliberately made of
animals this audience would recognise on sight; a list full of axolotls reads
as a joke from somewhere else.

THE NAME BELONGS TO THE POST, NOT THE PERSON
--------------------------------------------
See the note on `Post.anon_name`. Nothing here is per-user and nothing is
reserved: the same animal turning up twice is a feature, because a name that
follows somebody around is a pseudonym and a pseudonym can be correlated.
"""
import random

PREFIX = "anonymous_"

ANIMALS = (
    "aardvark", "baboon", "buffalo", "cheetah", "chihuahua", "crocodile",
    "dassie", "duiker", "eagle", "elephant", "flamingo", "gecko", "genet",
    "giraffe", "hippo", "hornbill", "hyena", "impala", "jackal", "kingfisher",
    "kudu", "leopard", "lion", "lizard", "meerkat", "mongoose", "ostrich",
    "otter", "pangolin", "porcupine", "rhino", "sable", "secretarybird",
    "springbok", "steenbok", "tortoise", "warthog", "waterbuck", "weaver",
    "wildebeest", "zebra",
)

# What a post wears when its author did not pick anything. Not an animal, so
# "chose not to choose" and "chose the leopard" stay distinguishable.
DEFAULT_LABEL = "Anonymous member"

# Enough to feel like a choice, few enough to pick from without reading a list.
OFFER_COUNT = 5


def name_for(animal):
    return f"{PREFIX}{animal}"


def offer(count=OFFER_COUNT):
    """
    `count` names to choose from, sampled without replacement.

    Regenerated on every render rather than held anywhere. There is nothing to
    reserve — two people offered the same animal at the same moment may both
    take it, and that is the intended behaviour, not a race to be locked.
    """
    return [name_for(animal) for animal in random.sample(ANIMALS, count)]


def is_valid(name):
    """True for a name this module could have produced, and nothing else."""
    if not name.startswith(PREFIX):
        return False
    return name[len(PREFIX):] in ANIMALS
