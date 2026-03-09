"""
Compare latest spider JSON output vs PDatumListe.txt (ground truth from UI).

Usage:
    python tools/compare_run_vs_pdlist.py [json_file] [pdlist_file]

Defaults:
    json_file  = most recent file in output/publications/
    pdlist_file = readme/PDatumListe.txt
"""
import json
import sys
from pathlib import Path
from collections import defaultdict


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────

def _to_iso(dd_mm_yyyy: str) -> str:
    """'24.02.2026' → '2026-02-24'"""
    parts = dd_mm_yyyy.strip().split(".")
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return ""


def parse_pdlist(path: Path) -> list[dict]:
    """
    Parse PDatumListe.txt.  Returns list of dicts with keys:
        pdatum (ISO), edatum (ISO), num, docid, flags
    Skips header/comment lines.
    """
    items = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            # Skip pure header lines
            if line.startswith(("vrai", "vagues", "trier", "PDatum;")):
                continue
            # Must look like a data line: starts with DD.MM.YYYY
            if not (len(line) >= 10 and line[2] == "." and line[5] == "."):
                continue
            parts = [p.strip() for p in line.split(";")]
            if len(parts) < 3:
                continue

            pdatum_raw = parts[0]
            edatum_raw = parts[1] if len(parts) > 1 else ""
            num        = parts[2].strip() if len(parts) > 2 else ""
            docid      = parts[3].strip() if len(parts) > 3 else ""
            flags      = " ".join(p.strip() for p in parts[4:] if p.strip())

            pdatum_iso = _to_iso(pdatum_raw)
            edatum_iso = _to_iso(edatum_raw) if edatum_raw else ""
            if not pdatum_iso or not num:
                continue

            items.append({
                "pdatum": pdatum_iso,
                "edatum": edatum_iso,
                "num":    num,
                "docid":  docid,
                "flags":  flags,
            })
    return items


def parse_spider_json(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Matching
# ─────────────────────────────────────────────────────────────────────────────

def build_spider_index(spider_items: list[dict]) -> tuple[dict, dict]:
    by_docid = {}
    by_num   = {}
    for it in spider_items:
        did = (it.get("DocId") or "").strip()
        num = (it.get("Num")   or "").strip()
        if did:
            by_docid[did] = it
        if num:
            by_num[num] = it          # last wins if duplicates
    return by_docid, by_num


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # ── resolve paths ──────────────────────────────────────────────────────
    if len(sys.argv) >= 2:
        json_path = Path(sys.argv[1])
    else:
        pub_dir = Path("output/publications")
        candidates = sorted(pub_dir.glob("publications_fribourg_*.json"),
                            key=lambda p: p.stat().st_size, reverse=True)
        # Prefer most recent non-empty file
        json_path = next((p for p in candidates if p.stat().st_size > 100), None)
        if not json_path:
            print("No JSON file found in output/publications/"); sys.exit(1)

    if len(sys.argv) >= 3:
        pdlist_path = Path(sys.argv[2])
    else:
        pdlist_path = Path("readme/PDatumListe.txt")

    print(f"Spider JSON : {json_path}  ({json_path.stat().st_size:,} bytes)")
    print(f"PDatumListe : {pdlist_path}")
    print()

    # ── parse ──────────────────────────────────────────────────────────────
    gt_items    = parse_pdlist(pdlist_path)
    spider_items = parse_spider_json(json_path)

    by_docid, by_num = build_spider_index(spider_items)

    # Determine the date window covered by the spider (min PDatum in output)
    spider_pdates = {it.get("PDatum","") for it in spider_items if it.get("PDatum")}
    spider_min = min(spider_pdates) if spider_pdates else ""
    spider_max = max(spider_pdates) if spider_pdates else ""

    print(f"Spider items : {len(spider_items)}")
    print(f"GT items     : {len(gt_items)}")
    print(f"Spider PDatum window : {spider_min} → {spider_max}")
    print()

    # Filter GT to the same window (ignore older items spider wouldn't fetch)
    gt_in_window = [it for it in gt_items if it["pdatum"] >= spider_min] if spider_min else gt_items

    print(f"GT items in spider window (>= {spider_min}): {len(gt_in_window)}")
    print()

    # ── compare ────────────────────────────────────────────────────────────
    present_correct   = []   # in spider, PDatum matches GT
    present_wrong     = []   # in spider, PDatum WRONG
    absent            = []   # in GT window but NOT in spider output
    spider_only       = []   # in spider but not in GT (new / outside GT)

    matched_spider_ids = set()

    for gt in gt_in_window:
        spider_item = None
        match_key   = None

        # 1. Match by DocId (preferred)
        if gt["docid"]:
            spider_item = by_docid.get(gt["docid"])
            match_key   = f"DocId:{gt['docid'][:12]}"
        # 2. Fallback: match by Num
        if spider_item is None and gt["num"]:
            spider_item = by_num.get(gt["num"])
            match_key   = f"Num:{gt['num']}"

        if spider_item is None:
            absent.append(gt)
        else:
            sid = spider_item.get("DocId","") or spider_item.get("Num","")
            matched_spider_ids.add(sid)
            spider_pd = (spider_item.get("PDatum") or "").strip()
            if spider_pd == gt["pdatum"]:
                present_correct.append({**gt, "spider_pd": spider_pd, "match_key": match_key})
            else:
                present_wrong.append({**gt, "spider_pd": spider_pd, "match_key": match_key})

    # Items in spider that weren't matched to any GT entry in window
    for it in spider_items:
        sid = it.get("DocId","") or it.get("Num","")
        if sid not in matched_spider_ids:
            spider_only.append(it)

    # ── report ─────────────────────────────────────────────────────────────
    total_in_window = len(gt_in_window)
    n_correct  = len(present_correct)
    n_wrong    = len(present_wrong)
    n_absent   = len(absent)
    n_only     = len(spider_only)

    acc_denom = n_correct + n_wrong
    acc = 100 * n_correct / acc_denom if acc_denom else 0

    print("=" * 65)
    print(f"  ACCURACY  : {n_correct}/{acc_denom} correct PDatum  ({acc:.1f}%)")
    print(f"  PRESENT   : {n_correct + n_wrong}  ({n_correct} correct PDatum, {n_wrong} wrong)")
    print(f"  ABSENT    : {n_absent}  (in GT window but missing from spider)")
    print(f"  SPIDER-ONLY: {n_only}  (in spider but not in GT list)")
    print("=" * 65)
    print()

    # ── wrong PDatum ───────────────────────────────────────────────────────
    if present_wrong:
        print(f"── WRONG PDatum ({n_wrong}) ─────────────────────────────────────────")
        for it in present_wrong:
            print(f"  [{it['match_key']}]  Num={it['num']}")
            print(f"    GT={it['pdatum']}  Spider={it['spider_pd']}")
        print()

    # ── absent items ───────────────────────────────────────────────────────
    if absent:
        print(f"── ABSENT items ({n_absent}) — in GT but not scraped ──────────────────")
        # Group by PDatum wave
        by_wave = defaultdict(list)
        for it in absent:
            by_wave[it["pdatum"]].append(it)
        for wave in sorted(by_wave.keys(), reverse=True):
            print(f"  Wave {wave}  ({len(by_wave[wave])} missing)")
            for it in by_wave[wave]:
                did = it['docid'][:12] if it['docid'] else "(no DocId)"
                print(f"    Num={it['num']}  DocId={did}  flags=[{it['flags']}]")
        print()

    # ── spider-only items ──────────────────────────────────────────────────
    if spider_only:
        print(f"── SPIDER-ONLY items ({n_only}) — not in GT list ───────────────────────")
        by_wave2 = defaultdict(list)
        for it in spider_only:
            by_wave2[it.get("PDatum","?")].append(it)
        for wave in sorted(by_wave2.keys(), reverse=True):
            print(f"  Wave {wave}  ({len(by_wave2[wave])} items)")
            for it in by_wave2[wave]:
                did = (it.get("DocId") or "")[:12]
                print(f"    Num={it.get('Num','')}  DocId={did}")
        print()

    # ── wave distribution ──────────────────────────────────────────────────
    print("── Wave distribution ────────────────────────────────────────────")
    print(f"  {'PDatum':<14}  {'GT':>4}  {'Spider':>6}  {'Correct':>7}  {'Wrong':>5}  {'Absent':>6}")

    all_waves = sorted(
        set(it["pdatum"] for it in gt_in_window) |
        set(it.get("PDatum","") for it in spider_items if it.get("PDatum")),
        reverse=True
    )
    for w in all_waves:
        gt_cnt  = sum(1 for it in gt_in_window if it["pdatum"] == w)
        sp_cnt  = sum(1 for it in spider_items  if it.get("PDatum","") == w)
        cor_cnt = sum(1 for it in present_correct if it["pdatum"] == w)
        wrg_cnt = sum(1 for it in present_wrong   if it["pdatum"] == w)
        abs_cnt = sum(1 for it in absent          if it["pdatum"] == w)
        print(f"  {w:<14}  {gt_cnt:4d}  {sp_cnt:6d}  {cor_cnt:7d}  {wrg_cnt:5d}  {abs_cnt:6d}")
    print()


if __name__ == "__main__":
    main()
