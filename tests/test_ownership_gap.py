import datetime as dt
import io
import json
import shutil
from pathlib import Path

import pytest

from ownership_gap import DataError, Screener, load, screen_vendors
from ownership_gap.cli import InputError, main, read_vendors
from ownership_gap.data import fetch
from ownership_gap.match import build, score
from ownership_gap.normalize import core, variants

FX = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 11, 19)


@pytest.fixture(scope="module")
def ds():
    return load(FX, today=TODAY)


@pytest.fixture(scope="module")
def sc(ds):
    return Screener(ds)


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    shutil.copytree(FX, d)
    return d


# -- normalization --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("Complete Genomics, Inc.", "COMPLETE GENOMICS LLC"),
        ("Novogene Company Limited", "NOVOGENE CORPORATION INC"),
        ("Baoding Tianwei Baobian Electric Co., Ltd.", "BAODING TIANWEI BAOBIAN ELECTRIC CO, LTD"),
        ('Joint Stock Company "Sistema Finance"', '"Sistema Finance" JSC'),
    ],
)
def test_same_core(a, b):
    assert core(a) == core(b)


def test_dba_split_and_leading_marker():
    assert variants("ACME INC DBA ROADRUNNER SUPPLY LLC") == ["acme", "roadrunner supply"]
    assert variants("DBA SYSTEMS INC") == ["dba systems"]


def test_parentheticals_only_on_request():
    assert variants("Shenzhen DJI Technology Co., Ltd. (DJI)") == ["shenzhen dji technology"]
    got = variants("Shenzhen DJI Technology Co., Ltd. (DJI)", parentheticals=True)
    assert got == ["shenzhen dji technology", "dji"]


def test_index_exact_and_fuzzy():
    idx = build([("hangzhou hikvision digital technology", 1), ("complete genomics", 2)])
    hits, closest = idx.best(["complete genomics"], 90)
    assert [h.payload for h in hits] == [2] and hits[0].exact
    hits, closest = idx.best(["hikvision usa"], 90)
    assert hits == [] and closest is not None and closest.payload == 1 and closest.score < 90
    assert score("abc def", "def abc") == 100


def test_same_first_blocks_lookalikes():
    idx = build([("boe technology group", 1)])
    assert idx.best(["bode technology group"], 90)[0]  # a plain fuzzy screen would call this a hit
    assert idx.best(["bode technology group"], 90, same_first=True)[0] == []


# -- the two screens ------------------------------------------------------------------------------------------


def test_named_subsidiary_is_a_confirmed_gap(sc):
    r = sc.screen("COMPLETE GENOMICS LLC")
    assert r.name_status == "clear"
    assert r.gap == "confirmed"
    h = r.control[0]
    assert h.kind == "listed" and "US_DOD_1260H" in h.lists and "US_BIOSECURE" in h.lists
    assert any("subsidiary of BGI Group" in step for step in h.chain)
    assert any("2026-11571" in s for s in h.sources)


def test_name_screen_hit_is_not_a_gap(sc):
    r = sc.screen("Huawei Technologies Co., Ltd.")
    assert r.name_status == "hit"
    assert r.control  # also on 889 / FCC / 1260H
    assert r.gap is None


def test_unrelated_vendor_is_clear(sc):
    r = sc.screen("Acme Plumbing Supply LLC")
    assert r.name_status == "clear" and r.control == [] and r.gap is None


def test_brand_in_legal_name_is_review(sc):
    r = sc.screen("HIKVISION USA INC")
    assert r.name_status == "clear"
    assert r.gap == "review"
    assert r.control[0].kind == "brand"
    assert "US_FAR_889" in r.control[0].lists


def test_short_brand_needs_corporate_words(sc):
    assert sc.screen("BYD COACH & BUS LLC").gap == "review"
    assert sc.screen("ZTE USA INC").gap == "review"
    assert sc.screen("BYDALE CONSTRUCTION").control == []
    assert sc.screen("ZTE PLUMBING HEATING").control == []


def test_short_exact_name_is_only_review(sc):
    r = sc.screen("BYD HOLDINGS LLC")
    assert r.gap == "review"


def test_lookalike_is_not_caught(sc):
    assert sc.screen("THE BODE TECHNOLOGY GROUP INC").control == []


def test_ownership_chain_confirmed(sc):
    r = sc.screen("Sistema Finance JSC")
    assert r.gap == "confirmed"
    h = r.control[0]
    assert h.kind == "owned" and "ofac-50" in h.lists
    assert any("owns 100%" in s for s in h.chain)
    assert any("SDN" in s for s in h.chain)
    assert any("gleif.org" in s for s in h.sources)


def test_control_only_is_review(sc):
    r = sc.screen("MEGAFON INVESTMENTS (CYPRUS) LIMITED")
    assert r.gap == "review"
    assert any("control_only" in s for s in r.control[0].chain)


def test_report_groups_and_sums(ds):
    rep = screen_vendors(
        [("COMPLETE GENOMICS LLC", 100.0), ("COMPLETE GENOMICS LLC", 22.5), ("", 5.0), ("Acme", 1.0)], ds
    )
    c = rep.counts()
    assert c["vendors"] == 2 and c["gaps"] == 1 and c["gaps_confirmed"] == 1 and c["unscreened_blank_names"] == 1
    assert rep.gaps[0].amount == 122.5 and rep.gaps[0].lines == 2
    j = rep.to_json()
    assert j["rows"][0]["gap"] == "confirmed"
    assert j["rows"][0]["name_screen"]["status"] == "clear"


# -- data loading fails closed --------------------------------------------------------------------------------


def test_missing_data_refuses(tmp_path):
    with pytest.raises(DataError, match="ownership-gap fetch"):
        load(tmp_path)


def test_stale_data_refuses(data_dir):
    with pytest.raises(DataError, match="stale"):
        load(data_dir, max_age_days=7, today=TODAY + dt.timedelta(days=30))
    assert load(data_dir, max_age_days=None, today=TODAY + dt.timedelta(days=30)).lists


def test_undated_data_refuses(data_dir):
    (data_dir / "fetched.json").unlink()
    m = json.loads((data_dir / "manifest.json").read_text())
    m.pop("built_at")
    (data_dir / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(DataError, match="no date"):
        load(data_dir, today=TODAY)


def test_empty_list_refuses(data_dir):
    (data_dir / "all.jsonl").write_text("")
    with pytest.raises(DataError, match="empty"):
        load(data_dir, today=TODAY)


def test_fetch_from_file_urls(tmp_path):
    dest = tmp_path / "dl"
    files = {
        n: (FX / n).resolve().as_uri()
        for n in (
            "consolidated.json",
            "all.jsonl",
            "manifest.json",
            "affiliates.senzing.jsonl",
            "affiliates.manifest.json",
        )
    }
    log = fetch(dest, files)
    assert set(log["files"]) == set(files)
    assert (dest / "fetched.json").exists()
    assert load(dest, today=TODAY).csl


# -- input reading --------------------------------------------------------------------------------------------


def test_read_vendors_detects_columns_and_amounts():
    rows, v, a = read_vendors(io.StringIO('Payee Name,Check Amount\nACME,"$1,200.50"\nFOO,(30.00)\nBAR,\n'))
    assert v == "Payee Name" and a == "Check Amount"
    assert rows == [("ACME", 1200.5), ("FOO", -30.0), ("BAR", None)]


def test_read_vendors_tsv_and_explicit_column():
    rows, v, a = read_vendors(io.StringIO("id\tsupplier legal\n1\tACME\n"), column="supplier legal")
    assert v == "supplier legal" and rows == [("ACME", None)] and a is None


def test_read_vendors_without_vendor_column():
    with pytest.raises(InputError, match="--column"):
        read_vendors(io.StringIO("a,b\n1,2\n"))


# -- command line exit codes ----------------------------------------------------------------------------------


def _csv(tmp_path, text):
    p = tmp_path / "v.csv"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _run(args, data_dir, capsys):
    code = main([*args, "--data", str(data_dir), "--allow-stale"])
    return code, capsys.readouterr()


def test_cli_gap_exits_1_and_explains(tmp_path, data_dir, capsys):
    p = _csv(tmp_path, "vendor,amount\nCOMPLETE GENOMICS LLC,222000\nAcme,5\n")
    code, out = _run(["screen", p], data_dir, capsys)
    assert code == 1
    assert "NAME-ONLY OFAC/CSL SCREEN" in out.out and "CONFIRMED" in out.out
    assert "subsidiary of BGI Group" in out.out


def test_cli_clean_file_exits_0(tmp_path, data_dir, capsys):
    p = _csv(tmp_path, "vendor\nAcme\nHuawei Technologies Co., Ltd.\n")
    code, _out = _run(["screen", p], data_dir, capsys)
    assert code == 0


def test_cli_fail_on(tmp_path, data_dir, capsys):
    p = _csv(tmp_path, "vendor\nHIKVISION USA INC\n")
    assert _run(["screen", p], data_dir, capsys)[0] == 1
    assert _run(["screen", p, "--fail-on", "confirmed"], data_dir, capsys)[0] == 0
    p2 = _csv(tmp_path, "vendor\nCOMPLETE GENOMICS LLC\n")
    assert _run(["screen", p2, "--fail-on", "never"], data_dir, capsys)[0] == 0


def test_cli_blank_names_fail_unless_skipped(tmp_path, data_dir, capsys):
    p = _csv(tmp_path, "vendor\nAcme\n\n,\n")
    p = _csv(tmp_path, "vendor,amount\nAcme,1\n,2\n")
    code, out = _run(["screen", p], data_dir, capsys)
    assert code == 2 and "--skip-blank" in out.err
    assert _run(["screen", p, "--skip-blank"], data_dir, capsys)[0] == 0


def test_cli_stale_exits_3(tmp_path, data_dir, capsys):
    p = _csv(tmp_path, "vendor\nAcme\n")
    for name in ("manifest.json", "affiliates.manifest.json", "fetched.json"):
        m = json.loads((data_dir / name).read_text())
        m["built_at" if "built_at" in m else "fetched_at"] = "2020-01-01T00:00:00Z"
        (data_dir / name).write_text(json.dumps(m))
    code = main(["screen", p, "--data", str(data_dir)])
    assert code == 3 and "stale" in capsys.readouterr().err


def test_cli_missing_data_exits_3(tmp_path, capsys):
    p = _csv(tmp_path, "vendor\nAcme\n")
    assert main(["screen", p, "--data", str(tmp_path / "nothing")]) == 3


def test_cli_bad_column_exits_2(tmp_path, data_dir, capsys):
    p = _csv(tmp_path, "x,y\n1,2\n")
    assert _run(["screen", p], data_dir, capsys)[0] == 2


def test_cli_json_and_csv(tmp_path, data_dir, capsys):
    p = _csv(tmp_path, "vendor\nCOMPLETE GENOMICS LLC\nAcme\n")
    out = tmp_path / "o.json"
    assert _run(["screen", p, "--format", "json", "--out", str(out), "--all"], data_dir, capsys)[0] == 1
    j = json.loads(out.read_text())
    assert j["counts"]["gaps"] == 1 and len(j["rows"]) == 2 and j["vendor_column"] == "vendor"
    _, o = _run(["screen", p, "--format", "csv"], data_dir, capsys)
    lines = o.out.strip().splitlines()
    assert lines[0].startswith("vendor,lines,amount,name_screen") and "confirmed" in lines[1]


def test_cli_check(data_dir, capsys):
    code, out = _run(["check", "Sistema Finance JSC", "--format", "json"], data_dir, capsys)
    assert code == 1
    assert json.loads(out.out)["rows"][0]["gap"] == "confirmed"


def test_curly_apostrophe_folds_like_straight():
    assert core("O" + chr(0x2019) + "Brien Supply Co") == core("O'Brien Supply Co") == "obrien supply"
