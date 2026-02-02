import argparse
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Iterable


_RE_DDMMYYYY = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
_RE_RUN_HEADER = re.compile(r"\bdays\s*=?\s*(\d+)\b", re.IGNORECASE)
_RE_FINAL = re.compile(
    r"FINAL_DEBUG_SUMMARY\s+pages_processed=(\d+)\s+items_emitted=(\d+)", re.IGNORECASE
)
_RE_DOC = re.compile(
    r"\bDOC\s+(?P<docid>[0-9a-f]{32})\s+PDatum=(?P<pdatum>\d{4}-\d{2}-\d{2})\s+Eligible=(?P<eligible>True|False)\s+Yielded=(?P<yielded>True|False)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DocRow:
    docid: str
    pdatum: str  # ISO yyyy-mm-dd
    eligible: bool
    yielded: bool


@dataclass
class RunBlock:
    label: str
    days: int | None
    pages_processed: int | None = None
    items_emitted: int | None = None
    docs: list[DocRow] | None = None

    def __post_init__(self) -> None:
        if self.docs is None:
            self.docs = []


def _parse_ddmmyyyy_to_iso(value: str) -> str | None:
    m = _RE_DDMMYYYY.match(value.strip())
    if not m:
        return None
    dd, mm, yyyy = map(int, m.groups())
    return date(yyyy, mm, dd).isoformat()


def _parse_expected_dates(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        iso = _parse_ddmmyyyy_to_iso(line)
        if iso:
            out.append(iso)
    return out


def parse_liste_file(path: Path) -> tuple[list[str], list[RunBlock]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # Expected section: all dd.mm.yyyy entries before the first "run" mention.
    expected_lines: list[str] = []
    first_run_idx = None
    for i, line in enumerate(lines):
        if re.search(r"\brun\b", line, re.IGNORECASE) or re.search(r"\bcrawl\b", line, re.IGNORECASE):
            first_run_idx = i
            break
        expected_lines.append(line)
    expected = _parse_expected_dates(expected_lines)

    runs: list[RunBlock] = []
    current: RunBlock | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and (current.docs or current.pages_processed is not None):
            runs.append(current)
        current = None

    scan_lines = lines[first_run_idx:] if first_run_idx is not None else []
    for raw in scan_lines:
        line = raw.strip("\ufeff").strip()
        lower = line.lower()

        # Detect start of a new run block.
        # Accept both styles:
        # - "run pour days = 8 :"
        # - "crawl days=13" (no trailing ':')
        # The file may contain typos like "scrawl"; so we primarily key off "days".
        is_header_candidate = (
            "days" in lower
            and (":" in line or lower.startswith("crawl") or lower.startswith("run") or " crawl" in lower)
            and "[" not in line  # avoid log lines
            and not lower.startswith("202")  # avoid timestamps
        )
        if is_header_candidate:
            flush()
            m = _RE_RUN_HEADER.search(line)
            days = int(m.group(1)) if m else None
            current = RunBlock(label=line.replace("\r", ""), days=days)
            continue

        if current is None:
            continue

        mfinal = _RE_FINAL.search(line)
        if mfinal:
            current.pages_processed = int(mfinal.group(1))
            current.items_emitted = int(mfinal.group(2))
            continue

        mdoc = _RE_DOC.search(line)
        if mdoc:
            current.docs.append(
                DocRow(
                    docid=mdoc.group("docid").lower(),
                    pdatum=mdoc.group("pdatum"),
                    eligible=mdoc.group("eligible").lower() == "true",
                    yielded=mdoc.group("yielded").lower() == "true",
                )
            )
            continue

    flush()
    return expected, runs


def _compute_positional_accuracy(expected: list[str], got: list[str], n: int) -> tuple[int, int]:
    matches = 0
    total = 0
    for i in range(min(n, len(expected), len(got))):
        total += 1
        if expected[i] == got[i]:
            matches += 1
    return matches, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--liste",
        type=Path,
        default=Path("liste_PDatum.txt"),
        help="Path to liste_PDatum.txt",
    )
    ap.add_argument(
        "--top",
        type=int,
        default=100,
        help="Compare only the first N positions (UI order)",
    )
    ap.add_argument(
        "--show-mismatches",
        type=int,
        default=15,
        help="Show up to K first positional mismatches per run",
    )
    ap.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Optional: write a JSON report to this path",
    )
    args = ap.parse_args()

    expected, runs = parse_liste_file(args.liste)
    if not expected:
        raise SystemExit("No expected UI dates found in the top section of the file.")
    if not runs:
        raise SystemExit("No run blocks found (no FINAL_DEBUG_SUMMARY sections parsed).")

    print(f"Expected UI dates: {len(expected)}")
    print(f"Parsed run blocks:  {len(runs)}")
    print()

    report: dict = {
        "expected": {"count": len(expected), "dates": expected},
        "runs": [],
    }

    # Summary per run
    run_docs_by_label: dict[str, list[DocRow]] = {}
    for run in runs:
        docs = run.docs or []
        run_docs_by_label[run.label] = docs
        got_pd = [d.pdatum for d in docs]
        matches, total = _compute_positional_accuracy(expected, got_pd, args.top)
        # Order-insensitive overlap (multiset Jaccard-style numerator / N)
        top_n = min(args.top, len(expected), len(got_pd))
        expected_top = expected[:top_n]
        got_top = got_pd[:top_n]
        exp_c = Counter(expected_top)
        got_c = Counter(got_top)
        overlap = sum(min(exp_c[k], got_c.get(k, 0)) for k in exp_c)
        eligible_true = sum(1 for d in docs if d.eligible)
        yielded_true = sum(1 for d in docs if d.yielded)
        print(run.label)
        print(
            f"  days={run.days} pages_processed={run.pages_processed} items_emitted={run.items_emitted} docs_listed={len(docs)}"
        )
        acc = (matches / total * 100.0) if total else 0.0
        bag = (overlap / top_n * 100.0) if top_n else 0.0
        print(
            f"  eligible_true={eligible_true} yielded_true={yielded_true} positional_match={matches}/{total} ({acc:.1f}%) bag_match={overlap}/{top_n} ({bag:.1f}%)"
        )

        run_entry = {
            "label": run.label,
            "days": run.days,
            "pages_processed": run.pages_processed,
            "items_emitted": run.items_emitted,
            "docs_listed": len(docs),
            "eligible_true": eligible_true,
            "yielded_true": yielded_true,
            "top_n_compared": total,
            "positional_matches": matches,
            "positional_accuracy": (matches / total) if total else None,
            "bag_overlap": overlap,
            "bag_accuracy": (overlap / top_n) if top_n else None,
            "docs": [
                {
                    "index": i + 1,
                    "docid": d.docid,
                    "pdatum": d.pdatum,
                    "eligible": d.eligible,
                    "yielded": d.yielded,
                    "expected_pdatum": expected[i] if i < len(expected) else None,
                    "positional_match": (expected[i] == d.pdatum) if i < len(expected) else None,
                }
                for i, d in enumerate(docs[: args.top])
            ],
        }
        report["runs"].append(run_entry)

        if args.show_mismatches:
            shown = 0
            for i in range(min(args.top, len(expected), len(got_pd))):
                if expected[i] != got_pd[i]:
                    shown += 1
                    docid = docs[i].docid
                    print(
                        f"    mismatch[{i+1:03d}] expected={expected[i]} got={got_pd[i]} docid={docid} eligible={docs[i].eligible}"
                    )
                    if shown >= args.show_mismatches:
                        break
        print()

    # Cross-run consistency for DocId -> PDatum
    docid_to_pdatum_by_run: dict[str, dict[str, str]] = defaultdict(dict)
    for label, docs in run_docs_by_label.items():
        for d in docs:
            docid_to_pdatum_by_run[d.docid][label] = d.pdatum

    # DocIds appearing in 2+ runs, with >1 distinct PDatum.
    unstable = []
    for docid, mp in docid_to_pdatum_by_run.items():
        if len(mp) < 2:
            continue
        if len(set(mp.values())) > 1:
            unstable.append((docid, mp))

    if unstable:
        print("DocId with non-deterministic PDatum across the parsed runs:")
        for docid, mp in sorted(unstable, key=lambda x: x[0])[:50]:
            counts = Counter(mp.values())
            variants = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
            print(f"  {docid}: {variants}")
        if len(unstable) > 50:
            print(f"  ... ({len(unstable)-50} more)")
        print()

    # Infer "correctness" under the assumption that each run's DOC order matches UI order.
    docid_match_runs: dict[str, list[str]] = defaultdict(list)
    docid_appear_runs: dict[str, set[str]] = defaultdict(set)
    for run in runs:
        docs = run.docs or []
        top_n = min(args.top, len(expected), len(docs))
        for i in range(top_n):
            docid_appear_runs[docs[i].docid].add(run.label)
            if docs[i].pdatum == expected[i]:
                docid_match_runs[docs[i].docid].append(run.label)

    partially = []
    for docid, appear_in in docid_appear_runs.items():
        if len(appear_in) < 2:
            continue
        matched_in = set(docid_match_runs.get(docid, []))
        if matched_in and matched_in != appear_in:
            partially.append((docid, appear_in, matched_in))

    if partially:
        print("DocId that match UI-position PDatum in some runs but not all:")
        for docid, appear_in, matched_in in sorted(partially, key=lambda x: x[0])[:50]:
            print(
                f"  {docid}: matched_in={len(matched_in)}/{len(appear_in)}"
                f" (matched={sorted(matched_in)}, missing={sorted(appear_in - matched_in)})"
            )
        if len(partially) > 50:
            print(f"  ... ({len(partially)-50} more)")
        print()

    if args.out_json is not None:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Wrote JSON report: {args.out_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
