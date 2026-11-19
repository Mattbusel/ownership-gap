"""ownership-gap command line.

Exit codes (stable, for procurement pipelines):
  0  no gap (or --fail-on never)
  1  at least one gap: a vendor the name screen clears and the ownership and control rules catch
  2  bad input (no vendor column, unreadable file, blank vendor names without --skip-blank)
  3  lists missing or stale: nothing is cleared on data the tool cannot vouch for
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, TextIO, Tuple

from . import __version__
from . import data as _data
from .match import SCORER
from .screen import DEFAULT_THRESHOLD, Report, Row, Screener, screen_vendors

VENDOR_COLUMNS = [
    "vendor",
    "vendor_name",
    "vendor name",
    "vendor_legal_name",
    "vendorname",
    "payee",
    "payee_name",
    "payee name",
    "supplier",
    "supplier_name",
    "supplier name",
    "recipient",
    "recipient_name",
    "recipient name",
    "counterparty",
    "counterparty_name",
    "name",
    "company",
    "company_name",
    "legal_name",
    "lgl_nm",
    "merchant",
]
AMOUNT_COLUMNS = [
    "amount",
    "amt",
    "total",
    "total_amount",
    "payment_amount",
    "check_amount",
    "dollar_amount",
    "sum_amount",
    "invoice_amount",
    "obligation",
    "obligated_amount",
    "ytd_amt",
    "line_item_amount",
    "value",
]


class InputError(Exception):
    pass


def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_")


def _pick(header: Sequence[str], wanted: Optional[str], candidates: List[str]) -> Optional[str]:
    keys = {_key(h): h for h in header}
    if wanted:
        if wanted in header:
            return wanted
        if _key(wanted) in keys:
            return keys[_key(wanted)]
        raise InputError(f"no column {wanted!r}; columns are: {', '.join(header)}")
    for c in candidates:
        if _key(c) in keys:
            return keys[_key(c)]
    return None


def read_vendors(
    f: TextIO, column: Optional[str] = None, amount_column: Optional[str] = None
) -> Tuple[List[Tuple[str, Optional[float]]], str, Optional[str]]:
    text = f.read()
    if text.startswith("﻿"):
        text = text[1:]
    if not text.strip():
        raise InputError("empty input")
    try:
        dialect = csv.Sniffer().sniff(text[:65536], delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    header = reader.fieldnames or []
    vcol = _pick(header, column, VENDOR_COLUMNS)
    if vcol is None:
        raise InputError(
            "no vendor column found (looked for "
            + ", ".join(VENDOR_COLUMNS[:8])
            + ", ...); pass --column. Columns are: "
            + ", ".join(header)
        )
    acol = _pick(header, amount_column, AMOUNT_COLUMNS)
    out: List[Tuple[str, Optional[float]]] = []
    for rec in reader:
        amt: Optional[float] = None
        if acol is not None:
            raw = (rec.get(acol) or "").replace("$", "").replace(",", "").strip()
            if raw.startswith("(") and raw.endswith(")"):
                raw = "-" + raw[1:-1]
            try:
                amt = float(raw) if raw else None
            except ValueError:
                amt = None
        out.append((rec.get(vcol) or "", amt))
    return out, vcol, acol


def _money(x: Optional[float]) -> str:
    return "" if x is None else f"${x:,.0f}"


def _name_cell(r: Row) -> str:
    if r.name_hits:
        h = r.name_hits[0]
        return f"HIT {h['list'].split(' - ')[0]}: {h['name']} ({h['score']:g})"
    if r.name_closest:
        return f"clear (closest {r.name_closest['score']:g})"
    return "clear"


def _control_cell(r: Row) -> str:
    if not r.control:
        return "clear"
    h = r.control[0]
    lists = ", ".join(_short(x) for x in h.lists)
    more = f" +{len(r.control) - 1}" if len(r.control) > 1 else ""
    return f"HIT {h.kind}: {lists}{more}"


def _short(x: str) -> str:
    return {
        "US_DOD_1260H": "1260H",
        "US_FAR_889": "889",
        "US_FCC_COVERED": "FCC Covered",
        "US_DHS_UFLPA": "UFLPA",
        "US_BIOSECURE": "BIOSECURE",
        "ofac-50": "OFAC 50%",
        "bis-affiliates": "BIS Affiliates",
        "any-list-50": "50% by listed",
    }.get(x, x)


def render_table(rep: Report, out: TextIO, all_rows: bool = False, chains: bool = True) -> None:
    rows = rep.rows if all_rows else [r for r in rep.rows if r.name_hits or r.control]
    c = rep.counts()
    w1 = min(max([len(r.vendor) for r in rows] + [6]), 44)
    w2 = 46
    out.write(f"{'VENDOR':<{w1}}  {'NAME-ONLY OFAC/CSL SCREEN':<{w2}}  {'OWNERSHIP AND CONTROL RULES':<36}  GAP\n")
    out.write(f"{'-' * w1}  {'-' * w2}  {'-' * 36}  ---------\n")
    for r in rows:
        line = (
            f"{r.vendor[:w1]:<{w1}}  {_name_cell(r)[:w2]:<{w2}}  {_control_cell(r)[:36]:<36}  {(r.gap or '').upper()}"
        )
        out.write(line.rstrip() + "\n")
    out.write(
        f"\n{c['vendors']} vendors screened. Name screen: {c['name_screen_hits']} hits. Ownership and control "
        f"rules: {c['ownership_control_hits']} hits. Gaps: {c['gaps']} ({c['gaps_confirmed']} confirmed, "
        f"{c['gaps_review']} review).\n"
    )
    if c["unscreened_blank_names"]:
        out.write(f"{c['unscreened_blank_names']} lines had no vendor name and were not screened.\n")
    out.write("Lists as of: " + ", ".join(f"{k} {v}" for k, v in sorted(rep.as_of.items())) + "\n")
    if chains and rep.gaps:
        out.write("\nWhy each gap is a gap:\n")
        for r in rep.gaps:
            out.write(f"\n[{(r.gap or '').upper()}] {r.vendor}" + (f"  {_money(r.amount)}" if r.amount else "") + "\n")
            if r.name_closest:
                out.write(
                    f"  name screen: clear; closest CSL name {r.name_closest['name']!r} "
                    f"({r.name_closest['list'].split(' - ')[0]}) scored {r.name_closest['score']:g}, "
                    f"threshold {rep.threshold:g}\n"
                )
            else:
                out.write("  name screen: clear; no CSL name shares a distinctive word\n")
            for h in r.control:
                out.write(f"  {h.kind} ({h.strength}):\n")
                for step in h.chain:
                    out.write(f"    {step}\n")
                for s in h.sources:
                    out.write(f"    source: {s}\n")
    out.write("\nEvidence for a compliance review, not a determination by any agency.\n")


def render_csv(rep: Report, out: TextIO, all_rows: bool = False) -> None:
    rows = rep.rows if all_rows else [r for r in rep.rows if r.name_hits or r.control]
    w = csv.writer(out, lineterminator="\n")
    w.writerow(
        [
            "vendor",
            "lines",
            "amount",
            "name_screen",
            "name_screen_match",
            "name_screen_list",
            "name_screen_score",
            "ownership_control",
            "kind",
            "lists",
            "matched",
            "strength",
            "chain",
            "sources",
            "gap",
        ]
    )
    for r in rows:
        nh = r.name_hits[0] if r.name_hits else (r.name_closest or {})
        h = r.control[0] if r.control else None
        w.writerow(
            [
                r.vendor,
                r.lines,
                "" if r.amount is None else f"{r.amount:.2f}",
                r.name_status,
                nh.get("name", ""),
                nh.get("list", ""),
                nh.get("score", ""),
                r.control_status,
                h.kind if h else "",
                ";".join(h.lists) if h else "",
                h.matched if h else "",
                h.strength if h else "",
                " -> ".join(h.chain) if h else "",
                " ".join(h.sources) if h else "",
                r.gap or "",
            ]
        )


def _render(rep: Report, args: argparse.Namespace, out: TextIO, vcol: str, acol: Optional[str]) -> None:
    if args.format == "json":
        json.dump(dict(rep.to_json(all_rows=args.all), vendor_column=vcol, amount_column=acol), out, indent=1)
        out.write("\n")
    elif args.format == "csv":
        render_csv(rep, out, all_rows=args.all)
    else:
        render_table(rep, out, all_rows=args.all)


def _load(args: argparse.Namespace) -> _data.Dataset:
    return _data.load(
        Path(args.data) if args.data else _data.default_dir(),
        max_age_days=None if args.allow_stale else args.max_age_days,
    )


def _exit_code(rep: Report, fail_on: str) -> int:
    if fail_on == "never":
        return 0
    if fail_on == "confirmed":
        return 1 if any(r.gap == "confirmed" for r in rep.rows) else 0
    return 1 if rep.gaps else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="ownership-gap",
        description="Show what a name-only OFAC/CSL screen clears and the ownership and control rules catch. "
        "Exits 1 on any gap.",
    )
    p.add_argument("--version", action="version", version=f"ownership-gap {__version__} ({SCORER})")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--data", help="data directory (default: $OWNERSHIP_GAP_DATA or ~/.cache/ownership-gap)")
        sp.add_argument(
            "--max-age-days",
            type=int,
            default=_data.DEFAULT_MAX_AGE_DAYS,
            help="refuse lists older than this (default 7)",
        )
        sp.add_argument("--allow-stale", action="store_true", help="screen on old lists anyway")
        sp.add_argument(
            "--threshold",
            type=float,
            default=DEFAULT_THRESHOLD,
            help="name match score (0-100) both screens use (default 90)",
        )

    f = sub.add_parser("fetch", help="download the lists (CSL from trade.gov, the rest from us-restricted-parties)")
    f.add_argument("--data", help="data directory")

    s = sub.add_parser("screen", help="screen a vendor CSV")
    s.add_argument("csv", help="vendor file (CSV/TSV), or - for stdin")
    s.add_argument("--column", help="vendor name column (default: detected)")
    s.add_argument("--amount-column", help="amount column (default: detected, optional)")
    s.add_argument("--format", choices=["table", "csv", "json"], default="table")
    s.add_argument("--out", help="write here instead of stdout")
    s.add_argument("--all", action="store_true", help="list every vendor, not only those with a hit")
    s.add_argument(
        "--fail-on",
        choices=["review", "confirmed", "never"],
        default="review",
        help="which gaps exit 1 (default review: every gap)",
    )
    s.add_argument("--skip-blank", action="store_true", help="count lines with no vendor name instead of failing")
    common(s)

    c = sub.add_parser("check", help="screen one or more names given on the command line")
    c.add_argument("names", nargs="+")
    c.add_argument("--format", choices=["table", "json"], default="table")
    c.add_argument("--fail-on", choices=["review", "confirmed", "never"], default="review")
    common(c)

    args = p.parse_args(argv)
    try:
        if args.cmd == "fetch":
            dest = Path(args.data) if args.data else _data.default_dir()
            log = _data.fetch(dest)
            for name, meta in log["files"].items():
                print(f"{name}  {meta['sha256'][:16]}  {meta['url']}")
            print(f"fetched into {dest} at {log['fetched_at']}")
            return 0
        ds = _load(args)
        if args.cmd == "check":
            sc = Screener(ds, args.threshold)
            rep = Report(rows=[sc.screen(n) for n in args.names], as_of=dict(ds.as_of), threshold=args.threshold)
            if args.format == "json":
                json.dump(rep.to_json(all_rows=True), sys.stdout, indent=1)
                sys.stdout.write("\n")
            else:
                render_table(rep, sys.stdout, all_rows=True)
            return _exit_code(rep, args.fail_on)
        # screen
        if args.csv == "-":
            vendors, vcol, acol = read_vendors(sys.stdin, args.column, args.amount_column)
        else:
            with open(args.csv, encoding="utf-8-sig", errors="replace", newline="") as fh:
                vendors, vcol, acol = read_vendors(fh, args.column, args.amount_column)
        rep = screen_vendors(vendors, ds, args.threshold)
        if rep.unscreened and not args.skip_blank:
            print(
                f"ownership-gap: {rep.unscreened} lines have no vendor name in column {vcol!r}; a blank name "
                "cannot be cleared. Fix the file or pass --skip-blank.",
                file=sys.stderr,
            )
            return 2
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="") as fh_out:
                _render(rep, args, fh_out, vcol, acol)
        else:
            _render(rep, args, sys.stdout, vcol, acol)
        return _exit_code(rep, args.fail_on)
    except _data.DataError as e:
        print(f"ownership-gap: {e}", file=sys.stderr)
        return 3
    except (InputError, OSError) as e:
        print(f"ownership-gap: {e}", file=sys.stderr)
        return 2


def run() -> None:  # console entry point
    sys.exit(main())


__all__ = ["InputError", "main", "read_vendors", "render_csv", "render_table"]
