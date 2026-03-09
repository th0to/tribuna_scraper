"""Compare PDatum entre la reference UI (PDatumListe.txt) et le JSON du spider.

Usage:
    python tools/compare_pdatum_ui_vs_spider.py [pdatumliste.txt] [publications.json]

Par defaut:
    - PDatumListe: tools/PDatumListe.txt
    - JSON: dernier fichier publications_fribourg_*.json dans output/publications/
"""

import json
import sys
from pathlib import Path
from datetime import datetime


def parse_pdatumliste(filepath: str) -> dict:
    """Parse PDatumListe.txt -> {docid: {pdatum, edatum, num}}"""
    entries = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            # Skip header lines and empty lines
            if not line or lineno <= 3:
                continue
            parts = [p.strip() for p in line.split(';')]
            if len(parts) < 4:
                continue
            pdatum_raw, edatum_raw, num, docid = parts[0], parts[1], parts[2], parts[3]
            if not docid:
                continue
            # Convert DD.MM.YYYY -> YYYY-MM-DD
            pdatum_iso = convert_ch_to_iso(pdatum_raw)
            edatum_iso = convert_ch_to_iso(edatum_raw) if edatum_raw else ''
            entries[docid] = {
                'pdatum': pdatum_iso,
                'edatum': edatum_iso,
                'num': num,
            }
    return entries


def convert_ch_to_iso(date_ch: str) -> str:
    """DD.MM.YYYY -> YYYY-MM-DD"""
    if not date_ch or '.' not in date_ch:
        return ''
    try:
        parts = date_ch.split('.')
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    except (IndexError, ValueError):
        return ''


def parse_json(filepath: str) -> dict:
    """Parse le JSON du spider -> {docid: {pdatum, edatum, num}}"""
    with open(filepath, 'r', encoding='utf-8') as f:
        items = json.load(f)
    entries = {}
    for item in items:
        docid = item.get('DocId', '')
        if not docid:
            continue
        entries[docid] = {
            'pdatum': item.get('PDatum', ''),
            'edatum': item.get('EDatum', ''),
            'num': item.get('Num', ''),
        }
    return entries


def find_latest_json(output_dir: str) -> str:
    """Trouve le dernier JSON Fribourg dans output/publications/."""
    p = Path(output_dir)
    candidates = sorted(p.glob('publications_fribourg_*.json'), reverse=True)
    if not candidates:
        raise FileNotFoundError(f"Aucun fichier publications_fribourg_*.json dans {output_dir}")
    return str(candidates[0])


def main():
    base_dir = Path(__file__).parent.parent

    # Paths
    if len(sys.argv) >= 3:
        pdatum_path = sys.argv[1]
        json_path = sys.argv[2]
    else:
        pdatum_path = str(base_dir / 'readme' / 'PDatumListe.txt')
        json_path = find_latest_json(str(base_dir / 'output' / 'publications'))

    print(f"Reference UI : {pdatum_path}")
    print(f"JSON spider  : {json_path}")
    print("=" * 90)

    # Parse
    ui_entries = parse_pdatumliste(pdatum_path)
    spider_entries = parse_json(json_path)

    print(f"Items dans PDatumListe (avec DocId) : {len(ui_entries)}")
    print(f"Items dans JSON spider              : {len(spider_entries)}")
    print("=" * 90)

    # Compare
    ok_count = 0
    mismatch_count = 0
    missing_in_spider = 0
    new_in_spider = 0

    mismatches = []
    missing = []

    all_docids = set(ui_entries.keys()) | set(spider_entries.keys())

    for docid in sorted(all_docids):
        in_ui = docid in ui_entries
        in_spider = docid in spider_entries

        if in_ui and in_spider:
            ui_pd = ui_entries[docid]['pdatum']
            sp_pd = spider_entries[docid]['pdatum']
            num = ui_entries[docid]['num'] or spider_entries[docid]['num']

            if ui_pd == sp_pd:
                ok_count += 1
            else:
                mismatch_count += 1
                mismatches.append({
                    'docid': docid,
                    'num': num,
                    'ui_pdatum': ui_pd,
                    'spider_pdatum': sp_pd,
                    'ui_edatum': ui_entries[docid]['edatum'],
                    'spider_edatum': spider_entries[docid]['edatum'],
                })
        elif in_ui and not in_spider:
            missing_in_spider += 1
            missing.append({
                'docid': docid,
                'num': ui_entries[docid]['num'],
                'ui_pdatum': ui_entries[docid]['pdatum'],
            })
        else:
            new_in_spider += 1

    # Report
    print(f"\n--- RESULTATS ---")
    print(f"  OK (PDatum identique)       : {ok_count}")
    print(f"  MISMATCH (PDatum different) : {mismatch_count}")
    print(f"  ABSENT du spider            : {missing_in_spider}")
    print(f"  NOUVEAU dans spider         : {new_in_spider}")
    print()

    if mismatches:
        print("=" * 90)
        print(f"DETAIL DES {mismatch_count} MISMATCH:")
        print("-" * 90)
        print(f"{'DocId (8ch)':<14} {'Num':<18} {'UI PDatum':<14} {'Spider PDatum':<16} {'Ecart'}")
        print("-" * 90)
        for m in sorted(mismatches, key=lambda x: x['ui_pdatum'], reverse=True):
            # Compute ecart en jours
            try:
                ui_d = datetime.strptime(m['ui_pdatum'], '%Y-%m-%d')
                sp_d = datetime.strptime(m['spider_pdatum'], '%Y-%m-%d')
                delta = (sp_d - ui_d).days
                ecart = f"+{delta}j" if delta > 0 else f"{delta}j"
            except ValueError:
                ecart = "?"
            print(f"{m['docid'][:12]:<14} {m['num']:<18} {m['ui_pdatum']:<14} {m['spider_pdatum']:<16} {ecart}")
        print()

    if missing:
        print("=" * 90)
        print(f"ITEMS UI ABSENTS DU SPIDER ({missing_in_spider}):")
        print("-" * 90)
        for m in sorted(missing, key=lambda x: x['ui_pdatum'], reverse=True):
            print(f"  {m['docid'][:12]}  {m['num']:<18}  PDatum UI: {m['ui_pdatum']}")
        print()

    if new_in_spider > 0:
        print("=" * 90)
        print(f"ITEMS NOUVEAUX DANS SPIDER ({new_in_spider}):")
        print("  (publies apres la mise a jour de PDatumListe ou non references dans PDatumListe)")
        print("-" * 90)
        for docid in sorted(spider_entries.keys()):
            if docid not in ui_entries:
                sp = spider_entries[docid]
                print(f"  {docid[:12]}  {sp['num']:<18}  PDatum: {sp['pdatum']}")
        print()

    # Exit code
    if mismatch_count > 0:
        print(f"CONCLUSION: {mismatch_count} PDatum incorrects detectes.")
        sys.exit(1)
    else:
        print("CONCLUSION: Tous les PDatum sont corrects.")
        sys.exit(0)


if __name__ == '__main__':
    main()
