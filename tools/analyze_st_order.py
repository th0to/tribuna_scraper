"""Focus on STRING TABLE order for GWT - this is the key structure.

The user's insight: dates appear only ONCE in the string table.
The string table ORDER is what encodes the relationship between items and their dates.
"""
import re
import sys
from pathlib import Path

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


def analyze_string_table_order(raw: str, gt: dict, label: str):
    parsed = parse_gwt(raw)
    st = find_string_table(parsed)
    if not st:
        print(f"ERROR: no string table for {label}")
        return

    print(f"\n{'='*100}")
    print(f"PAGE: {label}")
    print(f"String table has {len(st)} entries")
    print(f"{'='*100}")

    # Collect all interesting entries with their ST index
    entries = []
    for idx, val in enumerate(st):
        if RE_DOCID.match(val):
            entries.append((idx, 'DOCID', val))
        elif RE_NUM.match(val):
            entries.append((idx, 'NUM', val))
        elif RE_ZERO.match(val):
            entries.append((idx, 'ZDATE', val))
        elif RE_DATE.match(val):
            entries.append((idx, 'DATE', val))
        elif val == '-':
            entries.append((idx, 'SEP', val))
        elif RE_GCODE.match(val):
            entries.append((idx, 'GCODE', val))

    # Print in ST order with GT annotations
    print(f"\nString table entries (interesting only, in ST order):")
    print(f"{'ST#':>5} {'Type':6} {'Value':40} {'Role / Ground Truth':60}")
    print(f"{'-'*5} {'-'*6} {'-'*40} {'-'*60}")

    item_counter = 0
    for idx, typ, val in entries:
        annotation = ""
        if typ == 'DOCID':
            item_counter += 1
            if val in gt:
                ref = gt[val]
                annotation = f"Item#{item_counter} GT: PDatum={ref['pdatum']} EDatum={ref['edatum']} Num={ref['num']}"
            else:
                annotation = f"Item#{item_counter} (not in GT)"
        elif typ == 'NUM':
            if val in gt:
                ref = gt[val]
                annotation = f"GT: PDatum={ref['pdatum']} EDatum={ref['edatum']}"
        elif typ == 'DATE':
            roles = []
            for k, v in gt.items():
                if RE_NUM.match(k):
                    if v['pdatum'] == val: roles.append(f"P({k})")
                    if v['edatum'] == val: roles.append(f"E({k})")
            if roles:
                # Separate PDatum and EDatum roles
                p_roles = [r for r in roles if r.startswith('P(')]
                e_roles = [r for r in roles if r.startswith('E(')]
                parts = []
                if p_roles: parts.append(f"PDatum×{len(p_roles)}")
                if e_roles: parts.append(f"EDatum×{len(e_roles)}")
                annotation = f"{' + '.join(parts)}"
            else:
                annotation = "NOT a PDatum or EDatum of any GT item → UNKNOWN"
        elif typ == 'SEP':
            annotation = "separator"
        elif typ == 'GCODE':
            annotation = f"GCode"

        # Truncate display
        val_disp = val[:40] if len(val) > 40 else val
        print(f"{idx:5d} {typ:6s} {val_disp:40s} {annotation}")

    # Now let's look at the sequence pattern:
    # DocId → Num → Date ordering
    print(f"\n{'='*100}")
    print(f"SEQUENCE ANALYSIS — Items in string table order")
    print(f"{'='*100}")

    # Extract just DocIds, Nums, Dates, SEP in order
    seq = [(idx, typ, val) for idx, typ, val in entries if typ in ('DOCID', 'NUM', 'DATE', 'ZDATE', 'SEP')]

    # Group by items
    current_item = None
    items_found = []
    all_dates_in_st = []

    for idx, typ, val in seq:
        if typ == 'DATE' or typ == 'ZDATE':
            all_dates_in_st.append((idx, val))

    # Print item-by-item with surrounding dates
    docids = [(idx, val) for idx, typ, val in entries if typ == 'DOCID']
    nums = [(idx, val) for idx, typ, val in entries if typ == 'NUM']
    dates = [(idx, val) for idx, typ, val in entries if typ in ('DATE', 'ZDATE')]
    seps = [idx for idx, typ, val in entries if typ == 'SEP']

    print(f"\nDocIds: {len(docids)}, Nums: {len(nums)}, Dates (incl zero): {len(dates)}, SEPs: {len(seps)}")
    
    # For each DocId, find the nearest Num (before or after) and dates around it
    print(f"\n--- Item mapping (DocId → nearest Num → surrounding dates) ---")
    for i, (didx, docid) in enumerate(docids):
        gt_info = gt.get(docid, {})
        gt_pd = gt_info.get('pdatum', '?')
        gt_ed = gt_info.get('edatum', '?')
        gt_num = gt_info.get('num', '?')

        # Find nearest Num after DocId
        nearest_num = None
        for nidx, nval in nums:
            if nidx > didx:
                nearest_num = (nidx, nval)
                break

        # Find dates between prev DocId and next DocId
        prev_didx = docids[i-1][0] if i > 0 else -1
        next_didx = docids[i+1][0] if i + 1 < len(docids) else 99999

        dates_before = [(d_idx, d_val) for d_idx, d_val in dates if prev_didx < d_idx < didx]
        dates_after = [(d_idx, d_val) for d_idx, d_val in dates if didx < d_idx < next_didx]

        # Compact display
        before_str = ", ".join(f"[{d_idx}]{d_val}" for d_idx, d_val in dates_before)
        after_str = ", ".join(f"[{d_idx}]{d_val}" for d_idx, d_val in dates_after)
        num_str = f"[{nearest_num[0]}]{nearest_num[1]}" if nearest_num else "NONE"

        print(f"\n  Item {i+1}: DOCID[{didx}] {docid[:20]}...")
        print(f"    GT: PDatum={gt_pd} EDatum={gt_ed} Num={gt_num}")
        print(f"    Nearest Num after: {num_str}")
        print(f"    Dates BEFORE DocId (between prev DocId): {before_str or 'NONE'}")
        print(f"    Dates AFTER DocId (before next DocId):   {after_str or 'NONE'}")

    # Summary of all dates
    print(f"\n--- ALL dates in string table (in order) ---")
    for d_idx, d_val in dates:
        roles = []
        for k, v in gt.items():
            if RE_NUM.match(k):
                if v['pdatum'] == d_val: roles.append('P')
                if v['edatum'] == d_val: roles.append('E')
        role_set = set(roles)
        role_str = '+'.join(sorted(role_set)) if role_set else 'UNKNOWN'
        print(f"  ST[{d_idx:4d}] {d_val}  role={role_str} (×{len(roles)} items)")


def main():
    gwt_files = []
    gt_path = None
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '--gt' and i + 1 < len(sys.argv):
            gt_path = sys.argv[i+1]; i += 2
        else:
            gwt_files.append(sys.argv[i]); i += 1

    if not gt_path:
        for c in ['readme/PDatumListe.updated.txt']:
            if Path(c).exists(): gt_path = c; break

    gt = load_ground_truth(gt_path) if gt_path else {}
    print(f"Ground truth: {len(gt)//2} items loaded")

    for gwt_file in gwt_files:
        raw = Path(gwt_file).read_text(encoding='utf-8')
        positions = [m.start() for m in re.finditer(r'//OK\[', raw)]
        if len(positions) > 1:
            for bi, pos in enumerate(positions):
                end = positions[bi + 1] if bi + 1 < len(positions) else len(raw)
                block = raw[pos:end].rstrip().rstrip(';').rstrip()
                analyze_string_table_order(block, gt, f"{gwt_file} [block {bi}]")
        else:
            analyze_string_table_order(raw, gt, gwt_file)


if __name__ == '__main__':
    main()
