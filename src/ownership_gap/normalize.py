"""Name normalization shared by both screens.

Both screens see exactly the same normalized names, so a gap can never come from one side cleaning names
better than the other.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List

# Legal-form words stripped from the end of a name ("Complete Genomics, Inc." and "COMPLETE GENOMICS LLC" are
# the same core name). Only trailing words are stripped, so "Company" inside a name stays.
LEGAL_TAIL = {
    "inc",
    "incorporated",
    "llc",
    "l l c",
    "ltd",
    "limited",
    "co",
    "corp",
    "corporation",
    "company",
    "plc",
    "lp",
    "llp",
    "pllc",
    "pc",
    "gmbh",
    "ag",
    "sa",
    "nv",
    "bv",
    "srl",
    "spa",
    "sarl",
    "sas",
    "pte",
    "pty",
    "kk",
    "jsc",
    "pjsc",
    "ojsc",
    "cjsc",
    "ooo",
    "ao",
    "oao",
    "zao",
    "pao",
    "sl",
    "slu",
    "ab",
    "as",
    "oy",
    "holdings",
    "holding",
    "and",
}
# Leading words and legal forms that carry no identity ("JSC Sistema Finance", "The Aerosun Corporation").
LEADING = [
    "public joint stock company",
    "closed joint stock company",
    "open joint stock company",
    "joint stock company",
    "limited liability company",
    "the",
    "jsc",
    "pjsc",
    "ojsc",
    "cjsc",
    "ooo",
    "oao",
    "zao",
    "pao",
    "ao",
]
# Splits a payee field into the names it carries: "ACME INC DBA ROADRUNNER SUPPLY".
_ALIAS_SPLIT = re.compile(
    r"(?<![a-z0-9])(?:d\.?\s?/?\s?b\.?\s?/?\s?a\.?|a/k/a|aka|f\.?/?k\.?/?a\.?|t/a)(?![a-z0-9])", re.IGNORECASE
)
_PAREN = re.compile(r"\(([^()]*)\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fold(s: str) -> str:
    """Lower case, accents removed, '&' read as 'and', punctuation turned into spaces."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = s.replace("&", " and ").replace("'", "").replace(chr(0x2019), "")
    return " ".join(_NON_ALNUM.sub(" ", s).split())


def core(name: str) -> str:
    """The identity-bearing part of one organization name.

    >>> core("Complete Genomics, Inc.")
    'complete genomics'
    >>> core("COMPLETE GENOMICS LLC")
    'complete genomics'
    >>> core("The Aerosun Corporation")
    'aerosun'
    >>> core("Hangzhou Hikvision Digital Technology Co., Ltd.")
    'hangzhou hikvision digital technology'
    """
    s = fold(_PAREN.sub(" ", name))
    toks = s.split()
    stripped = True
    while stripped and len(toks) > 1:
        stripped = False
        for lead in LEADING:
            n = len(lead.split())
            if len(toks) > n and " ".join(toks[:n]) == lead:
                toks = toks[n:]
                stripped = True
                break
    changed = True
    while changed and len(toks) > 1:
        changed = False
        for n in (3, 2, 1):
            if len(toks) > n and " ".join(toks[-n:]) in LEGAL_TAIL:
                toks = toks[:-n]
                changed = True
                break
    # "co ltd" style tails leave "co" behind once "ltd" is gone; the loop above handles that.
    return " ".join(toks)


def variants(name: str, parentheticals: bool = False) -> List[str]:
    """Every core name a raw name field carries.

    A payee field can hold more than one name ("X DBA Y"). List names carry short names in parentheses
    ("Shenzhen DJI Technology Co., Ltd. (DJI)"); those are kept only when ``parentheticals`` is true, since in
    payee fields a parenthesis usually holds a city or an account note.

    >>> variants("ACME INC DBA ROADRUNNER SUPPLY LLC")
    ['acme', 'roadrunner supply']
    >>> variants("Aerospace CH UAV Co., Ltd. (CH UAV)", parentheticals=True)
    ['aerospace ch uav', 'ch uav']
    """
    out: List[str] = []
    head = ""
    m = _ALIAS_SPLIT.match(name.strip())
    if m:
        # The field starts with the marker ("DBA SYSTEMS INC"): there it is part of the name, not an alias marker.
        head, name = name.strip()[: m.end()], name.strip()[m.end() :]
    parts = _ALIAS_SPLIT.split(name)
    parts[0] = head + parts[0]
    if parentheticals:
        parts += _PAREN.findall(name)
    for p in parts:
        c = core(p)
        if c and c not in out:
            out.append(c)
    return out


def tokens(c: str) -> List[str]:
    return c.split()
