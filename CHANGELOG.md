# Changelog

## 0.1.0 (2026-11-19)

First release.

- `ownership-gap screen FILE`: a name-only OFAC/CSL screen and an ownership and control screen side by side for
  every vendor in a CSV/TSV file, with the ownership chain and the source document behind every hit.
- Exits 1 on any gap (a vendor the name screen clears and the rules catch), 2 on bad input, 3 on missing or stale
  lists. Why: a screen that only writes a report lets a gap through; a non-zero exit stops the pipeline.
- Refuses lists older than 7 days and refuses blank vendor names unless told otherwise. Why: "clear" on old lists
  or on an empty name is a silent default, and silent defaults are how listed vendors get paid.
- Rules covered: DoD 1260H (with every subsidiary DoD names), Section 889, FCC Covered List, UFLPA, BIOSECURE
  (interim), OFAC 50 Percent Rule and BIS Affiliates Rule (GLEIF ownership, ownership-rules verdicts), plus listed
  brands in a vendor's legal name.
- The rules side is held to a stricter match bar (95 and the same first word for fuzzy matches; short names and
  brands never better than `review`) than the name side (90), so a reported gap is never a weaker name match.
- `ownership-gap fetch` downloads the CSL from trade.gov and the other lists from us-restricted-parties.
- `ownership-gap check NAME...` for one-off names; `--format table|csv|json`.
