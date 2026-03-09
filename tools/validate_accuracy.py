"""Compare spider output with ground truth PDatumListe.updated.txt.

Usage:
    python tools/validate_accuracy.py output/<run>.json [readme/PDatumListe.updated.txt]
"""
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GT = PROJECT_ROOT / 'readme' / 'PDatumListe.updated.txt'
DEFAULT_JSON = PROJECT_ROOT / 'output' / 'publications'


def parse_ground_truth(path: Path) -> dict:
    gt = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("vrai") or line.startswith("trier") or line.startswith("PDatum"):
                continue
            parts = [p.strip() for p in line.split(";")]
            if len(parts) >= 3 and parts[2]:
                pd_raw = parts[0]
                num = parts[2].strip()
                try:
                    dd, mm, yy = pd_raw.split(".")
                    iso_pd = f"{yy}-{mm}-{dd}"
                    gt[num] = iso_pd
                except ValueError:
                    pass
    return gt


def _find_latest_json(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob('publications_fribourg_*.json'), reverse=True)
    if not candidates:
        raise FileNotFoundError(f"Aucun fichier publications_fribourg_*.json dans {output_dir}")
    return candidates[0]


def main():
    if len(sys.argv) >= 2:
        json_path = Path(sys.argv[1])
    else:
        json_path = _find_latest_json(DEFAULT_JSON)

    gt_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else DEFAULT_GT

    print(f"Ground truth : {gt_path}")
    print(f"JSON spider  : {json_path}")
    print()

    gt = parse_ground_truth(gt_path)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    correct = 0
    wrong = []
    unmatched = []
    
    for item in data:
        num = item.get("Num", "").strip()
        spider_pd = item.get("PDatum", "")
        doc_id = item.get("DocId", "")[:16]
        
        if num and num in gt:
            if gt[num] == spider_pd:
                correct += 1
            else:
                wrong.append((num, doc_id, gt[num], spider_pd))
        else:
            unmatched.append((num, doc_id, spider_pd))
    
    total_matched = correct + len(wrong)
    if total_matched > 0:
        pct = 100 * correct / total_matched
    else:
        pct = 0
    
    print(f"=== Accuracy: {correct}/{total_matched} matched ({pct:.1f}%) ===")
    print(f"    Unmatched (no GT): {len(unmatched)}")
    print()
    
    if wrong:
        print("WRONG items:")
        for num, doc_id, expected, got in wrong:
            print(f"  {num} ({doc_id}): expected={expected}, got={got}")
    
    # Distribution comparison
    gt_counter = Counter(gt.values())
    spider_counter = Counter(item.get("PDatum", "") for item in data)
    
    all_dates = sorted(set(list(gt_counter.keys()) + list(spider_counter.keys())), reverse=True)
    # Filter to relevant window
    min_date = min(gt_counter.keys()) if gt_counter else ""
    relevant = [d for d in all_dates if d >= min_date]
    
    print("\n=== Distribution Comparison ===")
    print(f"  {'Date':<14} {'GT':>4} {'Spider':>7} {'Status'}")
    for d in relevant:
        g = gt_counter.get(d, 0)
        s = spider_counter.get(d, 0)
        if g == s:
            status = "OK"
        else:
            status = f"diff={s-g:+d}"
        print(f"  {d:<14} {g:4d} {s:7d}  {status}")

if __name__ == "__main__":
    main()
