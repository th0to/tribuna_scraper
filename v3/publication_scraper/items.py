import scrapy


class PublicationItem(scrapy.Item):
    """
    Item pour stocker les informations extraites des publications
    Compatible avec le modèle du client (basé sur tribuna.py)
    """
    # Identifiants
    Kanton = scrapy.Field()          # Code canton (FR, GR, etc.)
    DocId = scrapy.Field()           # ID unique du document
    Num = scrapy.Field()             # Numéro de dossier
    Signatur = scrapy.Field()        # Signature de la décision
    
    # Juridiction
    Gericht = scrapy.Field()         # Tribunal
    Kammer = scrapy.Field()          # Chambre
    VGericht = scrapy.Field()        # Tribunal virtuel
    VKammer = scrapy.Field()         # Chambre virtuelle
    
    # Dates
    EDatum = scrapy.Field()          # Date de décision (Entscheiddatum)
    PDatum = scrapy.Field()          # Date de publication (Publikationsdatum)
    
    # Contenu
    Titel = scrapy.Field()           # Titre de la décision
    Leitsatz = scrapy.Field()        # Résumé/Leitsatz
    Rechtsgebiet = scrapy.Field()    # Domaine juridique
    
    # URLs et fichiers
    PDFUrls = scrapy.Field()         # Liste des URLs PDF
    HTMLUrls = scrapy.Field()        # Liste des URLs HTML (optionnel)
    
    # Métadonnées techniques
    Raw = scrapy.Field()             # Données brutes GWT (debug)
    PageNr = scrapy.Field()          # Numéro de page (pagination)
