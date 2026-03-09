"""Deep analysis: atom-level frame structure + string table ordering.

Goal: understand EXACTLY how PDatum and EDatum are encoded,
both in the string table (unique strings) and in the atom array
(numeric indices referencing the string table).
"""
import re
import sys
from pathlib import Path
from collections import defaultdict

RE_DOCID = re.compile(r'^[0-9a-f]{32}$')
RE_NUM   = re.compile(r'^\d{1,4}\s+\d{4}\s+\d+$')
RE_DATE  = re.compile(r'^(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
RE_ZERO  = re.compile(r'^0{4}-0{2}-0{2}$')
RE_GCODE = re.compile(r'^\d{3}$')

# ── GWT tokenizer ──

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

def get_atoms_and_st(parsed: list):
    """Return (atoms_before_st, string_table)."""
    st = find_string_table(parsed)
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
    return atoms, st

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


def resolve(atom, st):
    """Resolve an atom: if int in range → string table entry, else raw."""
    if isinstance(atom, int) and 0 <= atom < len(st):
        return st[atom]
    return str(atom)


def classify(s):
    if RE_DOCID.match(s):  return 'DOCID'
    if RE_NUM.match(s):    return 'NUM'
    if RE_ZERO.match(s):   return 'ZDATE'
    if RE_DATE.match(s):   return 'DATE'
    if RE_GCODE.match(s):  return 'GCODE'
    if s == '-':           return 'SEP'
    return None


def analyze_atom_frames(raw: str, gt: dict, label: str):
    parsed = parse_gwt(raw)
    atoms, st = get_atoms_and_st(parsed)
    
    print(f"\n{'='*100}")
    print(f"ATOM-LEVEL ANALYSIS: {label}")
    print(f"Atoms: {len(atoms)}, String table: {len(st)} entries")
    print(f"{'='*100}")
    
    # Step 1: Find all atom positions that reference DocIds
    docid_atoms = []  # (atom_idx, st_idx, docid_str)
    for i, a in enumerate(atoms):
        if isinstance(a, int) and 0 <= a < len(st):
            s = st[a]
            if RE_DOCID.match(s):
                docid_atoms.append((i, a, s))
    
    print(f"\nDocId atom positions (first occurrence per unique DocId):")
    seen_docids = set()
    first_docid_atoms = []
    for ai, si, docid in docid_atoms:
        if docid not in seen_docids:
            seen_docids.add(docid)
            first_docid_atoms.append((ai, si, docid))
            gt_info = gt.get(docid, {})
            print(f"  atom[{ai:4d}] → ST[{si:3d}] = {docid[:20]}... GT: PD={gt_info.get('pdatum','?')} ED={gt_info.get('edatum','?')}")
    
    # Step 2: Compute stride between consecutive DocId first-occurrences
    print(f"\nStride between consecutive first DocId atoms:")
    for i in range(1, len(first_docid_atoms)):
        prev_ai = first_docid_atoms[i-1][0]
        curr_ai = first_docid_atoms[i][0]
        stride = curr_ai - prev_ai
        print(f"  DocId#{i} → DocId#{i+1}: atom[{prev_ai}] → atom[{curr_ai}] = stride {stride}")
    
    # Step 3: For each DocId, show the next ~20 atoms resolved
    print(f"\n{'='*100}")
    print(f"FRAME DUMP: atoms around each DocId (first occurrence)")
    print(f"{'='*100}")
    
    for item_idx, (ai, si, docid) in enumerate(first_docid_atoms):
        gt_info = gt.get(docid, {})
        gt_pd = gt_info.get('pdatum', '?')
        gt_ed = gt_info.get('edatum', '?')
        gt_num = gt_info.get('num', '?')
        
        # Show atoms from DocId-5 to DocId+25
        start = max(0, ai - 5)
        end = min(len(atoms), ai + 30)
        
        print(f"\n--- Item {item_idx+1}: DocId={docid[:16]}... (GT: PD={gt_pd} ED={gt_ed} Num={gt_num}) ---")
        print(f"  atom[{ai}] = ST[{si}] = DOCID")
        
        for j in range(start, end):
            a = atoms[j]
            prefix = ">>>" if j == ai else "   "
            
            if isinstance(a, int) and 0 <= a < len(st):
                s = st[a]
                cls = classify(s)
                if cls:
                    # Check if this is a date that matches GT
                    ann = ""
                    if cls == 'DATE' and s == gt_ed:
                        ann = " ← THIS ITEM'S EDATUM"
                    elif cls == 'DATE' and s == gt_pd:
                        ann = " ← THIS ITEM'S PDATUM"
                    elif cls == 'DATE':
                        # Check if it's any item's pdatum or edatum
                        roles = []
                        for k, v in gt.items():
                            if RE_NUM.match(k):
                                if v['pdatum'] == s: roles.append('P')
                                if v['edatum'] == s: roles.append('E')
                        if roles:
                            ann = f" ({'+'.join(sorted(set(roles)))} for other items)"
                        else:
                            ann = " (UNKNOWN date)"
                    
                    print(f"  {prefix} [{j:4d}] = ST[{a:3d}] → {s:30s} [{cls}]{ann}")
                else:
                    # Not interesting, but show if close to DocId  
                    if abs(j - ai) <= 5:
                        trunc = s[:30] if len(s) > 30 else s
                        print(f"  {prefix} [{j:4d}] = ST[{a:3d}] → {trunc}")
            elif isinstance(a, float):
                if a > 100000:
                    print(f"  {prefix} [{j:4d}] = {a:>12.1f}   [RECID]")
                elif abs(j - ai) <= 5:
                    print(f"  {prefix} [{j:4d}] = {a:>12.1f}")
            elif isinstance(a, int):
                if abs(j - ai) <= 5 or abs(a) > 10000:
                    print(f"  {prefix} [{j:4d}] = {a:>12d}")
    
    # Step 4: Analyze all atom positions for each DATE string
    print(f"\n{'='*100}")
    print(f"DATE REFERENCE ANALYSIS: which atoms reference each date?")
    print(f"{'='*100}")
    
    date_st_indices = {}
    for si, s in enumerate(st):
        if RE_DATE.match(s) or RE_ZERO.match(s):
            date_st_indices[si] = s
    
    for si in sorted(date_st_indices.keys()):
        date_val = date_st_indices[si]
        # Find ALL atoms that reference this ST index
        refs = [i for i, a in enumerate(atoms) if a == si]
        
        # Determine role
        roles = []
        for k, v in gt.items():
            if RE_NUM.match(k):
                if v['pdatum'] == date_val: roles.append(f"P({k})")
                if v['edatum'] == date_val: roles.append(f"E({k})")
        
        role_summary = ', '.join(roles[:5]) + ('...' if len(roles) > 5 else '') if roles else 'UNKNOWN'
        print(f"\n  ST[{si:3d}] = {date_val}  refs={len(refs)} atoms  role: {role_summary}")
        for ref in refs:
            # Find which DocId this ref is near
            nearest_docid = None
            for ai, _, docid in first_docid_atoms:
                if ai <= ref:
                    nearest_docid = (ai, docid)
                else:
                    break
            ctx = f"near DocId at atom[{nearest_docid[0]}]={nearest_docid[1][:16]}..." if nearest_docid else "before any DocId"
            offset = ref - nearest_docid[0] if nearest_docid else ref
            print(f"    atom[{ref:4d}] (offset +{offset:2d} from DocId) — {ctx}")


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
                analyze_atom_frames(block, gt, f"{gwt_file} [block {bi}]")
        else:
            analyze_atom_frames(raw, gt, gwt_file)


if __name__ == '__main__':
    main()
