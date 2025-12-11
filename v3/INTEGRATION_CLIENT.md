# INTEGRATION_CLIENT.md

## 📋 Checklist d'Intégration avec le Client

Ce document liste les éléments à obtenir du client pour finaliser l'intégration.

### 1. Fichiers à demander

#### items.py (PRIORITÉ HAUTE)
```python
# Demander le fichier items.py exact du client
# Questions:
# - Quels champs sont obligatoires vs optionnels ?
# - Y a-t-il des validations spécifiques ?
# - Format attendu pour les listes (PDFUrls, HTMLUrls) ?
```

#### pipelines.py (PRIORITÉ HAUTE)
```python
# Obtenir les pipelines PDF et HTML existantes
# Questions:
# - Quelle pipeline pour les PDF ?
# - Quelle pipeline pour les HTML ?
# - Middleware Files Scrapy utilisé ?
# - Configuration FILES_STORE (Sharepoint) ?
# - Gestion des doublons/updates ?
```

### 2. Logique Métier à implémenter

#### Fonction detect() (PRIORITÉ MOYENNE)
```python
# Dans tribuna.py ligne ~200:
# signatur, gericht, kammer = self.detect("", vkammer, num)
#
# Questions:
# - Quelle est la logique de detect() ?
# - Comment mapper vkammer → gericht/kammer ?
# - Y a-t-il un fichier de mapping par canton ?
# - Exemple: "2 CI" → Gericht="Tribunal cantonal", Kammer="2ème chambre civile" ?
```

#### Génération Signatur (PRIORITÉ BASSE)
```python
# Actuellement vide dans notre code
# Questions:
# - Format de la signature ? (ex: "FR_TC_2024_123")
# - Règle de construction ?
```

### 3. Configuration Cantons Tribuna

#### Cantons restants (PRIORITÉ HAUTE)

Nous avons configuré:
- ✅ Fribourg (FR)
- ✅ Graubünden (GR) - à valider

Cantons manquants (selon le client: "9 ou 10 sources"):
- ❓ Canton 3
- ❓ Canton 4
- ❓ Canton 5
- ❓ Canton 6
- ❓ Canton 7
- ❓ Canton 8
- ❓ Canton 9
- ❓ Canton 10 (optionnel)

**Pour chaque canton, demander:**
```json
{
  "name": "Nom du canton",
  "kanton_kurz": "XX",
  "base_url": "https://...",
  "result_page_url": "https://.../rpc/publicTribunalPubl",
  "decrypt_page_url": "https://.../rpc/publicTribunalPubl",
  "headers": {
    "Host": "...",
    "X-GWT-Permutation": "...",  // ⚠️ IMPORTANT à extraire
    "Referer": "..."
  },
  "result_query_tpl": "7|0|9|...",
  "result_query_tpl_ab": "7|0|10|...",
  "encrypted": true/false,
  "ascii_encrypted": true/false
}
```

**Comment obtenir X-GWT-Permutation:**
1. Ouvrir DevTools → Network
2. Filtrer XHR
3. Effectuer une recherche sur le site
4. Copier la requête POST vers `publicTribunalPubl`
5. Headers → X-GWT-Permutation

### 4. Architecture Stockage

#### Questions Sharepoint (PRIORITÉ MOYENNE)

```
Quelle structure de dossiers ?

Option A - Par année:
sharepoint/
├── 2024/
│   ├── FR_decision_123.pdf
│   ├── FR_decision_124.pdf
│   └── GR_decision_456.pdf
└── 2023/
    └── ...

Option B - Par canton/année:
sharepoint/
├── FR/
│   ├── 2024/
│   │   ├── decision_123.pdf
│   │   └── decision_124.pdf
│   └── 2023/
│       └── ...
└── GR/
    └── ...

Option C - Par cour/année:
sharepoint/
├── Tribunal_Cantonal_FR/
│   ├── Chambre_Civile/
│   │   ├── 2024/
│   │   │   └── decision_123.pdf
│   │   └── 2023/
│   └── Chambre_Penale/
└── ...

Option D - Autre ?
```

#### Nommage des fichiers PDF

```
Format actuel: {Num}_{DocId}.pdf
Exemple: 123_2024_abc123def456.pdf

Questions:
- Format préféré ?
- Inclure le canton dans le nom ?
- Inclure la date ?
```

### 5. Déploiement Azure

#### Questions Environnement (PRIORITÉ BASSE)

```bash
# Serveur Ubuntu 24
# Questions:
# - Python version ? (3.10, 3.11, 3.12 ?)
# - Virtualenv séparé par spider ou global ?
# - Chemin d'installation préféré ?
# - User système qui exécutera les spiders ?
```

#### Planification (PRIORITÉ BASSE)

```bash
# Cron hebdomadaire pour updates
# Questions:
# - Jour préféré ? (dimanche soir, lundi matin ?)
# - Heure ? (nuit, tôt le matin ?)
# - Tous les cantons en parallèle ou séquentiel ?
# - Gestion des échecs (retry, alertes) ?

# Exemple script:
#!/bin/bash
START_DATE=$(date -d '7 days ago' +%Y-%m-%d)
cd /opt/tribuna_scraper
source venv/bin/activate

scrapy crawl tribuna -a canton=fribourg -a ab=$START_DATE
scrapy crawl tribuna -a canton=graubunden -a ab=$START_DATE
# ... autres cantons
```

### 6. Tests et Validation

#### Phase de test (PRIORITÉ HAUTE)

```bash
# Test 1: Connexion Fribourg
scrapy crawl tribuna -a canton=fribourg -s MAX_PAGES=2 -s LOG_LEVEL=DEBUG

# Test 2: Décryptage PDF
# Vérifier qu'on obtient bien des URLs PDF valides

# Test 3: Scraping incrémental
scrapy crawl tribuna -a canton=fribourg -a ab=2024-11-01

# Test 4: Volume complet (sur petit canton d'abord)
# Tester sur le canton avec le moins de décisions
```

#### Validation des données (PRIORITÉ HAUTE)

```python
# Questions:
# - Comment valider la qualité des données ?
# - Comparaison avec extraction manuelle ?
# - Champs critiques à vérifier ?
# - Taux d'erreur acceptable ?
```

### 7. Monitoring et Logs

#### Questions (PRIORITÉ BASSE)

```
- Où envoyer les logs ? (fichier local, service cloud ?)
- Notifications en cas d'erreur ? (email, Slack, Teams ?)
- Métriques à tracker ?
  * Nombre de décisions par run
  * Durée d'exécution
  * Taux d'erreur
  * Taille des fichiers téléchargés
```

### 8. HTML Extraction (OPTIONNEL)

#### Si nécessaire (PRIORITÉ BASSE)

```python
# Le flag HOLE_AUCH_HTML est à false
# Si le client veut aussi les HTML:

# Questions:
# - Quels cantons nécessitent HTML ?
# - Format de stockage HTML ?
# - Priorité HTML vs PDF ?
# - Parser le HTML pour extraire metadata supplémentaire ?
```

## 📞 Ordre d'Appel Recommandé

### Phase 1: Validation Architecture (30 min)
1. ✅ Confirmer approche POST direct vs Playwright
2. ✅ Valider structure items.py actuelle
3. ✅ Confirmer mode dual (initial/incrémental)

### Phase 2: Intégration Technique (45 min)
4. Obtenir items.py et pipelines.py exactes
5. Obtenir fonction detect() et logique signatur
6. Configurations des 8 autres cantons Tribuna

### Phase 3: Déploiement (15 min)
7. Architecture stockage Sharepoint
8. Planification Azure et environnement

### Phase 4: Tests (temps variable)
9. Tests sur Fribourg (petit échantillon)
10. Validation résultats avec le client
11. Déploiement progressif autres cantons

## ✅ Checklist Avant l'Appel

- [ ] Lire complètement ce document
- [ ] Préparer questions sur detect() et signatur
- [ ] Avoir cantons_config.json ouvert pour prendre notes
- [ ] Préparer demo rapide du spider Fribourg
- [ ] Liste des 8 cantons manquants
- [ ] Questions sur architecture Sharepoint
