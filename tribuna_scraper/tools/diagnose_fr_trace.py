from __future__ import annotations

import json
import re
from pathlib import Path


def _parse_ui_pdatum_liste(path: Path) -> dict[str, str]:
    """Return mapping doc_id -> YYYY-MM-DD from PDatumListe.txt."""
    mapping: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\S+))?$", line)
        if not m:
            continue
        iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        doc_id = m.group(4)
        if doc_id:
            mapping[doc_id] = iso
    return mapping


def _parse_trace_dates_final(trace_path: Path) -> tuple[dict[str, str | None], dict[str, dict[str, str | None]]]:
    """Return (doc_id -> final_pdatum, doc_id -> hint_applied record)."""
    final: dict[str, str | None] = {}
    hint: dict[str, dict[str, str | None]] = {}
    for raw in trace_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        doc_id = obj.get("doc_id")
        stage = obj.get("stage")
        if not doc_id or not stage:
            continue
        if stage == "pdatum_hint_applied":
            hint[doc_id] = {
                "old": obj.get("old_pdatum"),
                "hint": obj.get("hint"),
                "new": obj.get("new_pdatum"),
            }
        if stage == "dates_final":
            final[doc_id] = obj.get("pdatum")
    return final, hint


def _parse_trace_details(trace_path: Path) -> dict[str, dict[str, object]]:
    """Extract extra per-doc debug info from trace (candidates, raw matches)."""
    details: dict[str, dict[str, object]] = {}
    for raw in trace_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        doc_id = obj.get("doc_id")
        stage = obj.get("stage")
        if not doc_id or not stage:
            continue
        d = details.setdefault(doc_id, {})
        if stage == "pdatum_candidates":
            d["candidates"] = obj.get("candidates")
            d["pick_mode"] = obj.get("pick_mode")
            d["row_end"] = obj.get("row_end")
            d["future_cutoff_pd"] = obj.get("future_cutoff_pd")
        elif stage == "pdatum_raw_fallback_matches":
            # Keep last seen window/matches for this doc.
            d["raw_window_len"] = obj.get("window_len")
            d["raw_matches"] = obj.get("matches")
        elif stage == "dates_start":
            d["id_pos"] = obj.get("id_pos")
            d["row_end_hint"] = obj.get("row_end_hint")
    return details


def _parse_items_json(items_path: Path) -> dict[str, str | None]:
    items = json.loads(items_path.read_text(encoding="utf-8", errors="ignore"))
    out: dict[str, str | None] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        doc_id = it.get("ID") or it.get("DocId") or it.get("doc_id")
        pd = it.get("PDatum") or it.get("publikationsdatum") or it.get("publication_date")
        if doc_id:
            out[str(doc_id)] = pd
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[1]

    trace = root / "output" / "date_traces" / "date_trace_fribourg_20260131_135614.jsonl"
    ui = root / "PDatumListe.txt"
    items_json = root / "output" / "publications" / "publications_fribourg_20260131_145608.json"

    for p in (trace, ui, items_json):
        if not p.exists():
            raise SystemExit(f"Missing file: {p}")

    ui_map = _parse_ui_pdatum_liste(ui)
    trace_final, trace_hint_applied = _parse_trace_dates_final(trace)
    trace_details = _parse_trace_details(trace)
    item_map = _parse_items_json(items_json)

    in_both = sorted(set(trace_final) & set(ui_map))

    mismatches: list[tuple[str, str, str | None, str | None, dict[str, str | None] | None]] = []
    for doc_id in in_both:
        ui_d = ui_map.get(doc_id)
        tr = trace_final.get(doc_id)
        out_pd = item_map.get(doc_id)
        if tr != ui_d:
            mismatches.append((doc_id, ui_d, tr, out_pd, trace_hint_applied.get(doc_id)))

    print("=== Summary ===")
    print(f"UI docids: {len(ui_map)}")
    print(f"Trace docids (dates_final): {len(trace_final)}")
    print(f"Trace docids with hint_applied: {len(trace_hint_applied)}")
    print(f"DocIds in trace ∩ UI: {len(in_both)}")
    print(f"Mismatches trace_final vs UI: {len(mismatches)}")

    print("\n=== Sample mismatches (first 25) ===")
    for doc_id, ui_d, tr, out_pd, hint in mismatches[:25]:
        print(f"- {doc_id}  UI={ui_d}  TRACE={tr}  OUT={out_pd}  HINT={hint}")

    focus = [m for m in mismatches if m[1] == "2026-01-13" and m[2] == "2026-01-30"]
    print(f"\n=== Focus: UI=2026-01-13 but TRACE=2026-01-30 (count={len(focus)}) ===")
    for doc_id, ui_d, tr, out_pd, hint in focus:
        det = trace_details.get(doc_id, {})
        print(f"- {doc_id} OUT={out_pd} HINT={hint}")
        if det:
            print(f"    candidates={det.get('candidates')} pick_mode={det.get('pick_mode')} row_end={det.get('row_end')} future_cutoff_pd={det.get('future_cutoff_pd')}")
            print(f"    raw_matches={det.get('raw_matches')} raw_window_len={det.get('raw_window_len')} row_end_hint={det.get('row_end_hint')} id_pos={det.get('id_pos')}")


if __name__ == "__main__":
    main()
