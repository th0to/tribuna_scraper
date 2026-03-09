"""
STRING-TABLE-BASED PDatum/EDatum extraction algorithm.

CONFIRMED PATTERNS (from analyzing pages 2 and 3):
====================================================

1. STRING TABLE ORDER = UI ITEM ORDER
   Items appear sequentially: Item 1, 2, ..., 20.

2. PER-ITEM STRUCTURE in string table:
   [GCode(if court changes)] → DOCID → [descriptions] → NUM → [EDatum(if new)] → [appeal_date(optional)]

3. PDatum APPEARS ONCE PER WAVE:
   - First wave: between SEP("-") and Item 2's DOCID
   - Subsequent waves: after 1st-item-of-new-wave's data, before 2nd-item's DOCID

4. EDatum DEDUPLICATION:
   If an item's EDatum value was already used earlier in the ST, it's NOT repeated.
   The item just has DOCID → NUM with no date following.

5. UNKNOWN DATES (appeal deadlines):
   Future dates after some items' EDatum. Neither PDatum nor EDatum for any known item.

6. ZERO DATE (0000-00-00):
   Placeholder for missing/null values.

ALGORITHM:
==========
1. Walk ST, identify items by DOCID → NUM pairs (in order)
2. Find first PDatum: the DATE between SEP and Item 2's DOCID
3. For each item, look at dates after its NUM and before next item's start:
   - First date right after NUM → EDatum (if new)
   - Remaining dates: classify as appeal dates or PDatum
4. PDatum appears only at wave boundaries (between items where publication date changes)
5. Items inherit current wave's PDatum until next PDatum is found
"""
import re
import sys
from pathlib import Path
from datetime import date, timedelta

RE_DOCID = re.compile(r'^[0-9a-f]{32}$')
RE_NUM   = re.compile(r'^\d{1,4}\s+\d{4}\s+\d+$')
RE_DATE  = re.compile(r'^(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
RE_ZERO  = re.compile(r'^0{4}-0{2}-0{2}$')
RE_GCODE = re.compile(r'^\d{3}$')

def _skip_ws(t, i):
    while i < len(t) and t[i] in ' \t\r\n': i += 1
    return i

def _pstr(t, i):
    i += 1; buf = []; n = len(t)
    while i < n:
        c = t[i]
        if c == '"': return ''.join(buf), i + 1
        if c == '\\':
            i += 1
            if i >= n: break
            e = t[i]
            esc = {'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','"':'"','\\':'\\','/':'/'}
            if e in esc: buf.append(esc[e])
            elif e in ('u', 'x'):
                ln = 4 if e == 'u' else 2
                try: buf.append(chr(int(t[i+1:i+1+ln], 16))); i += ln
                except: buf.append(f'\\{e}' + t[i+1:i+1+ln]); i += ln
            else: buf.append(e)
            i += 1; continue
        buf.append(c); i += 1
    raise ValueError("Unterminated string")

def _pnum(t, i):
    j = i
    if j < len(t) and t[j] == '-': j += 1
    has_dot = False
    while j < len(t):
        c = t[j]
        if c.isdigit(): j += 1
        elif c == '.' and not has_dot: has_dot = True; j += 1
        else: break
    r = t[i:j]
    return (float(r) if has_dot else int(r)), j

def _pval(t, i):
    i = _skip_ws(t, i)
    if i >= len(t): raise ValueError("End of input")
    c = t[i]
    if c == '[':
        i += 1; arr = []; i = _skip_ws(t, i)
        if i < len(t) and t[i] == ']': return arr, i + 1
        while True:
            v, i = _pval(t, i); arr.append(v)
            i = _skip_ws(t, i)
            if i >= len(t): raise ValueError("Unterminated array")
            if t[i] == ',': i += 1; continue
            if t[i] == ']': return arr, i + 1
            raise ValueError(f"Unexpected at {i}: {t[i:i+30]!r}")
    if c == '"': return _pstr(t, i)
    if c.isdigit() or c == '-': return _pnum(t, i)
    raise ValueError(f"Unknown at {i}: {t[i:i+30]!r}")

def parse_gwt(raw: str) -> list:
    t = raw.strip()
    ok = t.find('//OK')
    if ok < 0: raise ValueError("No //OK")
    br = t.find('[', ok)
    val, _ = _pval(t, br)
    return val

def find_string_table(parsed: list) -> list:
    best = None; bl = 5
    def walk(o):
        nonlocal best, bl
        if isinstance(o, list):
            if len(o) >= bl and all(isinstance(x, str) for x in o):
                best = o; bl = len(o); return
            for item in o: walk(item)
    walk(parsed)
    return best

def load_ground_truth(path: str) -> dict:
    gt = {}
    def conv(d):
        try: dd, mm, yy = d.split('.'); return f'{yy}-{mm}-{dd}'
        except: return d
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or not line[0].isdigit(): continue
            parts = [p.strip() for p in line.split(';')]
            if len(parts) < 4: continue
            pd, ed, num, docid = parts[:4]
            if not RE_NUM.match(num): continue
            gt[num] = {'pdatum': conv(pd), 'edatum': conv(ed), 'docid': docid}
            gt[docid] = {'pdatum': conv(pd), 'edatum': conv(ed), 'num': num}
    return gt


def parse_date(s: str):
    """Parse YYYY-MM-DD to date object."""
    try:
        y, m, d = s.split('-')
        return date(int(y), int(m), int(d))
    except:
        return None


def classify_st(st: list):
    """Classify each ST entry."""
    result = []
    for idx, val in enumerate(st):
        if RE_DOCID.match(val):
            result.append((idx, 'DOCID', val))
        elif RE_NUM.match(val):
            result.append((idx, 'NUM', val))
        elif RE_ZERO.match(val):
            result.append((idx, 'ZDATE', val))
        elif RE_DATE.match(val):
            result.append((idx, 'DATE', val))
        elif val == '-':
            result.append((idx, 'SEP', val))
        elif RE_GCODE.match(val):
            result.append((idx, 'GCODE', val))
    return result


def extract_items_from_st(st: list, scrape_date: date | None = None):
    """
    Extract items with PDatum and EDatum from string table ordering.
    
    ALGORITHM:
    1. Walk ST, pair each DOCID with its nearest following NUM → item list
    2. For each consecutive pair (item_i, item_{i+1}), examine dates between them
    3. First date right after item_i's NUM → item_i's EDatum (if it's a plausible past date)
    4. LAST date before item_{i+1}'s start that is NOT EDatum and NOT future → PDatum candidate
    5. If PDatum candidate differs from current wave PDatum → new wave
    6. Items inherit current_pdatum until next wave change
    
    Returns list of dicts: [{docid, num, pdatum, edatum, pdatum_source}, ...]
    """
    classified = classify_st(st)
    
    # Step 1: Find all DOCIDs, NUMs, DATEs, SEPs in order
    docid_entries = [(idx, val) for idx, typ, val in classified if typ == 'DOCID']
    num_entries = [(idx, val) for idx, typ, val in classified if typ == 'NUM']
    date_entries = [(idx, val) for idx, typ, val in classified if typ == 'DATE']
    sep_entries = [idx for idx, typ, val in classified if typ == 'SEP']
    
    # Step 2: Build item list by pairing DOCIDs with their nearest following NUM
    items = []
    used_nums = set()
    for didx, docid in docid_entries:
        best_num = None
        for nidx, num in num_entries:
            if nidx > didx and nidx not in used_nums:
                best_num = (nidx, num)
                break
        if best_num:
            used_nums.add(best_num[0])
            items.append({
                'docid': docid,
                'num': best_num[1],
                'docid_idx': didx,
                'num_idx': best_num[0],
                'edatum': None,
                'pdatum': None,
                'pdatum_source': 'none',
            })
    
    if not items:
        return items
    
    # Step 3: For each item, assign EDatum using the +1 RULE:
    #         EDatum is at ST[NUM_idx + 1] if and only if that entry is a DATE.
    #         This is confirmed across all pages: EDatum is always right after NUM in the ST.
    for i, item in enumerate(items):
        num_idx = item['num_idx']
        if num_idx + 1 < len(st):
            candidate = st[num_idx + 1]
            if RE_DATE.match(candidate):
                item['edatum'] = candidate
    
    # Step 4: Detect PDatum for each "between-items" zone
    # Walk consecutive pairs and find the LAST non-EDatum, non-future, non-zero date
    # between item_i's NUM and item_{i+1}'s start. That's the PDatum (if different from current).
    
    current_pdatum = None
    
    for i in range(len(items)):
        num_idx = items[i]['num_idx']
        
        if i + 1 < len(items):
            next_start = item_start_idx(items[i + 1], classified)
        else:
            next_start = len(st)
        
        # All dates between this item's NUM and next item's start
        zone_dates = [(idx, val) for idx, val in date_entries if num_idx < idx < next_start]
        
        # Filter: remove EDatum (first date), remove future dates, remove zero dates
        pdatum_candidates = []
        for idx, val in zone_dates:
            if val == items[i]['edatum']:
                continue  # Skip EDatum (it's the first date usually)
            d = parse_date(val)
            if not d:
                continue
            if scrape_date and d > scrape_date + timedelta(days=5):
                continue  # Future date → appeal deadline
            pdatum_candidates.append((idx, val))
        
        # Take the LAST candidate → PDatum (it's closest to the next item's start)
        if pdatum_candidates:
            new_pd = pdatum_candidates[-1][1]
            new_pd_date = parse_date(new_pd)
            cp = parse_date(current_pdatum) if current_pdatum else None
            
            # PDatum must be strictly LESS than current (waves are ordered newest→oldest)
            if new_pd != current_pdatum:
                if cp is None or (new_pd_date and new_pd_date < cp):
                    current_pdatum = new_pd
                    items[i]['pdatum'] = current_pdatum
                    items[i]['pdatum_source'] = 'wave_change'
                else:
                    # Date is >= current PDatum → NOT a valid wave change
                    # (could be an appeal date or other metadata)
                    items[i]['pdatum'] = current_pdatum
                    items[i]['pdatum_source'] = 'wave_inherit'
            else:
                items[i]['pdatum'] = current_pdatum
                items[i]['pdatum_source'] = 'explicit_in_zone'
        else:
            # No PDatum in this zone → inherit current wave
            items[i]['pdatum'] = current_pdatum
            if items[i]['pdatum_source'] == 'none':
                items[i]['pdatum_source'] = 'wave_inherit'
    
    return items


def item_start_idx(item: dict, classified: list) -> int:
    """Find the earliest ST index for an item (GCode before its DOCID, or DOCID itself)."""
    docid_idx = item['docid_idx']
    # Check if there's a GCode right before the DOCID
    for idx, typ, val in classified:
        if typ == 'GCODE' and idx < docid_idx:
            # Check if this GCode is closer to this DOCID than any previous DOCID
            # Simple: within 5 positions before DOCID
            if docid_idx - idx <= 5:
                return idx
    return docid_idx


def validate_against_gt(items: list, gt: dict, label: str):
    """Compare extracted items against ground truth."""
    print(f"\n{'='*100}")
    print(f"VALIDATION: {label}")
    print(f"{'='*100}")
    print(f"{'#':>3} {'Num':20} {'PDatum_ext':12} {'PDatum_GT':12} {'EDatum_ext':12} {'EDatum_GT':12} {'PD_src':15} {'OK?'}")
    print(f"{'-'*3} {'-'*20} {'-'*12} {'-'*12} {'-'*12} {'-'*12} {'-'*15} {'-'*4}")
    
    correct_pd = 0
    correct_ed = 0
    total = 0
    errors = []
    
    for i, item in enumerate(items):
        num = item['num']
        ext_pd = item['pdatum'] or '?'
        ext_ed = item['edatum'] or '?'
        pd_src = item['pdatum_source']
        
        gt_info = gt.get(num, gt.get(item['docid'], {}))
        gt_pd = gt_info.get('pdatum', '?')
        gt_ed = gt_info.get('edatum', '?')
        
        pd_ok = ext_pd == gt_pd
        ed_ok = ext_ed == gt_ed
        
        total += 1
        if pd_ok: correct_pd += 1
        if ed_ok: correct_ed += 1
        
        status = '✓' if pd_ok else '✗'
        if not pd_ok:
            errors.append((i+1, num, ext_pd, gt_pd, pd_src))
        
        # Only show details for errors or first/last items
        if not pd_ok or i < 2 or i >= len(items) - 2:
            print(f"{i+1:3d} {num:20} {ext_pd:12} {gt_pd:12} {ext_ed:12} {gt_ed:12} {pd_src:15} {status}")
    
    print(f"\n  PDatum accuracy: {correct_pd}/{total} = {100*correct_pd/total:.1f}%")
    print(f"  EDatum accuracy: {correct_ed}/{total} = {100*correct_ed/total:.1f}%")
    
    if errors:
        print(f"\n  PDatum ERRORS:")
        for num_i, num, ext, gt_v, src in errors:
            print(f"    Item #{num_i} {num}: extracted={ext} vs GT={gt_v} (source: {src})")
    
    return correct_pd, correct_ed, total


def analyze_page(raw: str, gt: dict, label: str, scrape_date: date | None = None):
    parsed = parse_gwt(raw)
    st = find_string_table(parsed)
    if not st:
        print(f"ERROR: no ST for {label}")
        return
    
    print(f"\n{'='*100}")
    print(f"EXTRACTION: {label}")
    print(f"String table: {len(st)} entries")
    print(f"{'='*100}")
    
    items = extract_items_from_st(st, scrape_date)
    print(f"Extracted {len(items)} items")
    
    if gt:
        return validate_against_gt(items, gt, label)
    else:
        for i, item in enumerate(items):
            print(f"  {i+1:3d}: DocId={item['docid'][:16]}... Num={item['num']} PDatum={item['pdatum']} EDatum={item['edatum']} src={item['pdatum_source']}")
        return len(items), 0, 0


def main():
    gwt_files = []
    gt_path = None
    sd = date.today()  # Default: today
    
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '--gt' and i + 1 < len(sys.argv):
            gt_path = sys.argv[i+1]; i += 2
        elif sys.argv[i] == '--scrape-date' and i + 1 < len(sys.argv):
            sd = date.fromisoformat(sys.argv[i+1]); i += 2
        else:
            gwt_files.append(sys.argv[i]); i += 1
    
    if not gt_path:
        for c in ['readme/PDatumListe.updated.txt']:
            if Path(c).exists(): gt_path = c; break
    
    gt = load_ground_truth(gt_path) if gt_path else {}
    print(f"Ground truth: {len(gt)//2} items, scrape date: {sd}")
    
    total_correct_pd = 0
    total_correct_ed = 0
    total_items = 0
    
    for gwt_file in gwt_files:
        raw = Path(gwt_file).read_text(encoding='utf-8')
        positions = [m.start() for m in re.finditer(r'//OK\[', raw)]
        
        if len(positions) > 1:
            for bi, pos in enumerate(positions):
                end = positions[bi + 1] if bi + 1 < len(positions) else len(raw)
                block = raw[pos:end].rstrip().rstrip(';').rstrip()
                result = analyze_page(block, gt, f"{gwt_file} [page {bi}]", sd)
                if result:
                    cpd, ced, n = result
                    total_correct_pd += cpd
                    total_correct_ed += ced
                    total_items += n
        else:
            result = analyze_page(raw, gt, gwt_file, sd)
            if result:
                cpd, ced, n = result
                total_correct_pd += cpd
                total_correct_ed += ced
                total_items += n
    
    if total_items > 0:
        print(f"\n{'='*100}")
        print(f"OVERALL: PDatum {total_correct_pd}/{total_items} = {100*total_correct_pd/total_items:.1f}%, EDatum {total_correct_ed}/{total_items} = {100*total_correct_ed/total_items:.1f}%")
        print(f"{'='*100}")


if __name__ == '__main__':
    main()
