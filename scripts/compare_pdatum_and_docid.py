#!/usr/bin/env python3
"""Compare JSON items with PDatumListe.txt and mark differences.

Usage:
  python scripts/compare_pdatum_and_docid.py --json output/publications/publications_fribourg_20260224_095326.json \
      --pdlist readme/PDatumListe.txt

By default writes `readme/PDatumListe.updated.txt`. Use `--inplace` to overwrite (a .bak is kept).
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
from typing import List, Dict, Tuple
from datetime import datetime


def normalize_num(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = re.sub(r"\s+", " ", s.strip())
    return s


def normalize_date_for_compare(s: str) -> str:
    """Return ISO date 'YYYY-MM-DD' if parseable from 'DD.MM.YYYY' or 'YYYY-MM-DD', else original trimmed."""
    if not s:
        return ""
    s = s.strip()
    # try DD.MM.YYYY
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    return s


def load_items_from_json(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # If data is a plain list of dicts, return it
    if isinstance(data, list):
        return data

    # If dict, try to locate the list of items
    if isinstance(data, dict):
        # Common keys that might contain the list
        for key in ("items", "publications", "results", "data"):
            if key in data and isinstance(data[key], list):
                return data[key]

        # Otherwise pick first list-of-dicts value
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v

    raise ValueError(f"Cannot find list of items in JSON: {path}")


def find_key_case_insensitive(d: dict, candidates: List[str]):
    for k in candidates:
        if k in d:
            return d[k]
    # try lowercased keys
    lowermap = {kk.lower(): vv for kk, vv in d.items()}
    for c in candidates:
        if c.lower() in lowermap:
            return lowermap[c.lower()]
    return None


def parse_pdlist_lines(lines: List[str]) -> Tuple[List[str], Dict[str,int]]:
    """Return original lines and a mapping num->index for lines that look like table rows."""
    map_num_to_index: Dict[str,int] = {}
    for i, line in enumerate(lines):
        if ";" not in line:
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) >= 3:
            num = normalize_num(parts[2])
            if num:
                # only map first occurrence
                if num not in map_num_to_index:
                    map_num_to_index[num] = i
    return lines, map_num_to_index


def ensure_parts_length(parts: List[str], n: int) -> List[str]:
    if len(parts) < n:
        parts += [""] * (n - len(parts))
    return parts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="Input JSON with items")
    ap.add_argument("--pdlist", required=True, help="Path to readme/PDatumListe.txt")
    ap.add_argument("--out", help="Output file (default: readme/PDatumListe.updated.txt)")
    ap.add_argument("--inplace", action="store_true", help="Overwrite input file (creates .bak)")
    args = ap.parse_args()

    items = load_items_from_json(args.json)

    # collect nums present in JSON
    nums_in_json = set()
    for it in items:
        num = find_key_case_insensitive(it, ["Num", "num", "NUM"]) or find_key_case_insensitive(it, ["Numéro", "numero"]) or ""
        num = normalize_num(num)
        if num:
            nums_in_json.add(num)

    with open(args.pdlist, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    orig_lines, map_num_to_index = parse_pdlist_lines(lines)

    modified_lines = orig_lines[:]  # copy

    def get_item_field(it: dict, keys: List[str]):
        return find_key_case_insensitive(it, keys)

    for it in items:
        # heuristics for keys
        num = get_item_field(it, ["Num", "num", "NUM"]) or get_item_field(it, ["Numéro", "numero"]) or ""
        num = normalize_num(num)
        if not num:
            # skip items without Num
            continue

        docid = get_item_field(it, ["DocId", "DocId", "docid", "DocID"]) or ""
        docid = str(docid).strip()
        pdatum_json = get_item_field(it, ["PDatum", "Pdatum", "pdatum"]) or ""
        pdatum_json = str(pdatum_json).strip()

        if num not in map_num_to_index:
            # create a new line for JSON-only items and mark source
            print(f"Num not found in PDatumListe.txt, will append: '{num}'")
            # use JSON pdatum for PDatum column; do not modify existing file PDatum entries
            new_pdatum = pdatum_json
            # canonicalize output PDatum to DD.MM.YYYY if possible
            p_out = ""
            if new_pdatum:
                # try parse ISO-like to dd.mm.yyyy
                try:
                    dt = datetime.strptime(new_pdatum, "%Y-%m-%d")
                    p_out = dt.strftime("%d.%m.%Y")
                except Exception:
                    p_out = new_pdatum
            # construct a line: PDatum; EDatum; Num; DocId; PDatumDiff; DocIdDiff; Only
            new_parts = [p_out, "", num, docid or "", "", "", "JSON_ONLY"]
            new_line = "; ".join(new_parts)
            if not new_line.endswith(";"):
                new_line = new_line + ";"
            modified_lines.append(new_line)
            # also register in the map to avoid duplicates
            map_num_to_index[num] = len(modified_lines) - 1
            # continue to next item
            continue

        idx = map_num_to_index[num]
        line = modified_lines[idx]
        parts = [p.strip() for p in line.split(";")]
        parts = ensure_parts_length(parts, 7)

        # parts indices: 0 PDatum, 1 EDatum, 2 Num, 3 DocId, 4 PDatumDiff, 5 DocIdDiff, 6 Only
        existing_docid = parts[3].strip()
        pdlist_pdatum = parts[0].strip()

        # If DocId missing in pdlist -> copy from JSON into DocId column
        if not existing_docid and docid:
            parts[3] = docid
            print(f"Copied DocId for Num {num}: {docid}")
        elif existing_docid and docid and existing_docid != docid:
            # different -> note erroneous value from JSON into DocIdDiff
            parts[5] = docid
            print(f"DocId mismatch for Num {num}: pdlist='{existing_docid}' json='{docid}' -> set DocIdDiff={docid}")

        # Compare PDatum: normalize formats (DD.MM.YYYY == YYYY-MM-DD)
        norm_pdlist = normalize_date_for_compare(pdlist_pdatum)
        norm_json = normalize_date_for_compare(pdatum_json)
        if norm_json and pdlist_pdatum and norm_json != norm_pdlist:
            # set the erroneous value from JSON in the PDatumDiff column
            parts[4] = pdatum_json
            print(f"PDatum mismatch for Num {num}: pdlist='{pdlist_pdatum}' json='{pdatum_json}' -> set PDatumDiff={pdatum_json}")

        # reconstruct line preserving semicolon separators and a trailing semicolon
        new_line = "; ".join(parts).rstrip()  # join with semicolon+space
        # ensure trailing semicolon as in original style
        if not new_line.endswith(";"):
            new_line = new_line + ";"
        modified_lines[idx] = new_line

    # mark lines present in PDList but not in JSON: set Only = 'PDLIST_ONLY'
    for orig_num, orig_idx in parse_pdlist_lines(orig_lines)[1].items():
        if orig_num and orig_num not in nums_in_json:
            parts = [p.strip() for p in modified_lines[orig_idx].split(";")]
            parts = ensure_parts_length(parts, 7)
            # set Only flag if empty
            if not parts[6]:
                parts[6] = "PDLIST_ONLY"
            new_line = "; ".join(parts).rstrip()
            if not new_line.endswith(";"):
                new_line = new_line + ";"
            modified_lines[orig_idx] = new_line

    # write output
    out_path = args.out or os.path.join(os.path.dirname(args.pdlist), "PDatumListe.updated.txt")
    if args.inplace:
        bak = args.pdlist + ".bak"
        shutil.copyfile(args.pdlist, bak)
        out_path = args.pdlist
        print(f"Backup created: {bak}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(modified_lines) + "\n")

    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
