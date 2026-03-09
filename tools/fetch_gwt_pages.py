#!/usr/bin/env python3
"""Mini GWT page fetcher — fetches raw GWT-RPC responses for Fribourg.

Usage:
    python tools/fetch_gwt_pages.py --pages 0-7
    python tools/fetch_gwt_pages.py --pages 0-7 --outdir saved_pages/fresh
    python tools/fetch_gwt_pages.py --pages 0-7 --combined gwt_fresh_pages.txt

Fetches raw GWT responses sorted by publication date (PDatum),
20 items per page, matching the tribunal UI order.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── Config ──────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).resolve().parent.parent / "publication_scraper" / "fribourg_config.json"

# Regex from gwt_utils
RE_TREFFER = re.compile(r'^//OK\[(\d+)')


def load_config() -> dict:
    """Load fribourg config."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)["fribourg"]


def extract_trefferzahl(payload: str) -> Optional[int]:
    """Extract total result count from //OK[NNN,...."""
    m = RE_TREFFER.search(payload.lstrip())
    return int(m.group(1)) if m else None


def extract_permutation_from_html(html: str) -> Optional[str]:
    """Extract X-GWT-Permutation from bootstrap HTML."""
    # Look for strongName in script tags
    m = re.search(r'strongName\s*[:=]\s*["\']([A-Za-z0-9._-]{8,})["\']', html)
    if m:
        return m.group(1)
    # Fallback: *.cache.js reference
    scripts = re.findall(r'src=["\']([^"\']+\.cache\.js)["\']', html)
    for s in scripts:
        name = s.rsplit("/", 1)[-1].split(".")[0]
        if re.match(r'^[A-Fa-f0-9]{32}$', name):
            return name
    return None


def extract_nocache_url(html: str, base_url: str) -> Optional[str]:
    """Extract nocache.js URL for permutation follow-up."""
    scripts = re.findall(r'src=["\']([^"\']+\.nocache\.js)["\']', html)
    if scripts:
        from urllib.parse import urljoin
        return urljoin(base_url, scripts[0])
    return None


def bootstrap_session(session: requests.Session, config: dict) -> str:
    """Bootstrap GWT session — get fresh X-GWT-Permutation token.
    
    Returns the permutation token.
    """
    base_url = config["base_url"]
    headers = dict(config["headers"])
    
    print(f"[bootstrap] GET {base_url}")
    resp = session.get(base_url, headers={
        "User-Agent": headers["User-Agent"],
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": headers.get("Accept-Language", "fr"),
    }, timeout=30, verify=False)
    resp.raise_for_status()
    
    perm = extract_permutation_from_html(resp.text)
    if perm:
        print(f"[bootstrap] ✅ Permutation from HTML: {perm}")
        return perm
    
    # Follow nocache.js
    nocache_url = extract_nocache_url(resp.text, base_url)
    if nocache_url:
        print(f"[bootstrap] Following nocache.js: {nocache_url}")
        resp2 = session.get(nocache_url, headers={
            "User-Agent": headers["User-Agent"],
            "Referer": base_url,
        }, timeout=30, verify=False)
        
        perm = extract_permutation_from_html(resp2.text)
        if perm:
            print(f"[bootstrap] ✅ Permutation from nocache: {perm}")
            return perm
        
        # Try hex32 in nocache content
        hex32 = re.findall(r'"([A-Fa-f0-9]{32})"', resp2.text)
        if hex32:
            perm = hex32[0]
            print(f"[bootstrap] ✅ Permutation from hex32: {perm}")
            return perm
    
    # Fallback to config
    perm = headers.get("X-GWT-Permutation", "")
    print(f"[bootstrap] ⚠️ Using config permutation: {perm}")
    return perm


def fetch_page(session: requests.Session, config: dict, page_nr: int,
               permutation: str, trefferzahl: Optional[int] = None) -> str:
    """Fetch a single GWT page.
    
    - page_nr=0 with trefferzahl=None: uses result_query_tpl (unsorted, to get trefferzahl)
    - page_nr>=0 with trefferzahl: uses ui_publication_sort_body (sorted by PDatum)
    """
    url = config["result_page_url"]
    headers = dict(config["headers"])
    headers["X-GWT-Permutation"] = permutation
    
    if trefferzahl is not None and "ui_publication_sort_body" in config:
        tpl = config["ui_publication_sort_body"]
        body = tpl.replace("{page_nr}", str(page_nr)).replace("{trefferzahl}", str(trefferzahl))
    else:
        tpl = config["result_query_tpl"]
        body = tpl.replace("{page_nr}", str(page_nr))
    
    print(f"[fetch] POST page {page_nr} (trefferzahl={'N/A' if trefferzahl is None else trefferzahl})")
    
    resp = session.post(url, data=body, headers=headers, timeout=30, verify=False)
    resp.raise_for_status()
    
    text = resp.text
    if not text.startswith("//OK"):
        print(f"  ⚠️ Response doesn't start with //OK (len={len(text)})")
        print(f"  First 200 chars: {text[:200]}")
    else:
        t = extract_trefferzahl(text)
        n_tokens = text.count('","')
        print(f"  ✅ //OK response (len={len(text)}, trefferzahl={t}, ~tokens={n_tokens})")
    
    return text


def parse_page_range(spec: str) -> list[int]:
    """Parse page range like '0-7' or '0,2,5' or '3'."""
    pages = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a), int(b) + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))


def main():
    parser = argparse.ArgumentParser(description="Fetch fresh GWT pages for Fribourg")
    parser.add_argument("--pages", default="0-7", help="Page range (e.g. '0-7', '0,2,5')")
    parser.add_argument("--outdir", default=None, help="Dir to save individual page files")
    parser.add_argument("--combined", default=None, help="Combined output file (all pages)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests (seconds)")
    args = parser.parse_args()
    
    pages = parse_page_range(args.pages)
    print(f"Will fetch pages: {pages}")
    
    config = load_config()
    
    # Setup output
    outdir = Path(args.outdir) if args.outdir else Path("saved_pages/fresh")
    outdir.mkdir(parents=True, exist_ok=True)
    
    session = requests.Session()
    
    # 1. Bootstrap
    permutation = bootstrap_session(session, config)
    time.sleep(args.delay)
    
    # 2. Warmup: fetch page 0 unsorted to get trefferzahl
    print("\n--- Warmup (page 0, unsorted) ---")
    warmup_resp = fetch_page(session, config, 0, permutation, trefferzahl=None)
    trefferzahl = extract_trefferzahl(warmup_resp)
    if trefferzahl is None:
        print("❌ Could not extract trefferzahl. Aborting.")
        sys.exit(1)
    print(f"✅ Trefferzahl = {trefferzahl}")
    time.sleep(args.delay)
    
    # 3. Fetch each page sorted by PDatum
    combined_parts = []
    scrape_date = datetime.now().strftime("%Y-%m-%d")
    
    for page_nr in pages:
        print(f"\n--- Page {page_nr} ---")
        text = fetch_page(session, config, page_nr, permutation, trefferzahl=trefferzahl)
        
        # Save individual page
        page_file = outdir / f"gwt_page{page_nr}.txt"
        page_file.write_text(text, encoding="utf-8")
        print(f"  Saved: {page_file}")
        
        combined_parts.append(f"### PAGE {page_nr} ###\n{text}")
        
        if page_nr < pages[-1]:
            time.sleep(args.delay)
    
    # 4. Save combined file
    combined_path = Path(args.combined) if args.combined else outdir / f"gwt_pages_combined_{datetime.now().strftime('%Y%m%d')}.txt"
    combined_content = f"# Scrape date: {scrape_date}\n# Pages: {pages}\n# Trefferzahl: {trefferzahl}\n\n" + "\n\n".join(combined_parts)
    combined_path.write_text(combined_content, encoding="utf-8")
    print(f"\n✅ Combined file: {combined_path}")
    print(f"   Scrape date: {scrape_date}, Trefferzahl: {trefferzahl}, Pages: {len(pages)}")


if __name__ == "__main__":
    main()
