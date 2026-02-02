"""Script pour comparer les PDatum entre fribourg_spider et tribuna_spider."""
import subprocess
import re
import sys

def run_spider(spider_name, days=7, max_pages=1):
    """Exécute un spider et extrait les paires (DocId, PDatum)."""
    cmd = [
        sys.executable, "-m", "scrapy", "crawl", spider_name,
        "-a", f"days={days}",
        "-s", f"MAX_PAGES={max_pages}",
        "-s", "LOG_LEVEL=DEBUG",
        "-s", "FILES_STORE=output/pdfs/test",
    ]
    
    result = subprocess.run(
        cmd, 
        capture_output=True, 
        text=True, 
        encoding='utf-8', 
        errors='replace',
        cwd=r"c:\Users\thoma\OneDrive\Bureau\estiam\tribuna\tribuna_scraper"
    )
    
    # Parser les items émis
    items = []
    output = result.stdout + result.stderr
    
    # Pattern pour trouver les items dans les logs
    # On cherche les lignes avec DocId et PDatum
    for line in output.split('\n'):
        if "'DocId':" in line and "'PDatum':" in line:
            doc_match = re.search(r"'DocId':\s*'([^']+)'", line)
            pd_match = re.search(r"'PDatum':\s*'([^']*)'", line)
            if doc_match:
                doc_id = doc_match.group(1)[:8]
                pdatum = pd_match.group(1) if pd_match else ''
                items.append((doc_id, pdatum))
    
    return items

if __name__ == "__main__":
    print("Comparaison PDatum: fribourg_spider vs tribuna_spider")
    print("=" * 60)
    
    # Fribourg spider
    print("\n--- fribourg_spider (days=7, page 0) ---")
    fr_items = run_spider("fribourg", days=7, max_pages=1)
    print(f"Items trouvés: {len(fr_items)}")
    for doc_id, pdatum in fr_items[:15]:
        print(f"  {doc_id}... PDatum={pdatum}")
    
    # Tribuna spider avec canton=fribourg
    print("\n--- tribuna_spider (canton=fribourg, days=7, page 0) ---")
    tr_items = run_spider("tribuna", days=7, max_pages=1)
    print(f"Items trouvés: {len(tr_items)}")
    for doc_id, pdatum in tr_items[:15]:
        print(f"  {doc_id}... PDatum={pdatum}")
