# ownership-gap: a clean name screen is not a clean vendor

[![test](https://github.com/Mattbusel/ownership-gap/actions/workflows/test.yml/badge.svg)](https://github.com/Mattbusel/ownership-gap/actions/workflows/test.yml)
[![release](https://img.shields.io/github/v/release/Mattbusel/ownership-gap)](https://github.com/Mattbusel/ownership-gap/releases)

Most vendor screening checks payee names against the OFAC SDN list and the trade.gov Consolidated Screening List
(CSL) and calls a vendor clean when nothing matches. The rules that decide whether a buyer has a problem reach
further than those names: the OFAC 50 Percent Rule and the BIS Affiliates Rule cover companies listed parties own,
and five US lists (DoD 1260H with the subsidiaries DoD names, Section 889, the FCC Covered List, UFLPA and
BIOSECURE) are not in the CSL at all.

We ran both screens over 366,981 vendors that 33 public buyers paid (city, county and state open checkbooks and
federal contract recipients, Nov 19 2026). The name screen cleared every vendor below. The rules caught them:

| Buyer | Paid | Vendor | What the name screen missed |
|---|---|---|---|
| A large US city's power utility | $12.3M since Jan 2025 | BAODING TIANWEI BAOBIAN ELECTRIC CO, LTD | named by DoD as a subsidiary of China South Industries Group, a 1260H Chinese military company ([FR 2026-11571](https://www.federalregister.gov/documents/2026/06/10/2026-11571/notice-of-availability-of-designation-of-chinese-military-companies)) |
| A US city airport's bus operations | $4.8M in 2025 | BYD COACH & BUS LLC | carries the brand of BYD Company Limited, a 1260H company (review: confirm ownership) |
| NIH | $222,000 for a DNBSEQ T1+ sequencer | COMPLETE GENOMICS LLC | named by DoD as a subsidiary of BGI Group (1260H, BIOSECURE) |
| 9 federal buyers (NIH, VA, ARS, EPA, NOAA, USGS, NASA, NIST, Smithsonian) | contract actions FY2025-26 | NOVOGENE CORPORATION INC | same name as Novogene Company Limited, 1260H and BIOSECURE (Novogene contests the listing) |

`ownership-gap` puts the two screens side by side for any vendor file and **exits 1 when there is a gap**, so it
can stop a procurement pipeline instead of producing a report nobody reads. Every hit carries the ownership chain
and the source document. It refuses to clear anyone on lists older than 7 days.

## Install

```
pip install "ownership-gap @ https://github.com/Mattbusel/ownership-gap/releases/download/v0.1.0/ownership_gap-0.1.0-py3-none-any.whl"
pip install rapidfuzz   # optional, faster on large files
```

No required dependencies, Python 3.9+, typed. `pip install ownership-gap` works once the PyPI upload is live.

## Use

```
ownership-gap fetch                       # CSL from trade.gov; 1260H, 889, FCC, UFLPA, BIOSECURE and affiliates from us-restricted-parties
ownership-gap screen vendors.csv          # side-by-side table, then the chain behind every gap
ownership-gap screen vendors.csv --format json --out gaps.json
ownership-gap check "COMPLETE GENOMICS LLC" "HIKVISION USA INC"
```

The vendor column is detected (`vendor`, `vendor_name`, `payee`, `supplier`, `recipient`, ...) or set with
`--column`; an amount column is optional and summed per vendor.

| Exit | Meaning |
|---|---|
| 0 | no gap |
| 1 | at least one vendor the name screen clears and the rules catch (`--fail-on confirmed` ignores review gaps, `--fail-on never` reports only) |
| 2 | bad input: no vendor column, unreadable file, or blank vendor names (a blank name cannot be cleared; `--skip-blank` counts them instead) |
| 3 | lists missing or older than `--max-age-days` (default 7); `--allow-stale` overrides |

In CI:

```yaml
- run: pip install "ownership-gap @ https://github.com/Mattbusel/ownership-gap/releases/download/v0.1.0/ownership_gap-0.1.0-py3-none-any.whl"
- run: ownership-gap fetch
- run: ownership-gap screen new_vendors.csv --format csv --out ownership-gap.csv
```

## A real example, anonymized

Ten payees from a large US city's open checkbook (`examples/city_checkbook.csv`; the city is left out, the vendor
names and amounts are as published):

```
$ ownership-gap screen examples/city_checkbook.csv --all
VENDOR                                        NAME-ONLY OFAC/CSL SCREEN                       OWNERSHIP AND CONTROL RULES           GAP
--------------------------------------------  ----------------------------------------------  ------------------------------------  ---------
BAODING TIANWEI BAOBIAN ELECTRIC CO, LTD      clear (closest 59.6)                            HIT listed: 1260H                     CONFIRMED
BYD MOTORS LLC                                clear (closest 52.2)                            HIT brand: 1260H                      REVIEW
MV TRANSPORTATION INC                         clear (closest 84.2)                            clear
MV PUBLIC TRANSPORTATION INC.                 clear (closest 78.3)                            clear
NATIONAL AUTO FLEET GROUP 72 HOUR LLC         clear (closest 56.8)                            clear
WESCO DISTRIBUTION INC.                       clear (closest 82.4)                            clear
INSIGHT PUBLIC SECTOR INC                     clear (closest 66.7)                            clear
FORD E C INC                                  clear (closest 50)                              clear
WORKDAY, INC.                                 clear                                           clear
BROWNING-FERRIS INDUSTRIES OF CALIFORNIA INC  clear (closest 58)                              clear

10 vendors screened. Name screen: 0 hits. Ownership and control rules: 2 hits. Gaps: 2 (1 confirmed, 1 review).
Lists as of: affiliates 2026-11-19T15:41:56Z, csl 2026-11-19T18:00:49Z, lists 2026-11-19T15:41:48Z

Why each gap is a gap:

[CONFIRMED] BAODING TIANWEI BAOBIAN ELECTRIC CO, LTD  $12,337,970
  name screen: clear; closest CSL name 'China Aeronautical Radio Electronics Research Institute' (Entity List (EL)) scored 59.6, threshold 90
  listed (confirmed):
    BAODING TIANWEI BAOBIAN ELECTRIC CO, LTD (vendor)
    is Baoding Tianwei Baobian Electric Co., Ltd.
    named by the list as a subsidiary of China South Industries Group Corporation (DoD 1260H Chinese military company)
    DoD 1260H Chinese military company, listed 2026-06-10 (FR 2026-11571)
    source: https://www.federalregister.gov/documents/2026/06/10/2026-11571/notice-of-availability-of-designation-of-chinese-military-companies

[REVIEW] BYD MOTORS LLC  $1,071,799
  name screen: clear; closest CSL name 'XTREME MOTORS ' (Denied Persons List (DPL)) scored 52.2, threshold 90
  brand (review):
    BYD MOTORS LLC (vendor)
    legal name carries the brand "BYD" of BYD Company Limited
    BYD Company Limited: DoD 1260H Chinese military company
    ownership of this vendor not yet confirmed from corporate records
    source: https://www.federalregister.gov/documents/2026/06/10/2026-11571/notice-of-availability-of-designation-of-chinese-military-companies

Evidence for a compliance review, not a determination by any agency.
$ echo $?
1
```

An ownership hit looks like this (`ownership-gap check "Sistema Finance JSC"`):

```
  owned (confirmed):
    Sistema Finance JSC (vendor)
    = "Sistema Finance" JSC (lei:2534004JKXBEM0T4LW33, RU, name match 100)
    BIS Affiliates Rule: definitely
    OFAC 50 Percent Rule: definitely
    SISTEMA PUBLIC JOINT STOCK FINANCIAL CORPORATION [SDN, RUSSIA-EO14024, UKRAINE-EO13662] (listed party)
    SISTEMA PUBLIC JOINT STOCK FINANCIAL CORPORATION owns 100%, controls of "Sistema Finance" JSC (lei:2534004JKXBEM0T4LW33) (GLEIF IS_DIRECTLY_CONSOLIDATED_BY as of 2025-12-08)
```

## What each screen does

Both screens see the same names, cleaned the same way (case, accents, punctuation and legal forms such as Inc,
LLC, Co., Ltd., JSC set aside; `X DBA Y` read as two names).

**Name-only OFAC/CSL screen.** A hit is an exact match, or a token-sort score of 90 or more, against any name or
alias on the CSL (SDN and the other Treasury lists, Entity List, MEU, Denied Persons, Unverified, ITAR debarred,
nonproliferation). This is the screen most vendor files already get.

**Ownership and control rules.** Three ways to be caught:

- `listed`: the vendor is on 1260H (including every subsidiary DoD names), Section 889, the FCC Covered List,
  UFLPA or BIOSECURE. None of these is in the CSL.
- `owned`: the vendor is a company that a listed party owns or controls, from GLEIF ownership links, with the
  verdict of the [ownership-rules](https://github.com/Mattbusel/ownership-rules) engine for the OFAC 50 Percent
  Rule, the BIS Affiliates Rule (stayed until January 10, 2027) and a 50 percent test against the five lists.
- `brand`: the vendor's legal name carries the brand of a listed parent (`HIKVISION USA INC`, `ZTE USA INC`,
  `BYD COACH & BUS LLC`). Three-letter brands count only as the first word followed by corporate words.

The rules side is held to a stricter bar than the name side, so a gap is never a name the name screen had a
weaker chance at: a fuzzy rules-side match needs 95 and the same first word (`THE BODE TECHNOLOGY GROUP` is not
`BOE Technology Group`).

**A gap** is a vendor the name screen clears and the rules catch:

- `confirmed`: the vendor's name is a listed entity's name (exact, longer than 4 characters), or an ownership
  chain the rule covers (`definitely` or `possibly`).
- `review`: a brand in the name, a short-name match (`BYD HOLDINGS LLC`), a fuzzy match, or control with no
  known 50 percent share (`control_only`, which OFAC FAQ 398 and BIS FAQ 43 say the rules do not reach).

## Limits, stated plainly

- Matching is by name. A confirmed gap means the payee's legal name is a listed entity's name; it is not proof a
  payment was prohibited when it was made. Many 1260H listings date from June 10, 2026, Novogene contests its
  listing, and the BIS Affiliates Rule is stayed.
- Ownership coverage is what GLEIF records. A subsidiary with no LEI, or one GLEIF does not link to its parent,
  is only caught when DoD names it or its name carries the parent's brand.
- Resellers are not resolved from names. A DJI drone bought through a dealer shows up as the dealer.
- Results are evidence for a compliance review, not a determination by any agency.

## Data

| File | From | Used by |
|---|---|---|
| `consolidated.json` | trade.gov Consolidated Screening List | name screen |
| `all.jsonl`, `manifest.json` | [us-restricted-parties](https://github.com/Mattbusel/us-restricted-parties) (rebuilt daily from the Federal Register, eCFR, fcc.gov, dhs.gov) | rules: listed, brand |
| `affiliates.senzing.jsonl`, `affiliates.manifest.json` | us-restricted-parties affiliates build (GLEIF Level 2 + CSL, ownership-rules verdicts) | rules: owned |

`ownership-gap fetch` puts them in `~/.cache/ownership-gap` (or `--data`, or `$OWNERSHIP_GAP_DATA`) and records
when and what it fetched; `screen` refuses data older than 7 days.

## Library

```python
from ownership_gap import load, screen_vendors, default_dir

report = screen_vendors([("COMPLETE GENOMICS LLC", 222000.0), ("Acme Supply", 120.0)], load(default_dir()))
for row in report.gaps:
    print(row.vendor, row.gap, row.control[0].chain)
```

## License

Apache-2.0. The list data carries its own terms (CC BY 4.0 for us-restricted-parties; CSL and GLEIF data are
public).

Built by [Tensorust LLC](https://tensorust.com). We run this screen over a buyer's full vendor or payment file,
with every hit checked against corporate records, as a paid service: mattbusel@tensorust.com.
