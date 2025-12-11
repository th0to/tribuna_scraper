# Publication Scraper - Tribuna Spider

Spider Scrapy professionnel pour extraire les publications des sites utilisant Tribuna (Fribourg, Graubünden, etc.)

## 🎯 Caractéristiques

- ✅ **POST requests directes** (pas de Playwright, performance optimale)
- ✅ **Parsing GWT** avec regex inspirées du code client
- ✅ **Décryptage PDF** automatique
- ✅ **Mode dual**: scraping initial complet OU incrémental (updates)
- ✅ **Multi-canton** via fichier de configuration JSON
- ✅ **Compatible pipeline client** (structure items conforme)
- ✅ **Production-ready**: retry, autothrottle, logging professionnel

## 📦 Installation

```bash
# 1. Installer les dépendances (Scrapy uniquement, pas de Playwright)
pip install -r requirements.txt

# 2. Configurer l'environnement
Copy-Item .env.example .env
# Éditer .env selon vos besoins
```

## 🚀 Utilisation

### Scraping Initial (toutes les décisions)

```bash
# Fribourg (13563 décisions)
scrapy crawl tribuna -a canton=fribourg

# Graubünden (14043 décisions)
scrapy crawl tribuna -a canton=graubunden
```

### Scraping Incrémental (updates hebdomadaires)

```bash
# Derniers 7 jours
scrapy crawl tribuna -a canton=fribourg -a ab=2024-11-25

# Ou via variable d'environnement
# Dans .env: START_DATE=2024-11-25
scrapy crawl tribuna -a canton=fribourg
```

### Export personnalisé

```bash
# JSON uniquement (par défaut)
scrapy crawl tribuna -a canton=fribourg

# CSV et JSON
# Dans .env: OUTPUT_FORMAT=both
scrapy crawl tribuna -a canton=fribourg
```

## 📁 Structure des Items

Compatible avec le modèle client (basé sur tribuna.py):

```python
{
  'Kanton': 'FR',                    # Code canton
  'DocId': 'abc123...',              # ID unique
  'Num': '123/2024',                 # Numéro dossier
  'Signatur': '...',                 # Signature
  'Gericht': 'Tribunal cantonal',    # Tribunal
  'Kammer': 'Chambre civile',        # Chambre
  'EDatum': '2024-11-15',            # Date de décision
  'PDatum': '2024-11-20',            # Date de publication
  'Titel': 'Titre...',               # Titre
  'Leitsatz': 'Résumé...',           # Leitsatz
  'Rechtsgebiet': 'Droit civil',     # Domaine juridique
  'PDFUrls': ['https://...pdf'],     # URLs PDF
  'HTMLUrls': ['https://...html'],   # URLs HTML (optionnel)
  'PageNr': 5                        # Page de pagination
}
```

## ⚙️ Configuration par Canton

Le fichier `publication_scraper/cantons_config.json` contient la configuration pour chaque canton:

- **URLs** (result_page_url, decrypt_page_url, etc.)
- **Headers GWT** (X-GWT-Permutation, etc.)
- **Templates de requête** (RESULT_QUERY_TPL, RESULT_QUERY_TPL_AB)
- **Flags** (encrypted, needs_cookie, etc.)

### Ajouter un nouveau canton

1. Copier la configuration de `fribourg` dans `cantons_config.json`
2. Adapter les URLs et headers
3. Tester: `scrapy crawl tribuna -a canton=nouveau_canton`

## 🔧 Variables d'Environnement

| Variable | Description | Défaut |
|----------|-------------|--------|
| `CANTON` | Canton par défaut | `fribourg` |
| `START_DATE` | Date début scraping incrémental | `` (vide = complet) |
| `DOWNLOAD_DELAY` | Délai entre requêtes (s) | `1` |
| `CONCURRENT_REQUESTS` | Requêtes simultanées | `1` |
| `MAX_PAGES` | Limite de pages (0 = ∞) | `0` |
| `OUTPUT_DIR` | Dossier de sortie | `./output` |
| `OUTPUT_FORMAT` | Format (json/csv/both) | `json` |
| `LOG_LEVEL` | Niveau de log | `INFO` |
| `RETRY_TIMES` | Nombre de retry | `3` |
| `AUTOTHROTTLE_ENABLED` | AutoThrottle activé | `false` |

## 📊 Résultats

Les fichiers sont sauvegardés dans `OUTPUT_DIR`:

```
output/
├── publications_fribourg_20241202_143025.json
└── publications_fribourg_20241202_143025.csv
```

Format JSON avec statistiques par année:
```
2024: 450 décisions
2023: 1200 décisions
2022: 980 décisions
...
```

## 🔌 Intégration Pipeline Client

### Pipeline actuelle (placeholder)

La pipeline `PublicationPipeline` sauvegarde uniquement les métadonnées.

### À intégrer

Le client doit ajouter ses pipelines existantes dans `settings.py`:

```python
ITEM_PIPELINES = {
    'publication_scraper.pipelines.PublicationPipeline': 100,  # Métadonnées
    'client_pipelines.PDFPipeline': 200,                       # Download PDF
    'client_pipelines.HTMLPipeline': 300,                      # Download HTML
}
```

### Files middleware Scrapy

Le client utilise le middleware Files de Scrapy. Configuration suggérée:

```python
# Dans settings.py
FILES_STORE = 'path/to/sharepoint'
FILES_URLS_FIELD = 'PDFUrls'
FILES_RESULT_FIELD = 'files'
```

## 🏗️ Architecture Technique

### Différences avec l'ancien spider Playwright

| Aspect | Ancien (Playwright) | Nouveau (POST direct) |
|--------|---------------------|----------------------|
| Technologie | Playwright + Selenium | HTTP POST pur |
| Performance | Lent (navigateur) | Rapide (requêtes HTTP) |
| Ressources | Lourd (Chrome) | Léger |
| Headless issues | ❌ Problèmes | ✅ N/A |
| Production ready | ⚠️ Limité | ✅ Oui |

### Parsing GWT

Le spider parse les réponses `//OK[[...]]` avec regex:

1. Nettoyage: `reVor.sub('', response.text)`
2. Extraction: `reAll.findall(content)` → liste de valeurs
3. Identification: regex pour ID, dates, numéros, pfad
4. Décryptage: POST vers `decrypt_page_url` si `encrypted=true`

## 🔁 Recent changes (2025-12-11)

Les modifications récentes apportées au spider lors d'un run de validation :

- Ajout de la constante `MINIMUM_PAGE_LEN` pour remplacer le "magic number" et stabiliser la détection de pages valides.
- Correction de l'extraction du nombre total de résultats (`trefferzahl`) : la détection utilise désormais le `current_page` ou l'absence de `trefferzahl` plutôt que le compteur interne de requêtes.
- Amélioration du traitement des cookies : `set_cookie` rassemble maintenant plusieurs en-têtes `Set-Cookie`, décode en `utf-8` (avec `errors='ignore'`) et construit une en-tête `Cookie: name=value; name2=value2` pour les requêtes suivantes.
- Robustification de la vérification PDF (`_is_pdf_ok`) : accepte les statuts `200` et `206`, gère correctement les headers bytes/str et vérifie `Content-Type`.
- Sauvegarde des réponses de décryptage dans `publication_scraper/output/debug_decrypts/` pour faciliter l'analyse.

Ces correctifs ont été validés par un run de test sur `fribourg` (2025-12-11) :

- Items extraits : 43
- Pagination atteinte jusqu'à ~80 pages avant interruption manuelle
- Les PDFs liés ont été vérifiés et acceptés (content-type `application/pdf`).

Prochains correctifs recommandés (à implémenter):

- Unifier l'utilisation des regex (`RE_*` vs `self.re*`) pour améliorer la testabilité.
- Ajouter un helper `_safe_get` pour éviter les IndexError lors de l'accès aux tokens.
- Instrumenter des compteurs Scrapy (`self.crawler.stats.inc_value(...)`) pour les drops, erreurs de décrypt, et items émis.

Ces tâches sont planifiées et peuvent être appliquées dans les prochains commits.

## 📋 Préparation Meeting (François Uldry)

### Objectifs clés
- Récupérer l'année de publication et une URL PDF stable pour toutes les décisions publiées via Tribuna (6 cantons cibles, extensible à 9–10).
- Offrir deux modes d'exécution: first-run (historique complet) et mises à jour hebdomadaires (N jours précédents + aujourd'hui).
- Être compatible avec Scrapy 2.13.3 et les pipelines existantes (PDF/HTML, déduplication, mises à jour).

### Démo rapide (Windows PowerShell)
```
cd "C:\Users\thoma\OneDrive\Bureau\estiam\tribuna\v3"
& ".venv\Scripts\python.exe" -m scrapy crawl tribuna -a canton=fribourg -s MAX_ITEMS=42 -s LOG_LEVEL=INFO -o "output\demo_fr.json"
```
- Mise à jour 7 jours:
```
& ".venv\Scripts\python.exe" -m scrapy crawl tribuna -a canton=fribourg -a days=7 -s LOG_LEVEL=INFO -o "output\demo_fr_7d.json"
```

### Observations sur les outputs anciens
- Les sorties plus anciennes (ex. `output/publications_fribourg_20251209_052442.json`) illustrent bien le comportement attendu de la plateforme: 20 items par page, tous exploitables (année de publication + PDF valide).
- Le spider actuel reproduit ce schéma en first-run. En mode incrémental, si `DEFER_DECRYPT` évite les lignes plus anciennes, la page peut émettre moins de 20 items; désactiver `DEFER_DECRYPT` pour des runs de démonstration maximisant 20/20.
- Normalisation possible de `PageNr` en 1-based pour lisibilité humaine.

### Points techniques validés
- Parsing GWT robuste (fenêtre locale, regex unifiées), accès sécurisé aux tokens, unification des candidats PDF et vérification `200/206` + `application/pdf`.
- Cookies: agrégation de `Set-Cookie` → `Cookie` unique pour endpoints de décrypt/download.
- Contrôles d'arrêt: `MAX_ITEMS`, `MAX_PAGES`, et early-stop basé sur `min_date` (fraction configurable).
- Statistiques détaillées: items émis, pages traitées, drops par raison, échecs de décrypt.

### Questions anticipées
- Pourquoi pas Playwright? Les payloads GWT sont stables et plus rapides à traiter en POST headless; évite les contraintes GUI.
- Résilience aux changements? Fenêtre heuristique + regex constants + journalisation des diff numériques; patch rapide si drift.
- Multi-canton? Piloté par `cantons_config.json` (URLs, headers, templates). Ajout ciblé par canton avec validation.
- Chambre/tribunal? Extractibles si disponibles dans les tokens (`department`/`VKammer`), ajoutables aux items.

### Mesures de succès
- ≥ 99% des décisions couvertes par canton (full run).
- ≥ 98% des PDF vérifiés (`application/pdf`).
- Mises à jour hebdomadaires finies sans erreurs (Exit Code 0), fenêtres temporelles respectées.


## 🐛 Dépannage

### Test de connexion

```bash
# Vérifier la configuration
scrapy crawl tribuna -a canton=fribourg -s LOG_LEVEL=DEBUG

# Limiter à 2 pages pour test
# Dans .env: MAX_PAGES=2
scrapy crawl tribuna -a canton=fribourg
```

### Erreurs courantes

**"Canton 'xxx' non trouvé"**
→ Vérifier `cantons_config.json`, ajouter la config du canton

**"0 résultats trouvés"**
→ Vérifier les templates de requête (RESULT_QUERY_TPL)

**"Impossible de décrypter le PDF"**
→ Activer `LOG_LEVEL=DEBUG`, vérifier DECRYPT_START/END

**Timeout**
→ Augmenter `DOWNLOAD_TIMEOUT` dans .env

## 📝 TODO - Points à clarifier avec le client

### Questions pour l'appel

1. **Items.py**: Demander le fichier `items.py` exact du client pour conformité 100%

2. **Pipelines**: Obtenir les pipelines PDF/HTML existantes pour intégration

3. **Gericht/Kammer**: Comment détecter la cour et la chambre ?
   - Actuellement: `VKammer` extrait, mais `Gericht`/`Kammer` vides
   - Besoin d'une fonction `detect()` comme dans tribuna.py

4. **Signatur**: Comment générer la signature ?
   - Actuellement: vide, besoin de la logique

5. **Autres cantons**: Configurations pour les 4 autres cantons Tribuna ?
   - Fribourg ✅
   - Graubünden ✅ (à tester)
   - 4 autres: URLs, headers, permutations GWT ?

6. **HTML extraction**: Le flag `HOLE_AUCH_HTML` est à `false`
   - Faut-il activer pour certains cantons ?
   - Logique de parsing HTML à implémenter ?

7. **Stockage Sharepoint**: Architecture de stockage souhaitée ?
   - Par année: `2024/decision_123.pdf`
   - Par cour: `tribunal_cantonal/2024/decision_123.pdf`
   - Autre ?

8. **Planification Azure**: Cron jobs hebdomadaires ?
   - Script de lancement avec `ab=$(date -d '7 days ago' +%Y-%m-%d)` ?

## 🚀 Prêt pour Production

- [x] POST requests directes (pas de Playwright)
- [x] Parsing GWT complet
- [x] Décryptage PDF
- [x] Mode dual (initial/incrémental)
- [x] Multi-canton via config JSON
- [x] Structure items compatible client
- [x] Pipeline extensible (placeholder pour client)
- [x] Variables d'environnement
- [x] Retry + AutoThrottle
- [x] Logging professionnel
- [x] Documentation complète

**Reste à faire**: Intégrer items.py, pipelines, et configs des 4 autres cantons fournis par le client.
