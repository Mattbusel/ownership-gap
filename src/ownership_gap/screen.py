"""Two screens over the same vendor names, side by side.

1. Name screen: what a name-only OFAC/CSL screen sees. A vendor is a hit when one of its core names equals, or
   scores at least ``threshold`` against, a name or alias on the Consolidated Screening List.
2. Ownership and control screen: what the rules reach that the name screen cannot see.
   - ``listed``: the vendor is on a list the CSL does not carry (DoD 1260H with every subsidiary DoD names,
     Section 889, FCC Covered List, UFLPA, BIOSECURE).
   - ``owned``: the vendor is owned or controlled by a listed party (OFAC 50 Percent Rule, BIS Affiliates Rule,
     50 percent by a 1260H/889/FCC/UFLPA/BIOSECURE party), from GLEIF ownership links, with the proof path.
   - ``brand``: the vendor's legal name carries the brand of a listed parent (HIKVISION USA, BYD COACH & BUS).
     Evidence that it belongs to the listed group, to be confirmed against corporate records.

A gap is a vendor the name screen clears and the ownership and control screen catches. Every gap is
``confirmed`` (an exact or near-exact name match to a listed entity, or an ownership chain the rule covers) or
``review`` (a fuzzy match, a brand in the name, or control without a known 50 percent share).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import data as _data
from .match import Hit, NameIndex, build
from .normalize import core, fold, variants

DEFAULT_THRESHOLD = 90.0
# The rules side is held to a stricter bar than the name screen, so every gap is a name the name screen had
# every chance to catch: a fuzzy rules-side match needs this score and the same first word, and is never more
# than ``review``.
CONTROL_THRESHOLD = 95.0
# An exact match on a short name ("BYD", "MGI", "BGI") stays ``review``: plenty of unrelated US firms share
# three letters.
SHORT_CORE = 4

# Listed parents whose brand appears in the legal names of their own US and foreign subsidiaries. Each maps to
# the name of the listed parent record. Short brands (3 letters) count only as the first word of the vendor
# name followed by corporate words ("ZTE USA INC", "BYD COACH AND BUS LLC"), never inside other names.
BRANDS: Dict[str, str] = {
    "hikvision": "Hangzhou Hikvision Digital Technology Co., Ltd.",
    "dahua": "Zhejiang Dahua Technology Co., Ltd.",
    "huawei": "Huawei Technologies Co., Ltd.",
    "zte": "ZTE Corporation",
    "hytera": "Hytera Communications Corporation",
    "kaspersky": "AO Kaspersky Lab",
    "dji": "Shenzhen DJI Technology Co., Ltd.",
    "autel": "Autel Intelligent Technology Corp., Ltd.",
    "autel robotics": "Autel Robotics Co., Ltd.",
    "byd": "BYD Company Limited",
    "crrc": "CRRC Corporation Limited",
    "catl": "Contemporary Amperex Technology Co., Ltd.",
    "contemporary amperex": "Contemporary Amperex Technology Co., Ltd.",
    "tp link": "TP-Link Technologies Co., Ltd.",
    "quectel": "Quectel Wireless Solutions Co., Ltd.",
    "hesai": "Hesai Group",
    "unitree": "Hangzhou Yushu Technology Co., Ltd.",
    "novogene": "Novogene Company Limited",
    "tencent": "Tencent Holdings Limited",
    "alibaba": "Alibaba Group Holding Limited",
    "baidu": "Baidu, Inc.",
    "inspur": "Inspur Group Co., Ltd.",
    "sensetime": "SenseTime Group, Inc.",
    "cloudwalk": "CloudWalk Technology Co., Ltd.",
    "netposa": "NetPosa Technologies, Ltd.",
    "baicells": "Baicells Technologies Co., Ltd.",
    "jouav": "Chengdu JOUAV Automation Tech Co., Ltd.",
    "sinotrans": "Sinotrans & CSC Holdings Co., Ltd.",
    "cosco shipping": "China COSCO SHIPPING Corporation Limited",
    "zpmc": "ZPMC North America, Inc.",
    "comac": "Commercial Aircraft Corporation of China Limited",
    "avic": "Aviation Industry Corporation of China, Ltd.",
    "norinco": "China North Industries Group Corporation Limited",
    "cnooc": "China National Offshore Oil Corporation",
    "smic": "Semiconductor Manufacturing International Corporation",
    "ymtc": "Yangtze Memory Technologies Co., Ltd.",
    "cxmt": "ChangXin Memory Technologies, Inc.",
    "innolight": "Zhongji Innolight Co., Ltd.",
    "trina solar": "Trina Solar Co., Ltd.",
    "ja solar": "JA Solar Technology Co., Ltd.",
    "eve energy": "EVE Energy Co., Ltd.",
    "bgi": "BGI Group",
    "mgi": "MGI Tech Co., Ltd.",
    "boe": "BOE Technology Group Co., Ltd.",
    "nio": "NIO, Inc.",
    "china mobile": "China Mobile Communications Group Co., Ltd.",
    "china telecom": "China Telecom Group Co., Ltd.",
    "china unicom": "China United Network Communications Group Co., Ltd.",
}
CORP_WORDS = {
    "usa",
    "us",
    "america",
    "americas",
    "north",
    "na",
    "international",
    "intl",
    "tech",
    "technology",
    "technologies",
    "motors",
    "motor",
    "coach",
    "and",
    "bus",
    "genomics",
    "enterprise",
    "enterprises",
    "energy",
    "digital",
    "global",
    "group",
    "systems",
    "electronics",
    "robotics",
    "sales",
    "service",
    "services",
}


@dataclass
class ControlHit:
    kind: str  # listed | owned | brand
    lists: List[str]  # data sources (US_DOD_1260H, ...) or rules (ofac-50, ...)
    matched: str
    score: float
    strength: str  # confirmed | review
    chain: List[str]
    sources: List[str]
    effects: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "lists": self.lists,
            "matched": self.matched,
            "score": self.score,
            "strength": self.strength,
            "chain": self.chain,
            "sources": self.sources,
            "effects": self.effects,
        }


@dataclass
class Row:
    vendor: str
    lines: int = 0
    amount: Optional[float] = None
    name_hits: List[Dict[str, Any]] = field(default_factory=list)
    name_closest: Optional[Dict[str, Any]] = None
    control: List[ControlHit] = field(default_factory=list)

    @property
    def name_status(self) -> str:
        return "hit" if self.name_hits else "clear"

    @property
    def control_status(self) -> str:
        return "hit" if self.control else "clear"

    @property
    def gap(self) -> Optional[str]:
        """``confirmed`` or ``review`` when the name screen clears what the rules catch, else None."""
        if self.name_hits or not self.control:
            return None
        return "confirmed" if any(h.strength == "confirmed" for h in self.control) else "review"

    def to_json(self) -> Dict[str, Any]:
        return {
            "vendor": self.vendor,
            "lines": self.lines,
            "amount": self.amount,
            "name_screen": {"status": self.name_status, "hits": self.name_hits, "closest": self.name_closest},
            "ownership_control": {"status": self.control_status, "hits": [h.to_json() for h in self.control]},
            "gap": self.gap,
        }


def _list_label(ds: str) -> str:
    return _data.LIST_RULES.get(ds, {}).get("label", ds)


class Screener:
    """Build once per dataset, then screen any number of vendor names."""

    def __init__(
        self, ds: _data.Dataset, threshold: float = DEFAULT_THRESHOLD, control_threshold: float = CONTROL_THRESHOLD
    ) -> None:
        self.ds = ds
        self.threshold = threshold
        self.control_threshold = max(control_threshold, threshold)
        self._csl: NameIndex[_data.CslRecord] = build(
            (c, r) for r in ds.csl for n in r.names for c in variants(n, parentheticals=False)
        )
        self._lists: NameIndex[_data.ListRecord] = build(
            (c, r) for r in ds.lists for n in r.names for c in variants(n, parentheticals=True)
        )
        self._aff: NameIndex[_data.AffiliateRecord] = build(
            (c, r) for r in ds.affiliates for n in r.names for c in variants(n, parentheticals=False)
        )
        # Records by core name, to resolve a parent name or a brand to its list records.
        self._by_core: Dict[str, List[_data.ListRecord]] = {}
        for r in ds.lists:
            for n in r.names:
                for c in variants(n, parentheticals=True):
                    self._by_core.setdefault(c, []).append(r)
        self._brands: List[Tuple[List[str], str]] = sorted(
            ((b.split(), parent) for b, parent in BRANDS.items()), key=lambda x: -len(x[0])
        )

    # -- name screen ---------------------------------------------------------------------------------------
    def name_screen(self, names: Sequence[str]) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        hits, closest = self._csl.best(names, self.threshold)
        out, seen = [], set()
        for h in hits:
            key = (h.payload.name, h.payload.source)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {"name": h.payload.name, "list": h.payload.source, "programs": h.payload.programs, "score": h.score}
            )
        cl = None
        if closest is not None:
            cl = {"name": closest.payload.name, "list": closest.payload.source, "score": closest.score}
        return out, cl

    # -- ownership and control screen ----------------------------------------------------------------------
    def _records_for(self, name: str) -> List[_data.ListRecord]:
        found: List[_data.ListRecord] = []
        for c in variants(name, parentheticals=True):
            for r in self._by_core.get(c, []):
                if r not in found:
                    found.append(r)
        return found

    def _listed_hits(self, vendor: str, names: Sequence[str]) -> List[ControlHit]:
        hits, _ = self._lists.best(names, self.control_threshold, same_first=True)
        groups: Dict[str, List[Hit[_data.ListRecord]]] = {}
        for h in hits:
            groups.setdefault(core(h.payload.name), []).append(h)
        out = []
        for _, hs in groups.items():
            best = max(hs, key=lambda h: h.score)
            recs = [h.payload for h in hs]
            lists = sorted({r.data_source for r in recs})
            r0 = best.payload
            chain = [f"{vendor} (vendor)", _match_step(vendor, r0, best)]
            parents = sorted({r.parent for r in recs if r.parent})
            for p in parents:
                prec = self._records_for(p)
                plists = sorted({x.data_source for x in prec})
                chain.append(
                    f"named by the list as a subsidiary of {p}"
                    + (f" ({', '.join(_list_label(x) for x in plists)})" if plists else "")
                )
            for r in recs:
                when = f", listed {r.listed_on}" if r.listed_on else ""
                doc = f" (FR {r.source_doc})" if r.source_doc and r.data_source == "US_DOD_1260H" else ""
                chain.append(f"{_list_label(r.data_source)}{when}{doc}")
            chain = list(dict.fromkeys(chain))
            out.append(
                ControlHit(
                    kind="listed",
                    lists=lists,
                    matched=r0.name,
                    score=best.score,
                    strength=_strength(best),
                    chain=chain,
                    sources=sorted({r.source_url for r in recs if r.source_url}),
                    effects=[_data.LIST_RULES[x]["effect"] for x in lists if x in _data.LIST_RULES],
                )
            )
        return out

    def _owned_hits(self, vendor: str, names: Sequence[str]) -> List[ControlHit]:
        hits, _ = self._aff.best(names, self.control_threshold, same_first=True)
        out, seen = [], set()
        for h in hits:
            a = h.payload
            if a.record_id in seen:
                continue
            seen.add(a.record_id)
            rules = sorted(a.verdicts)
            covered = [r for r in rules if a.verdicts[r] in ("definitely", "possibly")]
            chain = [f"{vendor} (vendor)", f"= {a.name} ({a.record_id}, {a.country or '??'}, name match {h.score:g})"]
            for rule in rules:
                chain.append(f"{_data.AFFILIATE_RULES.get(rule, {}).get('label', rule)}: {a.verdicts[rule]}")
            chain += _proof_path(
                (a.proof.get(covered[0] if covered else rules[0]) or {}) if rules else {}, {a.record_id: a.name}
            )
            status = self.ds.rule_status
            effects = []
            for rule in rules:
                e = _data.AFFILIATE_RULES.get(rule, {}).get("effect")
                st = (status.get(rule) or {}).get("status")
                if e:
                    effects.append(e + (f" Rule status: {st}." if st else ""))
            strength = _strength(h) if covered else "review"
            out.append(
                ControlHit(
                    kind="owned",
                    lists=rules,
                    matched=a.name,
                    score=h.score,
                    strength=strength,
                    chain=chain,
                    sources=[_data.AFFILIATE_RULES[r]["cite"] for r in rules if r in _data.AFFILIATE_RULES]
                    + ["https://search.gleif.org/#/record/" + a.record_id.split(":", 1)[-1]],
                    effects=effects,
                )
            )
        return out

    def _brand_hits(self, vendor: str, names: Sequence[str], already: Iterable[str]) -> List[ControlHit]:
        out = []
        done = set(already)
        for n in names:
            toks = n.split()
            for btoks, parent in self._brands:
                if parent in done:
                    continue
                if not _brand_in(toks, btoks):
                    continue
                recs = self._records_for(parent)
                if not recs:
                    continue
                lists = sorted({r.data_source for r in recs})
                chain = [
                    f"{vendor} (vendor)",
                    f'legal name carries the brand "{" ".join(btoks).upper()}" of {parent}',
                    f"{parent}: " + ", ".join(_list_label(x) for x in lists),
                    "ownership of this vendor not yet confirmed from corporate records",
                ]
                done.add(parent)
                out.append(
                    ControlHit(
                        kind="brand",
                        lists=lists,
                        matched=parent,
                        score=0.0,
                        strength="review",
                        chain=chain,
                        sources=sorted({r.source_url for r in recs if r.source_url}),
                        effects=[_data.LIST_RULES[x]["effect"] for x in lists if x in _data.LIST_RULES],
                    )
                )
        return out

    def screen(self, vendor: str) -> Row:
        names = variants(vendor)
        row = Row(vendor=vendor)
        row.name_hits, row.name_closest = self.name_screen(names)
        listed = self._listed_hits(vendor, names)
        owned = self._owned_hits(vendor, names)
        matched_parents = set()
        for h in listed:
            matched_parents.add(h.matched)
            for r in self._records_for(h.matched):
                if r.parent:
                    matched_parents.add(r.parent)
        brand = self._brand_hits(vendor, names, matched_parents)
        row.control = listed + owned + brand
        return row


def _match_step(vendor: str, rec: _data.ListRecord, h: Hit[Any]) -> str:
    alias = next((n for n in rec.names if h.matched in variants(n, parentheticals=True)), rec.name)
    via = "" if alias == rec.name else f' by its listed name "{alias}"'
    if not h.exact:
        return f"name scores {h.score:g} against {rec.name}{via}"
    if fold(vendor) == fold(alias):
        return f"is {rec.name}" if not via else f"is {rec.name}{via}"
    return f"same name as {rec.name}{via}, legal form aside"


def _strength(h: Hit[Any]) -> str:
    return "confirmed" if h.exact and len(h.matched) > SHORT_CORE else "review"


def _brand_in(toks: List[str], btoks: List[str]) -> bool:
    n = len(btoks)
    if n == 1 and len(btoks[0]) <= 3:
        return toks[:1] == btoks and all(t in CORP_WORDS for t in toks[1:])
    return any(toks[i : i + n] == btoks for i in range(len(toks) - n + 1))


def _proof_path(p: Dict[str, Any], known: Optional[Dict[str, str]] = None) -> List[str]:
    """Turn an ownership-rules proof into readable steps, listed party first."""
    steps = p.get("proof") or []
    names = dict(known or {})
    for s in steps:
        if s.get("name"):
            names[s["entity"]] = s["name"]
    out = []
    for s in sorted(steps, key=lambda s: s.get("round", 0)):
        if s.get("verdict") == "listed":
            tags = ", ".join((s.get("lists") or []) + (s.get("programs") or []))
            out.append(f"{s.get('name') or s.get('entity')} [{tags}] (listed party)")
            continue
        for v in s.get("via") or []:
            pct = v.get("pct")
            share = f"{pct:g}%" if isinstance(pct, (int, float)) else "share unknown"
            ctl = ", controls" if v.get("control") else ""
            src = v.get("source") or ""
            asof = f" as of {v['as_of']}" if v.get("as_of") else ""
            owner = names.get(v.get("owner"), v.get("owner"))
            ent = s.get("entity")
            label = f"{names[ent]} ({ent})" if ent in names else str(ent)
            out.append(f"{owner} owns {share}{ctl} of {label} ({src}{asof})")
    return out


@dataclass
class Report:
    rows: List[Row]
    as_of: Dict[str, str]
    threshold: float
    unscreened: int = 0

    @property
    def gaps(self) -> List[Row]:
        return [r for r in self.rows if r.gap]

    def counts(self) -> Dict[str, int]:
        return {
            "vendors": len(self.rows),
            "name_screen_hits": sum(1 for r in self.rows if r.name_hits),
            "ownership_control_hits": sum(1 for r in self.rows if r.control),
            "gaps": len(self.gaps),
            "gaps_confirmed": sum(1 for r in self.rows if r.gap == "confirmed"),
            "gaps_review": sum(1 for r in self.rows if r.gap == "review"),
            "unscreened_blank_names": self.unscreened,
        }

    def to_json(self, all_rows: bool = False) -> Dict[str, Any]:
        rows = self.rows if all_rows else [r for r in self.rows if r.name_hits or r.control]
        return {
            "as_of": self.as_of,
            "threshold": self.threshold,
            "counts": self.counts(),
            "rows": [r.to_json() for r in rows],
        }


def screen_vendors(
    vendors: Iterable[Tuple[str, Optional[float]]], ds: _data.Dataset, threshold: float = DEFAULT_THRESHOLD
) -> Report:
    """Screen (name, amount) pairs. Names are grouped by their exact text; amounts are summed per name."""
    sc = Screener(ds, threshold)
    agg: Dict[str, Row] = {}
    blank = 0
    for name, amount in vendors:
        name = (name or "").strip()
        if not name or not core(name):
            blank += 1
            continue
        row = agg.get(name)
        if row is None:
            row = agg[name] = Row(vendor=name)
        row.lines += 1
        if amount is not None:
            row.amount = (row.amount or 0.0) + amount
    rows = []
    for name, row in agg.items():
        r = sc.screen(name)
        r.lines, r.amount = row.lines, row.amount
        rows.append(r)
    rows.sort(key=lambda r: ({"confirmed": 0, "review": 1}.get(r.gap or "", 2), -(r.amount or 0), r.vendor))
    return Report(rows=rows, as_of=dict(ds.as_of), threshold=threshold, unscreened=blank)
