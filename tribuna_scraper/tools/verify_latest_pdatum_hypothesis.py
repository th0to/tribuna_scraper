#!/usr/bin/env python3
"""Verify hypothesis: in each `dates_start` trace event, PDatum equals the most recent date
present in `tokens_around`.

Compares predicted dates (max ISO date found) to the ground-truth UI order list.

Usage (PowerShell):
  python tools/verify_latest_pdatum_hypothesis.py \
    --trace output/date_traces/date_trace_fribourg_20260126_123059.jsonl \
    --truth PDatumListe.txt
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--trace", required=True, help="Path to date_trace_*.jsonl")
    p.add_argument("--truth", required=True, help="Path to PDatumListe.txt")
    p.add_argument(
        "--respect-max-date",
        action="store_true",
        help="Ignore ISO dates > max_date from the trace row (filters future dates)",
    )
    p.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional cap on how many dates_start rows to compare",
    )
    p.add_argument(
        "--show-mismatches",
        type=int,
        default=25,
        help="How many mismatches to print (0 = none)",
    )
    return p.parse_args()


def ddmmyyyy_to_iso(s: str) -> Optional[str]:
    s = s.strip()
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%d.%m.%Y")
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d")


DOCID_RE = re.compile(r"\b[a-f0-9]{32}\b", re.IGNORECASE)


@dataclass(frozen=True)
class Truth:
    ordered_dates: list[str]
    docid_to_date: dict[str, str]


def load_truth(path: Path) -> Truth:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    ordered: list[str] = []
    mapping: dict[str, str] = {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # date at the beginning of the line (format dd.mm.yyyy)
        date_token = line.split()[0] if line else ""
        iso = ddmmyyyy_to_iso(date_token)
        if not iso:
            continue

        ordered.append(iso)

        m = DOCID_RE.search(line)
        if m:
            mapping[m.group(0).lower()] = iso

    return Truth(ordered_dates=ordered, docid_to_date=mapping)


@dataclass(frozen=True)
class PredRow:
    idx: int
    doc_id: str
    predicted: Optional[str]
    candidates: list[str]


def iter_dates_start_rows(trace_path: Path, *, respect_max_date: bool) -> Iterable[PredRow]:
    idx = 0
    for raw in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if obj.get("stage") != "dates_start":
            continue

        doc_id = obj.get("doc_id") or ""
        max_date = obj.get("max_date")
        if not isinstance(max_date, str) or not ISO_DATE_RE.fullmatch(max_date):
            max_date = None
        tokens = obj.get("tokens_around") or []

        candidates: list[str] = []
        for tok in tokens:
            t = tok.get("t") if isinstance(tok, dict) else None
            if not isinstance(t, str):
                continue
            for m in ISO_DATE_RE.findall(t):
                if m != "0000-00-00":
                    if respect_max_date and max_date is not None and m > max_date:
                        continue
                    candidates.append(m)

        # De-dup while preserving order
        seen: set[str] = set()
        dedup: list[str] = []
        for d in candidates:
            if d in seen:
                continue
            seen.add(d)
            dedup.append(d)

        predicted = max(dedup) if dedup else None

        yield PredRow(idx=idx, doc_id=doc_id, predicted=predicted, candidates=dedup)
        idx += 1


def main() -> int:
    args = parse_args()
    trace_path = Path(args.trace)
    truth_path = Path(args.truth)

    truth = load_truth(truth_path)
    preds: list[PredRow] = []

    for row in iter_dates_start_rows(trace_path, respect_max_date=args.respect_max_date):
        preds.append(row)
        if args.max_rows is not None and len(preds) >= args.max_rows:
            break

    n = min(len(truth.ordered_dates), len(preds))
    if n == 0:
        print("No comparable rows found.")
        print(f"truth_count={len(truth.ordered_dates)} preds_count={len(preds)}")
        return 2

    correct = 0
    missing_pred = 0
    mismatches: list[tuple[int, str, Optional[str], str, list[str]]] = []

    for i in range(n):
        expected = truth.ordered_dates[i]
        predicted = preds[i].predicted
        if predicted is None:
            missing_pred += 1
        if predicted == expected:
            correct += 1
        else:
            mismatches.append((i, preds[i].doc_id, predicted, expected, preds[i].candidates))

    acc = correct / n

    print("Hypothesis check: PDatum == max(date in tokens_around) per dates_start")
    print(f"trace={trace_path}")
    print(f"truth={truth_path}")
    print(f"compared_rows={n} (truth={len(truth.ordered_dates)} preds={len(preds)})")
    print(f"missing_pred={missing_pred}")
    print(f"accuracy={acc:.4f} ({correct}/{n})")

    if truth.docid_to_date:
        mapped_total = 0
        mapped_correct = 0
        mapped_missing = 0
        mapped_mismatches: list[tuple[str, Optional[str], str, list[str]]] = []

        first_by_docid: dict[str, PredRow] = {}
        for row in preds:
            doc_id = (row.doc_id or "").lower()
            expected_m = truth.docid_to_date.get(doc_id)
            if expected_m is None:
                continue

            if doc_id not in first_by_docid:
                first_by_docid[doc_id] = row

            mapped_total += 1
            if row.predicted is None:
                mapped_missing += 1
            if row.predicted == expected_m:
                mapped_correct += 1
            else:
                mapped_mismatches.append((doc_id, row.predicted, expected_m, row.candidates))

        if mapped_total:
            mapped_acc = mapped_correct / mapped_total
            print("\nDocId-mapped check (using doc_id->date lines in truth file)")
            print(f"mapped_rows={mapped_total}")
            print(f"mapped_missing_pred={mapped_missing}")
            print(f"mapped_accuracy={mapped_acc:.4f} ({mapped_correct}/{mapped_total})")

            unique_total = len(first_by_docid)
            unique_correct = 0
            unique_missing = 0
            unique_mismatches: list[tuple[str, Optional[str], str, list[str]]] = []
            for doc_id, row in first_by_docid.items():
                expected_m = truth.docid_to_date.get(doc_id)
                if expected_m is None:
                    continue
                if row.predicted is None:
                    unique_missing += 1
                if row.predicted == expected_m:
                    unique_correct += 1
                else:
                    unique_mismatches.append((doc_id, row.predicted, expected_m, row.candidates))

            if unique_total:
                unique_acc = unique_correct / unique_total
                print("\nUnique-docId check (first occurrence per doc_id)")
                print(f"unique_docids={unique_total}")
                print(f"unique_missing_pred={unique_missing}")
                print(f"unique_accuracy={unique_acc:.4f} ({unique_correct}/{unique_total})")

                if args.show_mismatches and unique_mismatches:
                    print("\nFirst unique docId mismatches:")
                    for (doc_id, predicted, expected_m, candidates) in unique_mismatches[
                        : args.show_mismatches
                    ]:
                        print(
                            f"doc_id={doc_id} expected={expected_m} predicted={predicted} candidates={candidates}"
                        )

            if args.show_mismatches and mapped_mismatches:
                print("\nFirst docId mismatches:")
                for (doc_id, predicted, expected_m, candidates) in mapped_mismatches[
                    : args.show_mismatches
                ]:
                    print(
                        f"doc_id={doc_id} expected={expected_m} predicted={predicted} candidates={candidates}"
                    )

    if args.show_mismatches and mismatches:
        print("\nFirst mismatches:")
        for (i, doc_id, predicted, expected, candidates) in mismatches[: args.show_mismatches]:
            print(
                f"#{i:03d} doc_id={doc_id} expected={expected} predicted={predicted} candidates={candidates}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
