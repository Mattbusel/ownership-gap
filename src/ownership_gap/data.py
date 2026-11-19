"""The data both screens run on, where it comes from, and the freshness check that refuses stale data.

Name screen (what a name-only OFAC/CSL screen sees):
  - consolidated.json: the trade.gov Consolidated Screening List (OFAC SDN and the other Treasury lists, BIS Entity
    List, MEU List, Denied Persons, Unverified, State ITAR and nonproliferation lists), names and aliases.

Ownership and control rules (what the name screen does not see):
  - all.jsonl: DoD 1260H (with every subsidiary DoD names), Section 889, FCC Covered List, UFLPA, BIOSECURE, from
    https://github.com/Mattbusel/us-restricted-parties (none of these five are in the CSL).
  - affiliates.senzing.jsonl: companies that listed parties own or control under the OFAC 50 Percent Rule, the BIS
    Affiliates Rule and a 50 percent test against the five lists above, derived from GLEIF ownership links by the
    ownership-rules engine, each with its proof path.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

FEED = "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/"
CSL_URL = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json"
FILES = {
    "consolidated.json": CSL_URL,
    "all.jsonl": FEED + "all.jsonl",
    "manifest.json": FEED + "manifest.json",
    "affiliates.senzing.jsonl": FEED + "affiliates.senzing.jsonl",
    "affiliates.manifest.json": FEED + "affiliates.manifest.json",
}
FETCH_LOG = "fetched.json"
UA = "ownership-gap (+https://github.com/Mattbusel/ownership-gap)"
DEFAULT_MAX_AGE_DAYS = 7

# What each list does to a buyer, in one line, with the rule text it rests on.
LIST_RULES: Dict[str, Dict[str, str]] = {
    "US_DOD_1260H": {
        "label": "DoD 1260H Chinese military company",
        "effect": "DoD's list of Chinese military companies, each with the subsidiaries DoD names; FY2024 NDAA sec. "
        "805 restricts DoD contracting with listed entities, and BIOSECURE builds on the biotech listings.",
        "cite": "https://www.federalregister.gov/documents/2026/06/10/2026-11571/notice-of-availability-of-designation-of-chinese-military-companies",
    },
    "US_FAR_889": {
        "label": "Section 889 covered company",
        "effect": "FAR 52.204-25 and 2 CFR 200.216: no federal contract or grant money for covered telecom or video "
        "surveillance equipment or services from these companies or any subsidiary or affiliate.",
        "cite": "https://www.ecfr.gov/current/title-48/chapter-1/subchapter-H/part-52/subpart-52.2/section-52.204-25",
    },
    "US_FCC_COVERED": {
        "label": "FCC Covered List",
        "effect": "No new FCC equipment authorizations; no Universal Service Fund (E-Rate, Rural Health Care) money "
        "for this equipment or service.",
        "cite": "https://www.fcc.gov/supplychain/coveredlist",
    },
    "US_DHS_UFLPA": {
        "label": "UFLPA Entity List",
        "effect": "Goods made wholly or in part by this entity are presumed made with forced labor and barred from "
        "US import.",
        "cite": "https://www.dhs.gov/uflpa-entity-list",
    },
    "US_BIOSECURE": {
        "label": "BIOSECURE biotechnology company of concern (interim, from 1260H)",
        "effect": "FY2026 NDAA sec. 851 makes 1260H biotechnology companies companies of concern for federal "
        "contracts and grants (OMB's own list is due Dec 18, 2026).",
        "cite": "https://github.com/Mattbusel/us-restricted-parties/blob/main/docs/lists.md#us_biosecure-biosecure-biotechnology-companies-of-concern-interim",
    },
}
AFFILIATE_RULES: Dict[str, Dict[str, str]] = {
    "ofac-50": {
        "label": "OFAC 50 Percent Rule",
        "effect": "An entity owned 50 percent or more, directly or indirectly, in the aggregate, by blocked persons is "
        "itself blocked, listed or not.",
        "cite": "https://ofac.treasury.gov/faqs/topic/1521",
    },
    "bis-affiliates": {
        "label": "BIS Affiliates Rule",
        "effect": "A foreign entity 50 percent or more owned by Entity List, MEU List or certain SDN parties takes "
        "their export restrictions (stayed until January 10, 2027).",
        "cite": "https://www.federalregister.gov/documents/2026/11/09/2026-22793/extension-of-the-suspension-of-expansion-of-end-user-controls-for-affiliates-of-certain-listed",
    },
    "any-list-50": {
        "label": "50 percent owned by a 1260H, 889, FCC, UFLPA or BIOSECURE party",
        "effect": "Not a rule of its own: evidence that the vendor sits inside a listed group.",
        "cite": "https://github.com/Mattbusel/us-restricted-parties/blob/main/docs/affiliates.md",
    },
}


class DataError(Exception):
    """Lists are missing, unreadable or stale. The screen refuses to run rather than clear anyone on them."""


@dataclass
class ListRecord:
    data_source: str
    record_id: str
    name: str
    names: List[str]
    parent: Optional[str]
    listed_on: Optional[str]
    source_url: Optional[str]
    source_doc: Optional[str]


@dataclass
class CslRecord:
    source: str
    name: str
    names: List[str]
    programs: List[str]
    country: Optional[str]
    source_url: Optional[str]


@dataclass
class AffiliateRecord:
    record_id: str
    name: str
    names: List[str]
    country: Optional[str]
    verdicts: Dict[str, str]
    proof: Dict[str, Any]
    verdict_class: str


@dataclass
class Dataset:
    lists: List[ListRecord]
    csl: List[CslRecord]
    affiliates: List[AffiliateRecord]
    as_of: Dict[str, str] = field(default_factory=dict)
    rule_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    removed: List[Dict[str, Any]] = field(default_factory=list)


def default_dir() -> Path:
    env = os.environ.get("OWNERSHIP_GAP_DATA")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "ownership-gap"


def _open(url: str, timeout: int = 300) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch(dest: Path, files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Download every file into ``dest`` atomically and record when and what was fetched."""
    files = files or FILES
    dest.mkdir(parents=True, exist_ok=True)
    log: Dict[str, Any] = {"fetched_at": _now(), "files": {}}
    for name, url in files.items():
        fd, tmp = tempfile.mkstemp(dir=str(dest), prefix=".part-")
        h = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, _open(url) as resp:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                out.write(chunk)
        shutil.move(tmp, str(dest / name))
        log["files"][name] = {"url": url, "sha256": h.hexdigest()}
    (dest / FETCH_LOG).write_text(json.dumps(log, indent=1), encoding="utf-8")
    return log


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(s: Optional[str]) -> Optional[_dt.date]:
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _jsonl(p: Path) -> Iterator[Dict[str, Any]]:
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _names(rec: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for n in rec.get("NAMES") or []:
        v = n.get("NAME_ORG") or n.get("NAME_FULL")
        if v and v not in out:
            out.append(v)
    primary = rec.get("NAME_ORG")
    if primary and primary not in out:
        out.insert(0, primary)
    return out


def load(
    data_dir: Path, max_age_days: Optional[int] = DEFAULT_MAX_AGE_DAYS, today: Optional[_dt.date] = None
) -> Dataset:
    """Read the data directory. Raises DataError on a missing file or on lists older than ``max_age_days``.

    ``max_age_days=None`` turns the age check off (the result still records each source's date).
    """
    missing = [n for n in ("consolidated.json", "all.jsonl") if not (data_dir / n).exists()]
    if missing:
        raise DataError(f"missing {', '.join(missing)} in {data_dir}; run `ownership-gap fetch` first")
    today = today or _dt.date.today()
    as_of: Dict[str, str] = {}
    fetched = {}
    if (data_dir / FETCH_LOG).exists():
        fetched = json.loads((data_dir / FETCH_LOG).read_text(encoding="utf-8"))
    rule_status: Dict[str, Dict[str, Any]] = {}
    removed: List[Dict[str, Any]] = []

    manifest = {}
    if (data_dir / "manifest.json").exists():
        manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
        for key, meta in (manifest.get("lists") or {}).items():
            for later in (meta.get("notes") or {}).get("later_notices") or []:
                if later.get("action") == "removed":
                    removed.append(dict(later, list=meta.get("data_source", key)))
    as_of["lists"] = manifest.get("built_at") or fetched.get("fetched_at") or ""

    csl_raw = json.loads((data_dir / "consolidated.json").read_text(encoding="utf-8"))
    as_of["csl"] = fetched.get("fetched_at") or _dt.datetime.fromtimestamp(
        (data_dir / "consolidated.json").stat().st_mtime, _dt.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    aff_path = None
    for cand in ("affiliates.senzing.jsonl",):
        if (data_dir / cand).exists():
            aff_path = data_dir / cand
    aff_parts = [data_dir / "affiliates.covered.senzing.jsonl", data_dir / "affiliates.review.senzing.jsonl"]
    if (data_dir / "affiliates.manifest.json").exists():
        am = json.loads((data_dir / "affiliates.manifest.json").read_text(encoding="utf-8"))
        as_of["affiliates"] = am.get("built_at") or ""
        rule_status = am.get("rules") or {}
    elif aff_path or any(p.exists() for p in aff_parts):
        as_of["affiliates"] = as_of["lists"]

    if max_age_days is not None:
        stale = []
        for k, v in as_of.items():
            d = _date(v)
            if d is None:
                stale.append(f"{k} (no date)")
            elif (today - d).days > max_age_days:
                stale.append(f"{k} ({v[:10]}, {(today - d).days} days old)")
        if stale:
            raise DataError(
                "stale or undated lists: "
                + "; ".join(stale)
                + f". Limit is {max_age_days} days. Run `ownership-gap fetch`, or pass --allow-stale "
                "and accept that a clear result only means clear as of those dates."
            )

    lists = []
    for r in _jsonl(data_dir / "all.jsonl"):
        lists.append(
            ListRecord(
                data_source=r.get("DATA_SOURCE", ""),
                record_id=r.get("RECORD_ID", ""),
                name=r.get("NAME_ORG", ""),
                names=_names(r),
                parent=r.get("PARENT_NAME"),
                listed_on=r.get("LISTED_ON"),
                source_url=r.get("SOURCE_URL"),
                source_doc=r.get("SOURCE_DOC"),
            )
        )

    csl = []
    for r in csl_raw.get("results", []):
        names = [r.get("name") or ""] + [a for a in (r.get("alt_names") or []) if a]
        csl.append(
            CslRecord(
                source=r.get("source", ""),
                name=r.get("name") or "",
                names=[n for n in names if n],
                programs=list(r.get("programs") or []),
                country=r.get("country"),
                source_url=r.get("source_list_url"),
            )
        )

    affiliates = []
    paths = [aff_path] if aff_path else [p for p in aff_parts if p.exists()]
    seen = set()
    for p in paths:
        for r in _jsonl(p):
            rid = r.get("RECORD_ID", "")
            if rid in seen:
                continue
            seen.add(rid)
            affiliates.append(
                AffiliateRecord(
                    record_id=rid,
                    name=r.get("NAME_ORG", ""),
                    names=_names(r),
                    country=r.get("REGISTRATION_COUNTRY"),
                    verdicts=r.get("OWNERSHIP_VERDICTS") or {},
                    proof=r.get("OWNERSHIP_PROOF") or {},
                    verdict_class=r.get("VERDICT_CLASS") or "",
                )
            )
    if not lists or not csl:
        raise DataError(f"empty lists in {data_dir} (lists={len(lists)}, csl={len(csl)}); refusing to screen")
    return Dataset(lists=lists, csl=csl, affiliates=affiliates, as_of=as_of, rule_status=rule_status, removed=removed)
