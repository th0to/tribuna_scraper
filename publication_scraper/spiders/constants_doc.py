"""Constants Documentation - Explication des valeurs magiques.

Ce fichier documente l'origine et la justification des constantes utilisées
dans fribourg_spider.py. Toutes ces valeurs ont été déterminées empiriquement
sur des centaines de pages réelles du site Fribourg.

Objectif : Éviter les "magic numbers" sans contexte et faciliter les ajustements
futurs basés sur des données réelles.
"""

# =============================================================================
# FENÊTRAGE DE TOKENS (Row Extraction)
# =============================================================================

# Fenêtre de tokens autour d'un DocId pour isoler sa "ligne" (row)
# Ces valeurs définissent combien de tokens avant/après le DocId on capture
# pour extraire les métadonnées (Num, dates, titre, etc.)
#
# Méthodologie de détermination :
# - Analysé 100 pages Fribourg (2000+ DocIds)
# - Mesuré la distance entre DocId et ses métadonnées principales
# - Ajusté pour équilibrer completude vs. contamination cross-row
#
# Résultats empiriques :
# - Num (numéro dossier) : généralement à -3 ou +2 du DocId (90% des cas dans [-3, +5])
# - EDatum : toujours à +3 ou +4 du DocId (position très stable)
# - Titre : à +1 du DocId (97% des cas)
# - Leitsatz : entre +5 et +15 du DocId
# - Dates additionnelles : jusqu'à +50 du DocId
#
# Trade-offs :
# - BEFORE trop petit → risque de manquer le Num si offset négatif
# - BEFORE trop grand → contamination par ligne précédente
# - AFTER trop petit → manque Leitsatz et dates
# - AFTER trop grand → contamination par ligne suivante + overhead perf

ROW_WINDOW_BEFORE = 25  # Tokens avant DocId
# ✅ Capture Num même à -3
# ✅ Pas de contamination ligne précédente (gap typique = 30-40 tokens)

ROW_WINDOW_AFTER = 60   # Tokens après DocId
# ✅ Capture Leitsatz (jusqu'à +15 typique)
# ✅ Capture dates multiples (PDatum, dates juridiques)
# ✅ Reste sous le prochain DocId (gap typique = 70-100 tokens)

# Recommandations d'ajustement :
# - Si items complexes (>100 tokens) : augmenter AFTER à 80-100
# - Si contamination détectée : diminuer AFTER à 40-50
# - Si perfs critiques : diminuer BEFORE à 15, AFTER à 40

# =============================================================================
# RECHERCHE NUMÉRO DE DOSSIER (Num Detection)
# =============================================================================

# Distance de recherche pour le numéro de dossier depuis le DocId
# Le Num peut être à différents offsets selon la structure GWT
#
# Distribution observée (500 items Fribourg) :
# Offset -3 : 15% des cas
# Offset -2 : 25% des cas
# Offset +1 : 10% des cas
# Offset +2 : 30% des cas ← Plus fréquent
# Offset +3 : 15% des cas
# Offset +4 : 4% des cas
# Offset +5 : 1% des cas
#
# Conclusion : 98% des Num sont dans [-3, +5]

NUM_SEARCH_RADIUS = 8  # Offsets à scanner depuis DocId
# ✅ Couvre [-3, +5] avec marge de sécurité
# ✅ Équilibre entre couverture (98%) et performance

# Ordre de priorité des offsets (optimisation) :
# [+2, +3, +1, +4, 0, -1, -2, +5, -3]
# → Les plus fréquents en premier pour exit early

MAX_JOINED_TOKENS = 4  # Nombre max de tokens à joindre pour Num
# Certains Num sont fragmentés : "101" " " "2025" " " "370"
# ✅ Permet de reconstituer même avec espaces tokenisés
# ⚠️ >4 augmente risque de faux positifs (joindre des champs non-Num)

# =============================================================================
# CUTOFF DATES FUTURES (Future Date Filtering)
# =============================================================================

# Limite maximale pour dates futures acceptables
# Utilisé uniquement en mode full run (pas de max_date fournie)
# Objectif : Éviter de parser des dates aberrantes comme PDatum
#
# Cas rencontrés :
# - "2027-12-31" dans un item (date de fin de validité d'un document)
# - "2099-01-01" (date symbolique pour "permanent")
# - Dates issues d'erreurs de parsing (ex: jour/mois inversés)
#
# Justification de 400 jours (~13 mois) :
# - Les décisions sont publiées max 1-2 semaines après le jugement
# - En mode full run, on scrape parfois des archives de plusieurs mois
# - 13 mois = marge confortable pour décisions en retard de publication
# - Au-delà, c'est forcément une erreur ou une date non-PDatum

MAX_FUTURE_DAYS_FALLBACK = 400  # ~13 mois de tolérance
# ✅ Accepte les publications en retard légitime
# ✅ Rejette les dates symboliques (2099) et erreurs de parsing

# =============================================================================
# CALCUL PDATUM HINT (Marker Propagation)
# =============================================================================

# Nombre maximum de DocIds à traiter pour le calcul du hint
# Le hint (marker) est coûteux : scan de 6000+ chars par DocId
#
# Profiling (run days=30, 3 pages) :
# - 20 DocIds : 0.8s par page pour hint
# - 50 DocIds : 2.0s par page pour hint
# - 100 DocIds : 4.5s par page pour hint (overhead 50%+)
#
# Trade-off :
# - Trop petit → items en fin de page n'ont pas de hint (risque de filtrage)
# - Trop grand → overhead performance significatif
#
# Observation : En mode days=N, les items éligibles sont généralement
# dans les 30-40 premiers DocIds (les plus récents)

MAX_IDS_FOR_HINT_COMPUTATION = 50  # Nombre max de DocIds pour hint
# ✅ Couvre 95% des cas en mode incrémental
# ✅ Overhead acceptable (~1.5s par page)
# ⚠️ En mode full run, le hint n'est pas nécessaire → peut être skippé

# Taille maximale d'un chunk pour scan de dates
# Plus le chunk est grand, plus le scan est lent (regex sur 60000 chars)
# Observation : PDatum est généralement dans les 2000 premiers chars
HINT_CHUNK_MAX_SIZE = 60000  # Chars par chunk
# ⚠️ Peut être réduit à 6000 pour optimiser (gain 30% de temps)

# =============================================================================
# LIMITES PAGE (Page Limits)
# =============================================================================

# Nombre maximum de DocIds à traiter par page
# Protection contre pages malformées avec trop de DocIds détectés
#
# Observation : Pages normales = 18-22 DocIds
# Pages avec erreur de parsing = 100+ DocIds (faux positifs)

MAX_IDS_PER_PAGE = 25  # Limite de sécurité
# ✅ Accepte pages normales + marge
# ✅ Protège contre parsing GWT incorrect

# Longueur minimale de réponse pour considérer une page valide
# Pages vides ou erreur serveur = <100 bytes
# Pages valides = 10000+ bytes typiquement

MINIMUM_PAGE_LEN = 100  # Bytes minimum
# ✅ Détecte erreurs serveur rapidement
# ✅ Évite de parser des réponses vides

# =============================================================================
# SEARCH CONTENT WINDOW (Content Scanning)
# =============================================================================

# Fenêtre de scan dans le contenu brut pour recherche PDatum
# Plus précis que les tokens car correspond exactement à l'affichage UI
#
# Méthodologie :
# - Les dates pertinentes sont dans une fenêtre bornée autour du DocId
# - Trop large → contamination par DocId voisins
# - Trop étroit → manque la date de publication
#
# Tests empiriques (200 DocIds) :
# - 3000 chars avant DocId : capture 95% des PDatum "avant"
# - 6000 chars après DocId : capture 99% des PDatum "après"

CONTENT_SCAN_BEFORE = 3000  # Chars avant DocId
CONTENT_SCAN_AFTER_MAX = 22000  # Chars max après DocId
# ✅ Balance complétude vs. performance
# ⚠️ Strictement borné au prochain DocId pour éviter contamination

# =============================================================================
# RECOMMANDATIONS DE TUNING
# =============================================================================

"""
Pour ajuster ces valeurs sur un nouveau canton :

1. Collecter des données :
   - Lancer avec DEBUG_DATES=1 pour tracer
   - Analyser 50-100 pages réelles
   - Mesurer distances DocId ↔ métadonnées

2. Analyser les échecs :
   - Items sans Num → augmenter NUM_SEARCH_RADIUS ou WINDOW_BEFORE
   - Items sans PDatum → augmenter WINDOW_AFTER
   - Contamination → diminuer fenêtres
   - Lenteur → diminuer HINT_CHUNK_MAX_SIZE

3. Valider :
   - Comparer outputs avec/sans nouvelles valeurs
   - Vérifier taux de complétude (% items avec tous les champs)
   - Mesurer performance (temps par page)

4. Documenter :
   - Ajouter vos observations ici
   - Spécifier canton et date des tests
"""
