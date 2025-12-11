
from bs4 import BeautifulSoup

html_file_path = '/home/ubuntu/tribuna_scraper/tribuna_full_page_after_js.html'

with open(html_file_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

soup = BeautifulSoup(html_content, 'html.parser')

print("\n--- Analyse des sélecteurs ---\n")

# Champ de recherche
search_input = soup.find('input', class_='gwt-TextBox search-box-publ')
if search_input:
    print(f"Champ de recherche trouvé: {search_input.name} avec classes {search_input.get('class')} et placeholder '{search_input.get('placeholder')}'")
    print(f"Sélecteur XPath potentiel pour le champ de recherche: //input[@class=\'{' '.join(search_input.get('class'))}\']")
else:
    print("Champ de recherche non trouvé avec le sélecteur actuel.")

# Bouton de recherche
search_button = soup.find('div', class_='search-icon-publ')
if search_button:
    print(f"Bouton de recherche trouvé: {search_button.name} avec classes {search_button.get('class')}")
    print(f"Sélecteur XPath potentiel pour le bouton de recherche: //div[contains(@class, \'search-icon-publ\')] ")
else:
    print("Bouton de recherche non trouvé avec le sélecteur actuel.")

# Éléments de résultats (premiers 5)
result_items = soup.find_all('div', class_='list-item-publ')
if result_items:
    print(f"\n{len(result_items)} éléments de résultats trouvés.")
    print("Sélecteur XPath potentiel pour les éléments de résultats: //div[contains(@class, \'list-item-publ\')] ")
    for i, item in enumerate(result_items[:5]):
        title = item.find('div', class_='list-item-content-publ-title')
        summary = item.find('div', class_='list-item-content-publ-summary')
        print(f"  Résultat {i+1}:")
        print(f"    Titre: {title.get_text(strip=True) if title else 'N/A'}")
        print(f"    Résumé: {summary.get_text(strip=True) if summary else 'N/A'}")
else:
    print("Aucun élément de résultat trouvé avec le sélecteur actuel.")

# Bouton de pagination (Suivant)
next_page_button = soup.find('div', class_='page-button-next-publ')
if next_page_button:
    print(f"\nBouton de page suivante trouvé: {next_page_button.name} avec classes {next_page_button.get('class')}")
    print(f"Sélecteur XPath potentiel pour le bouton de page suivante: //div[contains(@class, \'page-button-next-publ\')] ")
else:
    print("Bouton de page suivante non trouvé avec le sélecteur actuel.")

# Bouton de pagination (Précédent)
prev_page_button = soup.find('div', class_='page-button-prev-publ')
if prev_page_button:
    print(f"Bouton de page précédente trouvé: {prev_page_button.name} avec classes {prev_page_button.get('class')}")
    print(f"Sélecteur XPath potentiel pour le bouton de page précédente: //div[contains(@class, \'page-button-prev-publ\')] ")
else:
    print("Bouton de page précédente non trouvé avec le sélecteur actuel.")

# Numéros de page (si présents)
page_numbers = soup.find_all('div', class_='page-button-publ')
if page_numbers:
    print(f"\n{len(page_numbers)} numéros de page trouvés.")
    print("Sélecteur XPath potentiel pour les numéros de page: //div[contains(@class, \'page-button-publ\')] ")
    for i, page_num in enumerate(page_numbers):
        print(f"  Numéro de page {i+1}: {page_num.get_text(strip=True)}")
else:
    print("Aucun numéro de page trouvé avec le sélecteur actuel.")

