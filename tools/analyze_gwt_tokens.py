"""Analyze GWT token structure to understand PDatum/EDatum encoding.

Key observations from user:
- All DocId, Num, PDatum, EDatum appear in tokens, in UI order
- Each date appears ONLY ONCE even if shared by multiple items
- ~3 unknown dates per GWT response (mostly future, not always)
- First PDatum: between "-" and the 2nd DocId
- EDatum: right after the Num. If absent → EDatum already appeared earlier (propagate)
- Subsequent PDatum: between their DocId and next DocId, but closer to next DocId (by index)
"""

import re
import sys
import json
from pathlib import Path
from typing import Optional

# ── Regex ──
RE_DOCID = re.compile(r'^[0-9a-f]{32}$')
RE_NUM   = re.compile(r'^\d{1,4}\s+\d{4}\s+\d+$')
RE_DATE  = re.compile(r'^(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
RE_ZERO  = re.compile(r'^0{4}-0{2}-0{2}$')
RE_HASH  = re.compile(r'^[0-9a-f]{40,}$')
RE_JTYPE = re.compile(r'^tribunavtplus\.|^java\.util\.|^java\.lang\.')

# ── GWT parser (same as gwt_multipage_extract.py) ──

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
            raise ValueError(f"Unexpected char {t[i]!r} at pos {i}")
    if c == '"': return _pstr(t, i)
    if c.isdigit() or c == '-': return _pnum(t, i)
    raise ValueError(f"Unknown token at {i}: {t[i:i+30]!r}")

def parse_gwt(raw: str) -> list:
    t = raw.strip()
    ok_pos = t.find('//OK')
    if ok_pos < 0: raise ValueError("No //OK found")
    bracket = t.find('[', ok_pos)
    val, _ = _pval(t, bracket)
    return val

def find_string_table(parsed: list) -> Optional[list]:
    best = None; bl = 5
    def walk(o):
        nonlocal best, bl
        if isinstance(o, list):
            if len(o) >= bl and all(isinstance(x, str) for x in o):
                best = o; bl = len(o); return
            for item in o: walk(item)
    walk(parsed)
    return best

def get_atoms_before_st(parsed: list, st: list) -> list:
    atoms = []
    found = False
    def walk(o):
        nonlocal found
        if found: return
        if isinstance(o, list):
            if o is st: found = True; return
            for item in o: walk(item)
        else:
            atoms.append(o)
    walk(parsed)
    return atoms


def classify(s: str) -> str:
    if RE_DOCID.match(s):  return 'DOCID'
    if RE_NUM.match(s):    return 'NUM'
    if RE_ZERO.match(s):   return 'ZDATE'
    if RE_DATE.match(s):   return 'DATE'
    if RE_HASH.match(s):   return 'HASH'
    if RE_JTYPE.match(s):  return 'JTYPE'
    if s == '':            return 'EMPTY'
    if s == '-':           return 'SEP'
    if s == 'TC':          return 'TC'
    if re.match(r'^\d{3}$', s): return 'GCODE'
    return 'TEXT'


def resolve_tokens(atoms: list, st: list) -> list:
    """Resolve numeric atoms to string table entries, return list of (atom_idx, raw_val, resolved_str, classification)."""
    result = []
    for i, a in enumerate(atoms):
        if isinstance(a, int) and 0 <= a < len(st):
            s = st[a]
            result.append((i, a, s, classify(s)))
        elif isinstance(a, float):
            result.append((i, a, str(a), 'FLOAT'))
        elif isinstance(a, int):
            result.append((i, a, str(a), 'INT'))
        elif isinstance(a, str):
            result.append((i, a, a, classify(a)))
        else:
            result.append((i, a, str(a), '???'))
    return result


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


def analyze_page(raw: str, gt: dict, page_label: str):
    """Analyze a single GWT page, printing the resolved string table in order with annotations."""
    parsed = parse_gwt(raw)
    st = find_string_table(parsed)
    if not st:
        print(f"  ERROR: No string table found in {page_label}")
        return

    atoms = get_atoms_before_st(parsed, st)
    print(f"\n{'='*90}")
    print(f"PAGE: {page_label}")
    print(f"  String table: {len(st)} entries")
    print(f"  Atoms (before ST): {len(atoms)}")
    print(f"{'='*90}")

    # Print the string table with index and classification
    print(f"\n--- STRING TABLE (in order, index = position in ST) ---")
    dates_in_st = []
    docids_in_st = []
    nums_in_st = []
    
    for idx, entry in enumerate(st):
        cls = classify(entry)
        
        # Annotate with ground truth
        annotation = ""
        if cls == 'DOCID' and entry in gt:
            ref = gt[entry]
            annotation = f"  <-- GT: PDatum={ref['pdatum']} EDatum={ref['edatum']} Num={ref['num']}"
            docids_in_st.append((idx, entry))
        elif cls == 'NUM' and entry in gt:
            ref = gt[entry]
            annotation = f"  <-- GT: PDatum={ref['pdatum']} EDatum={ref['edatum']} DocId={ref['docid'][:16]}..."
            nums_in_st.append((idx, entry))
        elif cls == 'DATE':
            # Check if this date is a PDatum or EDatum for any item in GT
            roles = []
            for k, v in gt.items():
                if RE_NUM.match(k):
                    if v['pdatum'] == entry:
                        roles.append(f"PDatum({k})")
                    if v['edatum'] == entry:
                        roles.append(f"EDatum({k})")
            if roles:
                annotation = f"  <-- {', '.join(roles)}"
            else:
                annotation = f"  <-- UNKNOWN DATE (not PDatum/EDatum of any item on this page?)"
            dates_in_st.append((idx, entry, roles))
        elif cls == 'ZDATE':
            annotation = "  <-- ZERO DATE"
        
        # Only print interesting tokens
        if cls in ('DOCID', 'NUM', 'DATE', 'ZDATE', 'SEP', 'GCODE'):
            print(f"  ST[{idx:3d}] = {entry:40s} [{cls:6s}]{annotation}")
    
    # Now print the RESOLVED token sequence (atoms resolved via ST)
    print(f"\n--- RESOLVED TOKEN SEQUENCE (interesting tokens only) ---")
    print(f"  Legend: [idx] raw_atom -> resolved_value (CLASS)")
    print()
    
    resolved = resolve_tokens(atoms, st)
    
    # Extract only interesting tokens for display
    interesting = ['DOCID', 'NUM', 'DATE', 'ZDATE', 'SEP', 'FLOAT', 'GCODE']
    
    docid_positions = []  # (atom_idx, docid)
    num_positions = []    # (atom_idx, num)
    date_positions = []   # (atom_idx, date)
    sep_positions = []    # (atom_idx)
    float_positions = []  # (atom_idx, float_val)
    
    for atom_idx, raw_val, resolved_str, cls in resolved:
        if cls == 'DOCID':
            docid_positions.append((atom_idx, resolved_str))
        elif cls == 'NUM':
            num_positions.append((atom_idx, resolved_str))
        elif cls == 'DATE':
            date_positions.append((atom_idx, resolved_str))
        elif cls == 'ZDATE':
            date_positions.append((atom_idx, resolved_str))
        elif cls == 'SEP':
            sep_positions.append(atom_idx)
        elif cls == 'FLOAT' and float(resolved_str) > 100000:
            float_positions.append((atom_idx, resolved_str))
    
    # Print a consolidated timeline
    print(f"\n--- CONSOLIDATED TIMELINE (ordered by atom index) ---")
    print(f"  Shows DocIds, Nums, Dates, SEP('-'), and record-ID floats in token order\n")
    
    all_events = []
    for idx, docid in docid_positions:
        ann = ""
        if docid in gt:
            ref = gt[docid]
            ann = f" (GT: PDatum={ref['pdatum']} EDatum={ref['edatum']})"
        all_events.append((idx, f"DOCID  {docid[:20]}...{ann}"))
    
    for idx, num in num_positions:
        ann = ""
        if num in gt:
            ref = gt[num]
            ann = f" (GT: PDatum={ref['pdatum']} EDatum={ref['edatum']})"
        all_events.append((idx, f"NUM    {num}{ann}"))
    
    for idx, d in date_positions:
        roles = []
        for k, v in gt.items():
            if RE_NUM.match(k):
                if v['pdatum'] == d:
                    roles.append(f"PDatum({k})")
                if v['edatum'] == d:
                    roles.append(f"EDatum({k})")
        role_str = f" -> {', '.join(roles)}" if roles else " -> UNKNOWN/FUTURE?"
        all_events.append((idx, f"DATE   {d}{role_str}"))
    
    for idx in sep_positions:
        all_events.append((idx, f"SEP    -"))
    
    for idx, fval in float_positions:
        all_events.append((idx, f"RECID  {fval}"))
    
    all_events.sort(key=lambda x: x[0])
    
    prev_idx = -1
    for idx, desc in all_events:
        gap = idx - prev_idx - 1 if prev_idx >= 0 else 0
        gap_str = f"  (+{gap} atoms)" if gap > 2 else ""
        print(f"  [{idx:4d}]{gap_str} {desc}")
        prev_idx = idx
    
    # Summary
    print(f"\n--- SUMMARY ---")
    print(f"  DocIds: {len(docid_positions)}")
    print(f"  Nums:   {len(num_positions)}")
    print(f"  Dates:  {len(date_positions)}")
    print(f"  SEP(-): {len(sep_positions)}")
    print(f"  RecIDs: {len(float_positions)}")
    
    # Identify date roles
    known_pd = set()
    known_ed = set()
    unknown_dates = []
    for idx, d in date_positions:
        is_pd = is_ed = False
        for k, v in gt.items():
            if RE_NUM.match(k):
                if v['pdatum'] == d: is_pd = True
                if v['edatum'] == d: is_ed = True
        if is_pd: known_pd.add(d)
        if is_ed: known_ed.add(d)
        if not is_pd and not is_ed:
            unknown_dates.append((idx, d))
    
    print(f"\n  Date roles:")
    print(f"    PDatum dates: {len(known_pd)} unique values")
    print(f"    EDatum dates: {len(known_ed)} unique values")
    print(f"    Unknown dates: {len(unknown_dates)}")
    for idx, d in unknown_dates:
        print(f"      [{idx:4d}] {d}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_gwt_tokens.py <gwt_file1> [gwt_file2 ...] [--ground-truth GT_FILE]")
        sys.exit(1)
    
    gwt_files = []
    gt_path = None
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '--ground-truth' and i + 1 < len(sys.argv):
            gt_path = sys.argv[i+1]; i += 2
        else:
            gwt_files.append(sys.argv[i]); i += 1
    
    # Auto-detect ground truth
    if not gt_path:
        for candidate in ['readme/PDatumListe.updated.txt']:
            if Path(candidate).exists():
                gt_path = candidate
                break
    
    gt = load_ground_truth(gt_path) if gt_path else {}
    print(f"Ground truth: {len(gt)//2} items from {gt_path}")
    
    for gwt_file in gwt_files:
        raw = Path(gwt_file).read_text(encoding='utf-8')
        
        # Split multi-block files
        import re as _re
        positions = [m.start() for m in _re.finditer(r'//OK\[', raw)]
        if len(positions) > 1:
            for bi, pos in enumerate(positions):
                end = positions[bi + 1] if bi + 1 < len(positions) else len(raw)
                block = raw[pos:end].rstrip().rstrip(';').rstrip()
                analyze_page(block, gt, f"{gwt_file} [block {bi}]")
        else:
            analyze_page(raw, gt, gwt_file)


if __name__ == '__main__':
    main()
