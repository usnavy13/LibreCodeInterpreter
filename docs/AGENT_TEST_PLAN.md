# Cahier de tests fonctionnels — Agents LibreChat

Ce document décrit les tests métier pour valider le bon fonctionnement de chaque agent LibreChat connecté au runtime LibreCodeInterpreter.

**Principe de validation double :**
- **Validation utilisateur** : le résultat visible (fichier produit, contenu, mise en forme) est correct
- **Validation technique** : l'agent a utilisé la bonne méthodologie, les bons scripts, dans le bon ordre (vérifiable dans les logs d'exécution code du chat)

**Comment vérifier la méthodologie** : dans LibreChat, chaque appel `execute_code` est visible dans le fil de conversation (bloc de code exécuté + sortie). On vérifie que les commandes/imports correspondent à la méthodologie attendue.

**Règles transversales (tous les agents)** :
- **Chaînage obligatoire** : toutes les étapes doivent être dans UN SEUL bloc `execute_code` (les fichiers temporaires ne persistent pas entre les appels)
- **Auteur** : pour les tracked changes et métadonnées, l'agent doit utiliser le nom réel de l'utilisateur LibreChat (via `{{current_user}}`)
- **pack.py** : syntaxe positionnelle `pack.py <dir/> <output.docx>` (pas de flag `-o`)
- **Fichiers temporaires** : écrits dans `/tmp/`, pas `/mnt/data/` (seuls les fichiers de sortie vont dans `/mnt/data/`)
- **Palette OBA** : quand l'utilisateur ne fournit pas de charte, utiliser les couleurs On Behalf AI

---

## Agent 1 — Word DOCX Complete

### D01 — Reproduire un CR à partir d'un template utilisateur

**Prérequis** : uploader un DOCX de compte-rendu existant (avec logo, styles, en-têtes)

**Prompt** :
> Voici un exemple de compte-rendu de réunion de notre entreprise. Analyse sa structure et sa mise en forme (styles, polices, couleurs, en-têtes, pieds de page). Puis à partir du transcript suivant, produis un nouveau CR dans un format strictement identique :
> 
> "Réunion produit du 14 avril 2026. Participants : Marie, Jean, Sophie. Ordre du jour : lancement V2, budget marketing, planning Q3. Décision : lancement fixé au 15 juin. Action : Jean prépare le plan média avant le 1er mai. Prochaine réunion : 28 avril."

**Méthodologie attendue** :
1. `unpack.py` sur le template DOCX pour analyser la structure XML (styles, headers, footers)
2. Lecture du XML avec **lxml** pour identifier les styles utilisés
3. Copie du template + remplacement du contenu via **lxml** (jamais `content.replace()` sur le XML brut)
4. `pack.py unpacked/ output.docx` pour recompresser (syntaxe positionnelle, pas `-o`)
5. `validate.py` pour valider le résultat
6. Le tout dans **un seul bloc** `execute_code`

**Validation utilisateur** : ouvrir le DOCX produit dans Word/LibreOffice — en-têtes, pieds de page, polices et mise en forme identiques au template.

**Validation technique** : vérifier dans les blocs de code que `unpack.py` a été appelé, que lxml est utilisé (pas `content.replace()`), et que le résultat a été validé.

---

### D01b — Créer un CR depuis le template OBA (sans template utilisateur)

**Prérequis** : aucun fichier uploadé

**Prompt** :
> Crée un compte-rendu de la réunion suivante : "Réunion du 10 avril 2026, visioconférence Teams, organisée par Damien Juillard. Participants : Sophie Martin (Directrice RH, Nextera Corp) et Damien Juillard (Consultant IA, On Behalf AI). Sujet : cadrage projet IA RH. Décisions : lancement POC chatbot RH. Actions : étude faisabilité avant le 24 avril."

**Méthodologie attendue** :
1. `fill_cr_template.py` avec le template OBA `template-compte-rendu.docx`
2. Config JSON avec `meeting` (title, date, location, organizer) + `participants` + `sections`
3. Le config.json écrit dans `/tmp/` (pas `/mnt/data/`)

**Validation utilisateur** : DOCX avec page de garde OBA (logo, titre, métadonnées), tableau participants rempli, sections structurées.

**Validation technique** : vérifier que `fill_cr_template.py` est appelé (pas de manipulation XML manuelle).

---

### D01c — Créer un document technique depuis le template OBA

**Prérequis** : aucun fichier uploadé

**Prompt** :
> Crée un guide d'installation pour Docker sur Ubuntu, avec prérequis, étapes d'installation, configuration, et dépannage.

**Méthodologie attendue** :
1. `fill_template.py` avec le template OBA `template-base.docx`
2. Config JSON avec `placeholders` (titre, auteur, date) + `sections` avec `text`, `bullets`, `numbered`, `code`, `table`
3. Le config.json écrit dans `/tmp/`

**Validation utilisateur** : DOCX avec page de garde OBA, titres numérotés, listes à tirets, blocs de code en blanc sur noir (style "Code"), tableaux avec en-têtes bleus.

**Validation technique** : vérifier que `fill_template.py` est appelé, que le config JSON contient des types variés (`bullets`, `code`, `table`, `numbered`).

---

### D02 — Révision juridique avec tracked changes

**Prérequis** : uploader un DOCX de contrat ou CGV

**Prompt** :
> Voici nos CGV actuelles. Le service juridique nous demande de remplacer toutes les occurrences de "le Client" par "l'Utilisateur", et de remplacer "30 jours" par "15 jours ouvrés" partout dans le document. Fais ces modifications en tracked changes pour que notre juriste puisse les réviser dans Word.

**Méthodologie attendue** :
1. `unpack.py` sur le DOCX source
2. `tracked_replace.py` avec `--old "le Client" --new "l'Utilisateur" --author "{{current_user}}"`
3. `tracked_replace.py` avec `--old "30 jours" --new "15 jours ouvrés" --author "{{current_user}}"`
4. `pack.py` pour recompresser
5. `validate.py` pour valider

**Validation utilisateur** : ouvrir le DOCX dans Word → les modifications apparaissent en redline (barré/souligné), auteur "AI-Agent", le texte original est visible.

**Validation technique** : vérifier que `tracked_replace.py` a été appelé (pas python-docx ni manipulation manuelle du texte), avec les bons arguments `--old`/`--new`.

---

### D03 — Accepter les tracked changes et produire une version propre

**Prérequis** : le DOCX produit par D02 (avec tracked changes)

**Prompt** :
> Le juriste a validé toutes les modifications. Accepte tous les tracked changes et produis la version finale propre du document.

**Méthodologie attendue** :
1. `accept_changes.py --input <fichier.docx> --output <fichier_clean.docx>`
2. (utilise LibreOffice en headless avec une macro pour accepter les révisions)

**Validation utilisateur** : le DOCX produit ne contient plus aucune marque de révision.

**Validation technique** : vérifier que `accept_changes.py` a été appelé (et non pas une manipulation XML manuelle ou un simple remplacement de texte).

---

### D04 — Ajouter des commentaires de relecture

**Prérequis** : uploader un DOCX de proposition commerciale

**Prompt** :
> Relis cette proposition commerciale et ajoute des commentaires Word aux endroits suivants : (1) sur le paragraphe "Délais de livraison", commente "À vérifier avec la logistique", (2) sur le montant total, commente "Remise de 10% à négocier ?".

**Méthodologie attendue** :
1. `unpack.py` sur le DOCX
2. Identification des passages cibles dans le XML
3. `comment.py` pour injecter les commentaires dans le XML décompressé
4. `pack.py` pour recompresser
5. `validate.py`

**Validation utilisateur** : les commentaires apparaissent dans le volet de révision de Word, positionnés aux bons endroits.

**Validation technique** : vérifier que `comment.py` a été utilisé (pas une création from scratch du document).

---

### D05 — Conversion Markdown → DOCX professionnel

**Prompt** :
> Convertis ce texte markdown en document Word propre avec des vrais styles Word (pas juste du texte formaté) :
> 
> ```markdown
> # Politique de télétravail
> ## 1. Principes généraux
> Le télétravail est ouvert à tous les collaborateurs en CDI ayant validé leur période d'essai.
> ## 2. Modalités
> - Maximum 3 jours par semaine
> - Accord du manager requis
> - Équipement fourni par l'entreprise
> ## 3. Obligations
> Le collaborateur s'engage à **respecter les horaires** définis et à être **joignable** sur les outils de communication internes.
> ```

**Méthodologie attendue** :
1. `pandoc` avec `--reference-doc=$SKILLS_ROOT/docx/templates/onbehalfai/reference-pandoc.docx` pour appliquer les styles OBA
2. Optionnel : `--lua-filter=heading-unnumbered-v4.lua` pour les titres non numérotés
3. `validate.py` sur le résultat

**Validation utilisateur** : dans Word, les titres sont en style Heading1/Heading2 avec la charte OBA (Arial, couleurs navy), les listes sont des vraies listes Word.

**Validation technique** : vérifier que `pandoc` avec `--reference-doc` OBA a été utilisé (pas une création manuelle via python-docx).

---

### D06 — Fusion de deux documents Word

**Prérequis** : uploader deux DOCX (par exemple deux chapitres d'un rapport)

**Prompt** :
> Fusionne ces deux documents Word en un seul, en gardant la mise en forme de chacun. Le premier document doit apparaître en premier, suivi d'un saut de page, puis le second.

**Méthodologie attendue** :
1. Utilisation de `docxcompose` ou `python-docx` pour la fusion avec saut de page
2. `validate.py` sur le résultat

**Validation utilisateur** : le document fusionné contient les deux parties avec un saut de page entre elles, les styles de chaque partie sont préservés.

**Validation technique** : vérifier l'utilisation de `docxcompose` ou de `python-docx` avec `add_page_break()`.

---

### D07 — Extraction de contenu structuré depuis un DOCX

**Prérequis** : uploader un DOCX complexe (rapport annuel avec tableaux, images, sections)

**Prompt** :
> Analyse ce document Word et extrais-moi un résumé structuré : liste des titres de sections, nombre de tableaux, nombre d'images, nombre de pages estimé, et le texte des 3 premiers paragraphes.

**Méthodologie attendue** :
1. `unpack.py` pour décompresser et analyser la structure XML
2. OU `python-docx` pour lire les paragraphes, tables, images
3. OU `pandoc` pour extraire en markdown puis analyser

**Validation utilisateur** : le résumé est précis et correspond au document.

**Validation technique** : vérifier qu'au moins un outil structuré a été utilisé (pas juste `mammoth` ou extraction brute de texte).

---

### D08 — Conversion DOCX → PDF haute fidélité

**Prérequis** : uploader un DOCX avec mise en forme complexe (colonnes, tableaux, images)

**Prompt** :
> Convertis ce document Word en PDF en conservant exactement la mise en forme : tableaux, images, en-têtes/pieds de page, numérotation.

**Méthodologie attendue** :
1. `soffice --headless --convert-to pdf` (LibreOffice, seule méthode qui préserve fidèlement la mise en forme Word)
2. OU appel via `office/soffice.py` helper

**Validation utilisateur** : le PDF produit est visuellement identique au DOCX ouvert dans Word.

**Validation technique** : vérifier que `soffice` a été utilisé (pas `pandoc` ni `reportlab` qui perdent la mise en forme).

---

### D09 — Conversion d'un vieux .doc en .docx

**Prérequis** : uploader un fichier `.doc` (format ancien)

**Prompt** :
> Ce fichier est dans l'ancien format Word .doc. Convertis-le en .docx moderne.

**Méthodologie attendue** :
1. `soffice --headless --convert-to docx` (LibreOffice est le seul outil fiable pour cette conversion)

**Validation utilisateur** : le fichier .docx s'ouvre correctement dans Word.

**Validation technique** : vérifier que `soffice` a été utilisé.

---

### D10 — Création d'un document Word depuis des données structurées

**Prompt** :
> Crée un document Word "Fiche produit" avec : un titre "Fiche Produit — Widget Pro X200", un tableau de spécifications (Poids: 1.2 kg, Dimensions: 30x20x10 cm, Couleur: Noir mat, Prix HT: 149.90€), un paragraphe de description marketing de 3 lignes.

**Méthodologie attendue** :
1. `fill_template.py` avec le template OBA `template-base.docx`
2. Config JSON avec une section contenant un bloc `table` pour les spécifications et un bloc `text` pour la description
3. Placeholders remplis : titre, auteur, date

**Validation utilisateur** : le document utilise le template OBA (page de garde, styles), le tableau est formaté avec en-têtes bleus.

**Validation technique** : vérifier que `fill_template.py` est utilisé (pas python-docx from scratch).

---

### D11 — Remplacement ciblé avec --first

**Prérequis** : uploader un DOCX contenant plusieurs fois le mot "Directeur"

**Prompt** :
> Dans ce document, seule la première occurrence de "Directeur" doit être remplacée par "Directrice" (c'est un changement de titre pour la DG uniquement). Fais-le en tracked changes.

**Méthodologie attendue** :
1. `unpack.py`
2. `tracked_replace.py --old "Directeur" --new "Directrice" --first --author "{{current_user}}"`
3. `pack.py` + `validate.py`

**Validation utilisateur** : seule la première occurrence est modifiée en tracked change.

**Validation technique** : vérifier le flag `--first` dans l'appel à `tracked_replace.py`.

---

### D12 — Pipeline complet : template → remplissage → tracked changes → PDF

**Prérequis** : uploader un template DOCX de lettre (avec placeholders ou structure reconnaissable)

**Prompt** :
> Voici notre template de lettre de mission. Remplis-le avec ces informations : consultant = "Marie Dupont", client = "Société ABC", date de début = "1er mai 2026", durée = "6 mois", tarif journalier = "850€ HT". Puis remplace "les conditions définies" par "les conditions révisées du contrat-cadre" en tracked changes. Enfin, exporte le résultat en PDF.

**Méthodologie attendue** :
1. `unpack.py` du template
2. Manipulation XML avec **lxml** pour remplir les champs (jamais `content.replace()`)
3. `pack.py unpacked/ intermediate.docx` pour une version intermédiaire
4. `unpack.py` à nouveau sur intermediate.docx
5. `tracked_replace.py --author "{{current_user}}"` pour le changement de formulation
6. `pack.py unpacked/ output.docx` + `validate.py`
7. `soffice.py --headless --convert-to pdf` pour l'export final
8. Le tout dans **un seul bloc** `execute_code`

**Validation utilisateur** : le PDF contient les bonnes informations, le DOCX intermédiaire montre les tracked changes.

**Validation technique** : vérifier la chaîne complète dans un seul code block, avec lxml (pas string replace), tracked_replace avec `{{current_user}}`, et soffice.py (pas soffice direct).

---

## Agent 2 — PowerPoint PPTX

### P01 — Création d'un pitch deck startup

**Prompt** :
> Crée un pitch deck de 8 slides pour une startup FinTech appelée "PayFlow". Slides : (1) Titre + tagline, (2) Problème, (3) Solution, (4) Marché (TAM/SAM/SOM avec chiffres), (5) Business model, (6) Traction (métriques clés), (7) Équipe (3 fondateurs), (8) Ask (levée de 2M€). Design moderne, palette bleu/blanc/gris foncé.

**Méthodologie attendue** :
1. PptxGenJS via Node.js avec `NODE_PATH=/usr/lib/node_modules` (meilleur rendu visuel pour création from scratch)
2. Utilisation de formes, couleurs, layout varié (JAMAIS la même mise en page répétée)
3. Couleurs hex sans "#" (`"2F5597"` pas `"#2F5597"`)
4. `bullet: true` pour les listes (JAMAIS de "•" unicode)
5. Chaque slide doit avoir un élément visuel (forme, chart, icône)

**Validation utilisateur** : la présentation est visuellement professionnelle, les slides sont cohérents, layouts variés.

**Validation technique** : vérifier que `pptxgenjs` est utilisé (require('pptxgenjs')), pas `python-pptx`. Vérifier qu'aucun "#" n'apparaît devant les couleurs hex.

---

### P01b — Création d'une présentation avec template OBA

**Prérequis** : aucun fichier uploadé

**Prompt** :
> Crée une présentation de 5 slides sur l'IA générative pour une réunion interne.

**Méthodologie attendue** :
1. PptxGenJS via Node.js avec la palette OBA (navy `1C244B`, blue `2F5597`, orange `FB840D`)
2. Logo OBA intégré depuis `$SKILLS_ROOT/pptx/templates/onbehalfai/logo-onbehalfai.png`
3. Slide de titre sur fond navy, slides de contenu sur fond blanc, slide de closing sur fond navy

**Validation utilisateur** : la charte OBA est respectée (couleurs navy/bleu/orange, logo, police Arial).

**Validation technique** : vérifier la palette OBA dans le code JS et l'inclusion du logo.

---

### P02 — Édition d'un template existant : remplacer du contenu

**Prérequis** : uploader un PPTX d'entreprise avec charte graphique

**Prompt** :
> Voici le template de présentation de notre entreprise. Remplace le titre du slide 1 par "Bilan annuel 2025", le sous-titre par "Direction Commerciale", et mets à jour la date en pied de page sur tous les slides à "Avril 2026". Conserve strictement la charte graphique.

**Méthodologie attendue** :
1. `unpack.py` pour décompresser le PPTX
2. Édition ciblée du XML des slides concernés
3. `clean.py` pour nettoyer
4. `pack.py` + `validate.py`

**Validation utilisateur** : la charte graphique (couleurs, polices, logos) est intacte, seuls les textes demandés ont changé.

**Validation technique** : vérifier que `unpack.py` a été utilisé (pas python-pptx qui risque de perdre des éléments visuels complexes).

---

### P03 — Analyse de template avec thumbnails

**Prérequis** : uploader un PPTX de template corporate

**Prompt** :
> Analyse ce template PowerPoint : montre-moi un aperçu visuel de tous les layouts disponibles et dis-moi quels types de slides je peux créer avec.

**Méthodologie attendue** :
1. `thumbnail.py` pour générer une grille de thumbnails (utilise `pdftoppm` via LibreOffice export PDF)
2. Analyse des slide layouts via `unpack.py` ou `python-pptx`

**Validation utilisateur** : une image avec les thumbnails est produite, la liste des layouts est claire.

**Validation technique** : vérifier que `thumbnail.py` a été utilisé.

---

### P04 — Duplication de slides

**Prérequis** : uploader un PPTX avec un slide de type "case study"

**Prompt** :
> Dans cette présentation, le slide 3 est un modèle de "case study client". Duplique-le 3 fois pour que j'aie 4 slides case study au total, que je pourrai personnaliser ensuite.

**Méthodologie attendue** :
1. `unpack.py`
2. `add_slide.py` (3 appels pour dupliquer le slide 3)
3. `pack.py` + `validate.py`

**Validation utilisateur** : la présentation contient 3 copies supplémentaires du slide, identiques à l'original.

**Validation technique** : vérifier que `add_slide.py` a été utilisé (pas une copie manuelle de fichiers XML).

---

### P05 — Nettoyage d'un PPTX volumineux

**Prérequis** : uploader un PPTX lourd (avec des médias inutilisés, slides masqués)

**Prompt** :
> Ce fichier PowerPoint fait 45 Mo, c'est trop lourd pour l'envoyer par email. Nettoie-le : supprime les slides masqués, les médias non référencés, et optimise la taille.

**Méthodologie attendue** :
1. `unpack.py`
2. `clean.py` pour supprimer les orphelins (médias, slides cachés)
3. `pack.py`

**Validation utilisateur** : le fichier est significativement plus petit, les slides visibles sont intacts.

**Validation technique** : vérifier que `clean.py` a été utilisé.

---

### P06 — Conversion PPTX vers PDF pour diffusion

**Prérequis** : uploader un PPTX

**Prompt** :
> Convertis cette présentation en PDF pour diffusion aux participants de la réunion. Le PDF doit être fidèle au rendu PowerPoint.

**Méthodologie attendue** :
1. `soffice --headless --convert-to pdf` (seule méthode fidèle)

**Validation utilisateur** : le PDF reflète fidèlement les slides.

**Validation technique** : vérifier que `soffice` est utilisé (pas une conversion via python-pptx/reportlab).

---

### P07 — Extraction du contenu en markdown

**Prérequis** : uploader un PPTX de formation

**Prompt** :
> J'ai besoin du contenu textuel de cette présentation de formation pour en faire un document écrit. Extrais tout le texte slide par slide en markdown.

**Méthodologie attendue** :
1. `markitdown` pour convertir PPTX → markdown structuré

**Validation utilisateur** : le markdown est structuré par slide avec les titres et contenus.

**Validation technique** : vérifier que `markitdown` est utilisé (pas une extraction manuelle via python-pptx).

---

### P08 — Création de slides depuis un tableau de données

**Prompt** :
> Crée une présentation de reporting mensuel avec un slide par mois (janvier à juin 2026). Chaque slide doit contenir : le nom du mois en titre, un tableau avec CA/Charges/Résultat, et une barre colorée verte si résultat positif, rouge sinon. Utilise ces données :
> Jan: 120k/95k/+25k, Fev: 110k/100k/+10k, Mar: 130k/140k/-10k, Avr: 150k/120k/+30k, Mai: 140k/135k/+5k, Jun: 160k/125k/+35k

**Méthodologie attendue** :
1. PptxGenJS via Node.js (tableaux + formes conditionnelles)

**Validation utilisateur** : 6 slides avec les bonnes données, barres vertes/rouges correctes.

**Validation technique** : vérifier l'utilisation de `pptxgenjs`.

---

### P09 — Ajout d'un slide depuis un layout spécifique

**Prérequis** : uploader un PPTX avec plusieurs layouts

**Prompt** :
> Ajoute un nouveau slide à la fin de cette présentation en utilisant le layout "Titre et contenu" (ou le 2ème layout disponible). Mets comme titre "Prochaines étapes" et comme contenu une liste : "Valider le budget", "Recruter 2 développeurs", "Lancer la V2 en juin".

**Méthodologie attendue** :
1. `unpack.py` pour identifier les layouts
2. `add_slide.py` avec le bon layout
3. Édition du XML pour insérer le texte
4. `pack.py` + `validate.py`

**Validation utilisateur** : le nouveau slide utilise bien le layout demandé avec le bon contenu.

**Validation technique** : vérifier `add_slide.py` et l'édition XML.

---

### P10 — Remplacement de texte dans toute la présentation

**Prérequis** : uploader un PPTX avec le nom d'un client partout

**Prompt** :
> Cette présentation a été faite pour le client "Acme Corp". Je dois la réutiliser pour "GlobalTech SA". Remplace toutes les occurrences de "Acme Corp" par "GlobalTech SA" dans tous les slides, y compris les masters et layouts.

**Méthodologie attendue** :
1. `unpack.py`
2. Remplacement dans tous les fichiers XML (slides, slideMasters, slideLayouts)
3. `pack.py` + `validate.py`

**Validation utilisateur** : aucune trace de "Acme Corp" dans la présentation.

**Validation technique** : vérifier que le remplacement a été fait dans les XML (pas juste via python-pptx qui ne touche pas les masters).

---

### P11 — Création from scratch avec graphiques

**Prompt** :
> Crée une mini-présentation de 3 slides avec PptxGenJS : (1) Titre "Résultats T1 2026", (2) Un graphique en barres montrant les ventes par région (Nord: 45k, Sud: 38k, Est: 52k, Ouest: 41k), (3) Un graphique circulaire de répartition des charges (Salaires: 60%, Loyer: 15%, Marketing: 20%, Divers: 5%).

**Méthodologie attendue** :
1. PptxGenJS avec `slide.addChart()` pour les graphiques

**Validation utilisateur** : les graphiques sont visibles et corrects dans PowerPoint.

**Validation technique** : vérifier `pptxgenjs` avec `addChart`.

---

### P12 — Pipeline : analyser un template → créer des slides personnalisés → exporter

**Prérequis** : uploader un PPTX corporate

**Prompt** :
> Analyse ce template corporate. Puis crée 3 nouveaux slides dans le même style : un slide "Objectifs 2026" avec 4 bullet points, un slide "Budget prévisionnel" avec un tableau 4x3, et un slide "Calendrier" avec une timeline visuelle Q1-Q4. Exporte le résultat en PDF.

**Méthodologie attendue** :
1. `thumbnail.py` ou `unpack.py` pour analyser le template
2. `add_slide.py` pour créer depuis les layouts existants OU PptxGenJS pour les slides complexes
3. `pack.py` + `validate.py`
4. `soffice --convert-to pdf`

**Validation utilisateur** : les slides sont dans le style du template, le PDF est fidèle.

**Validation technique** : vérifier la chaîne analyse → création → validation → export.

---

## Agent 3 — Excel XLSX

### X01 — Création d'un budget prévisionnel complet

**Prompt** :
> Crée un fichier Excel de budget prévisionnel annuel pour une PME avec : un onglet par trimestre (Q1 à Q4), chaque onglet ayant les postes Salaires, Loyer, Marketing, Informatique, Frais généraux. Ajoute des formules de sous-total par onglet et un 5ème onglet "Synthèse" qui consolide les 4 trimestres avec des formules inter-onglets. Formate avec des bordures, des en-têtes colorés, et le format monétaire € sur les montants.

**Méthodologie attendue** :
1. `openpyxl` pour la création (préserve les formules inter-onglets)
2. Formatage via styles openpyxl (PatternFill, Border, Font, NumberFormat)
3. `recalc.py` pour recalculer toutes les formules

**Validation utilisateur** : ouvrir dans Excel, les 5 onglets sont présents, les formules de synthèse fonctionnent, le formatage est professionnel.

**Validation technique** : vérifier `openpyxl` (pas `pandas.to_excel` qui ne gère pas les formules inter-onglets) + `recalc.py`.

---

### X02 — Analyse d'un fichier Excel existant

**Prérequis** : uploader un XLSX de données commerciales (commandes, clients, montants)

**Prompt** :
> Analyse ce fichier Excel et donne-moi : (1) le nombre de lignes et colonnes, (2) les types de données par colonne, (3) les valeurs manquantes, (4) le top 5 des clients par CA, (5) la répartition mensuelle du CA. Présente les résultats de façon structurée.

**Méthodologie attendue** :
1. `pandas` pour la lecture et l'analyse (`pd.read_excel`, `df.describe()`, `df.info()`, groupby)
2. `openpyxl` pour la lecture si formules à préserver

**Validation utilisateur** : les statistiques sont cohérentes avec le fichier source.

**Validation technique** : vérifier l'utilisation de `pandas` pour l'analyse.

---

### X03 — Recalcul de formules après modification

**Prérequis** : uploader un XLSX avec des formules

**Prompt** :
> Dans ce fichier Excel, change la valeur de la cellule B2 (prix unitaire) de 25€ à 30€. Recalcule toutes les formules qui en dépendent et confirme-moi les nouvelles valeurs du total.

**Méthodologie attendue** :
1. `openpyxl` pour modifier B2
2. `recalc.py` pour forcer le recalcul de toutes les formules via LibreOffice
3. Lecture du résultat pour confirmer les valeurs

**Validation utilisateur** : les totaux sont mis à jour correctement.

**Validation technique** : vérifier que `recalc.py` a été appelé (pas juste un calcul Python des formules).

---

### X04 — Graphique dans Excel

**Prérequis** : uploader un XLSX avec des données de ventes mensuelles

**Prompt** :
> Ajoute un graphique en barres dans ce fichier Excel montrant l'évolution des ventes mensuelles. Place le graphique dans un nouvel onglet "Dashboard".

**Méthodologie attendue** :
1. `openpyxl` pour lire les données et créer un `BarChart`
2. Ajout dans un nouveau worksheet

**Validation utilisateur** : le graphique est visible dans l'onglet Dashboard.

**Validation technique** : vérifier `openpyxl.chart.BarChart` (pas matplotlib qui produirait une image, pas un vrai graphique Excel).

---

### X05 — Conversion XLS ancien format → XLSX

**Prérequis** : uploader un fichier `.xls` (format Excel 97-2003)

**Prompt** :
> Ce fichier est dans l'ancien format .xls. Convertis-le en .xlsx moderne sans perdre les données ni les formules.

**Méthodologie attendue** :
1. `soffice --headless --convert-to xlsx` (LibreOffice)

**Validation utilisateur** : le .xlsx contient les mêmes données et formules.

**Validation technique** : vérifier que `soffice` est utilisé.

---

### X06 — Tableau croisé dynamique

**Prérequis** : uploader un XLSX de données transactionnelles

**Prompt** :
> À partir de ces données de ventes, crée un tableau croisé dynamique montrant le CA par catégorie de produit et par mois. Exporte le résultat dans un nouvel onglet "Pivot".

**Méthodologie attendue** :
1. `pandas` pour le pivot_table
2. `openpyxl` pour écrire le résultat dans un nouvel onglet avec formatage

**Validation utilisateur** : le tableau croisé est correct et formaté.

**Validation technique** : vérifier `pandas.pivot_table` + écriture via `openpyxl`.

---

### X07 — Mise en forme conditionnelle

**Prompt** :
> Crée un fichier Excel de suivi des objectifs commerciaux avec 10 vendeurs, leurs objectifs et leurs résultats. Ajoute une mise en forme conditionnelle : vert si résultat ≥ objectif, orange si entre 80% et 100%, rouge si < 80%.

**Méthodologie attendue** :
1. `openpyxl` avec `ConditionalFormatting` et `ColorScaleRule` ou `CellIsRule`

**Validation utilisateur** : les couleurs sont correctes dans Excel.

**Validation technique** : vérifier `openpyxl` conditional formatting (pas juste des couleurs en dur).

---

### X08 — Export Excel → PDF

**Prérequis** : un XLSX créé précédemment

**Prompt** :
> Exporte ce fichier Excel en PDF avec une mise en page paysage, adapté à la largeur d'une page A4.

**Méthodologie attendue** :
1. `soffice --headless --convert-to pdf` (avec options de mise en page si possible)

**Validation utilisateur** : le PDF est en paysage, le tableau tient sur la largeur.

**Validation technique** : vérifier `soffice`.

---

### X09 — Fusion de plusieurs Excel

**Prérequis** : uploader 2-3 fichiers XLSX avec la même structure

**Prompt** :
> Fusionne ces 3 fichiers Excel (rapports mensuels) en un seul fichier avec un onglet par fichier source et un onglet "Consolidé" qui additionne les valeurs.

**Méthodologie attendue** :
1. `pandas` pour lire chaque fichier
2. `openpyxl` pour écrire dans un workbook multi-onglets avec formules de consolidation
3. Optionnel : `recalc.py`

**Validation utilisateur** : 4 onglets, le consolidé est correct.

**Validation technique** : vérifier la combinaison `pandas` lecture + `openpyxl` écriture.

---

### X10 — Nettoyage et dédoublonnage

**Prérequis** : uploader un XLSX avec des doublons et des données sales

**Prompt** :
> Nettoie ce fichier Excel : supprime les lignes en doublon, normalise les noms (majuscule sur la première lettre), corrige les formats de dates incohérents, et signale les valeurs manquantes. Produis le fichier nettoyé et un rapport des modifications effectuées.

**Méthodologie attendue** :
1. `pandas` pour le nettoyage (drop_duplicates, str.title(), pd.to_datetime, isna)
2. `openpyxl` pour écrire le résultat propre

**Validation utilisateur** : le fichier est propre, le rapport liste les corrections.

**Validation technique** : vérifier `pandas` pour les transformations.

---

### X11 — Création d'un modèle financier avec formules complexes

**Prompt** :
> Crée un modèle de prévision de trésorerie sur 12 mois avec : solde initial de 50 000€, encaissements mensuels croissants de 5% à partir de 20 000€, décaissements fixes de 18 000€, et une ligne de solde cumulé. Ajoute des formules Excel natives (pas des valeurs calculées en Python). Formate les montants négatifs en rouge.

**Méthodologie attendue** :
1. `openpyxl` avec des formules Excel natives dans les cellules (pas des valeurs calculées)
2. Formatage conditionnel pour les négatifs
3. `recalc.py` pour que les valeurs soient visibles

**Validation utilisateur** : les formules sont dynamiques dans Excel, pas des valeurs statiques.

**Validation technique** : vérifier que les cellules contiennent des formules (`=B2+C3-D3`), pas des nombres, + `recalc.py`.

---

### X12 — Analyse + visualisation + export complet

**Prérequis** : uploader un gros XLSX de données RH (effectifs, salaires, départements)

**Prompt** :
> Fais une analyse complète de ce fichier RH : effectif par département, masse salariale par département, salaire moyen/médian/min/max par département. Crée un graphique Excel en barres pour la masse salariale et un graphique circulaire pour la répartition des effectifs. Mets tout dans un onglet "Dashboard RH".

**Méthodologie attendue** :
1. `pandas` pour l'analyse
2. `openpyxl` pour créer le dashboard avec les données + graphiques Excel natifs
3. Optionnel : `recalc.py`

**Validation utilisateur** : le dashboard est complet avec graphiques Excel fonctionnels.

**Validation technique** : vérifier `openpyxl.chart` pour les graphiques (pas matplotlib en images).

---

## Agent 4 — PDF

### F01 — Extraction de texte d'un contrat PDF

**Prérequis** : uploader un PDF de contrat textuel

**Prompt** :
> Extrais le texte intégral de ce contrat PDF et identifie les clauses principales : durée, montant, conditions de résiliation, pénalités. Présente-moi un résumé structuré.

**Méthodologie attendue** :
1. `pdfplumber` pour l'extraction de texte structurée (préserve mieux le layout que pypdf)
2. Analyse du texte extrait

**Validation utilisateur** : les clauses identifiées sont correctes.

**Validation technique** : vérifier `pdfplumber` (pas `pypdf` ni `pdftotext`).

---

### F02 — Extraction de tableaux depuis un PDF

**Prérequis** : uploader un PDF contenant des tableaux (facture, relevé bancaire)

**Prompt** :
> Ce PDF contient des tableaux de données. Extrais-les et convertis-les en fichier Excel exploitable.

**Méthodologie attendue** :
1. `pdfplumber` avec `page.extract_tables()` pour les tableaux
2. `pandas` DataFrame pour structurer
3. `openpyxl` ou `pandas.to_excel` pour l'export

**Validation utilisateur** : le fichier Excel contient les données des tableaux, bien structurées.

**Validation technique** : vérifier `pdfplumber.extract_tables()`.

---

### F03 — OCR d'un PDF scanné

**Prérequis** : uploader un PDF scanné (image, pas de texte extractible)

**Prompt** :
> Ce PDF est un scan. Extrais le texte par OCR et produis un fichier texte lisible.

**Méthodologie attendue** :
1. `pdf2image` pour convertir les pages en images (utilise `pdftoppm`)
2. `pytesseract.image_to_string()` sur chaque image
3. Concaténation du texte

**Validation utilisateur** : le texte extrait est lisible et correspond au document scanné.

**Validation technique** : vérifier `pdf2image` + `pytesseract` (pas juste `pdfplumber` qui retournerait du vide sur un scan).

---

### F04 — Fusion de plusieurs PDFs

**Prérequis** : uploader 3 PDFs

**Prompt** :
> Fusionne ces 3 fichiers PDF en un seul document, dans l'ordre suivant : d'abord le rapport, puis les annexes, puis les conditions générales. Ajoute un signet pour chaque partie.

**Méthodologie attendue** :
1. `pypdf.PdfMerger()` pour la fusion
2. Ajout de bookmarks/outlines

**Validation utilisateur** : le PDF fusionné contient toutes les pages dans le bon ordre.

**Validation technique** : vérifier `pypdf.PdfMerger`.

---

### F05 — Split d'un PDF par pages

**Prérequis** : uploader un PDF de 10+ pages

**Prompt** :
> Découpe ce PDF : extrais les pages 3 à 7 dans un fichier séparé "extrait.pdf".

**Méthodologie attendue** :
1. `pypdf.PdfReader` + `PdfWriter` pour extraire les pages
2. OU `qpdf --pages input.pdf 3-7 -- output.pdf`

**Validation utilisateur** : le PDF extrait contient exactement les pages 3 à 7.

**Validation technique** : vérifier `pypdf` ou `qpdf --pages`.

---

### F06 — Vérification d'intégrité et réparation

**Prérequis** : uploader un PDF potentiellement corrompu

**Prompt** :
> Vérifie l'intégrité de ce fichier PDF. S'il y a des problèmes, essaie de le réparer.

**Méthodologie attendue** :
1. `qpdf --check` pour diagnostiquer
2. `qpdf --replace-input` ou `qpdf input.pdf output.pdf` pour réparer

**Validation utilisateur** : le rapport d'intégrité est clair, le fichier réparé s'ouvre correctement.

**Validation technique** : vérifier que `qpdf` est utilisé (pas juste pypdf).

---

### F07 — Conversion PDF → images haute résolution

**Prérequis** : uploader un PDF

**Prompt** :
> Convertis chaque page de ce PDF en image PNG haute résolution (300 DPI) pour intégration dans un document Word.

**Méthodologie attendue** :
1. `pdf2image.convert_from_path(dpi=300)` (utilise `pdftoppm` en backend)
2. Sauvegarde en PNG

**Validation utilisateur** : les images sont nettes et en haute résolution.

**Validation technique** : vérifier `pdf2image` avec `dpi=300`.

---

### F08 — Extraction de métadonnées

**Prérequis** : uploader un PDF

**Prompt** :
> Donne-moi toutes les métadonnées de ce PDF : auteur, date de création, date de modification, producteur, nombre de pages, taille, version PDF, et s'il est chiffré ou non.

**Méthodologie attendue** :
1. `pypdf.PdfReader` pour les métadonnées PDF
2. `qpdf --show-npages` et `qpdf --is-encrypted`

**Validation utilisateur** : les métadonnées sont correctes.

**Validation technique** : vérifier l'utilisation combinée de `pypdf` et `qpdf`.

---

### F09 — Rotation et réorganisation de pages

**Prérequis** : uploader un PDF dont certaines pages sont à l'envers

**Prompt** :
> Les pages 2 et 5 de ce PDF sont à l'envers (rotation 180°). Corrige-les.

**Méthodologie attendue** :
1. `pypdf` : `page.rotate(180)` sur les pages concernées
2. Écriture du PDF corrigé

**Validation utilisateur** : les pages sont dans le bon sens.

**Validation technique** : vérifier `pypdf` avec `rotate()`.

---

### F10 — Ajout de watermark

**Prompt** :
> Ajoute un watermark "BROUILLON" en diagonale sur toutes les pages de ce PDF, en texte gris semi-transparent.

**Méthodologie attendue** :
1. `reportlab` pour créer le watermark (Canvas avec rotation et transparence)
2. `pypdf` pour fusionner le watermark sur chaque page

**Validation utilisateur** : le watermark est visible mais n'empêche pas la lecture.

**Validation technique** : vérifier `reportlab` + `pypdf.merge_page`.

---

### F11 — Compression / optimisation d'un PDF lourd

**Prérequis** : uploader un PDF volumineux

**Prompt** :
> Ce PDF fait 25 Mo, c'est trop lourd pour l'envoyer par email. Optimise-le pour réduire sa taille.

**Méthodologie attendue** :
1. `qpdf --linearize --compress-streams=y` ou `qpdf --recompress-flate`

**Validation utilisateur** : le fichier est plus petit, le contenu est préservé.

**Validation technique** : vérifier que `qpdf` est utilisé pour la compression.

---

### F12 — Pipeline : OCR → extraction tableaux → Excel

**Prérequis** : uploader un PDF scanné contenant des tableaux (par ex. relevé bancaire scanné)

**Prompt** :
> Ce relevé bancaire est un PDF scanné. Extrais les données du tableau de transactions (date, libellé, débit, crédit) et produis un fichier Excel structuré.

**Méthodologie attendue** :
1. `pdf2image` pour convertir en images
2. `pytesseract` pour l'OCR
3. Parsing du texte OCR pour identifier les colonnes du tableau
4. `pandas` DataFrame pour structurer
5. Export en XLSX

**Validation utilisateur** : le fichier Excel contient les transactions avec les bons montants.

**Validation technique** : vérifier la chaîne complète `pdf2image` → `pytesseract` → `pandas` → export.

---

### F13 — Création d'un PDF professionnel via template DOCX OBA

**Prérequis** : aucun fichier uploadé

**Prompt** :
> Crée un PDF professionnel "Proposition commerciale" avec une page de garde, 3 sections (Contexte, Offre de service, Conditions), et des listes à puces.

**Méthodologie attendue** :
1. `fill_template.py` avec le template OBA DOCX `template-base.docx` pour créer un DOCX intermédiaire
2. `soffice.py --headless --convert-to pdf` pour convertir en PDF
3. Le tout dans un seul `execute_code`

**Validation utilisateur** : le PDF a la charte OBA (page de garde, titres numérotés, polices Arial).

**Validation technique** : vérifier que `fill_template.py` + `soffice.py` sont utilisés (pas `reportlab` from scratch).

---

## Agent 5 — Quick Edits (FFmpeg)

### M01 — Conversion vidéo MP4

**Prérequis** : uploader une vidéo .mov ou .avi

**Prompt** :
> Convertis cette vidéo en MP4 compatible web (H.264 + AAC). Réduis la résolution à 720p si elle est plus grande.

**Méthodologie attendue** :
1. `ffprobe` pour analyser la source
2. `ffmpeg -i input -c:v libx264 -c:a aac -vf scale=-2:720` si nécessaire

**Validation utilisateur** : le MP4 se lit dans un navigateur.

**Validation technique** : vérifier `ffprobe` puis `ffmpeg` avec les bons codecs.

---

### M02 — Extraction de l'audio d'une vidéo

**Prérequis** : uploader une vidéo

**Prompt** :
> Extrais uniquement la piste audio de cette vidéo en MP3 à 192 kbps.

**Méthodologie attendue** :
1. `ffmpeg -i input.mp4 -vn -c:a libmp3lame -b:a 192k output.mp3`

**Validation utilisateur** : le fichier MP3 contient l'audio de la vidéo.

**Validation technique** : vérifier `ffmpeg` avec `-vn` et `-c:a libmp3lame`.

---

### M03 — Découpe d'une vidéo

**Prérequis** : uploader une vidéo

**Prompt** :
> Découpe cette vidéo pour garder uniquement la partie de 0:45 à 2:30.

**Méthodologie attendue** :
1. `ffmpeg -i input -ss 00:00:45 -to 00:02:30 -c copy output.mp4`

**Validation utilisateur** : la vidéo fait ~1:45.

**Validation technique** : vérifier `-ss` et `-to` avec `-c copy` (pas de réencodage).

---

### M04 — Création d'un GIF animé

**Prérequis** : uploader une courte vidéo

**Prompt** :
> Crée un GIF animé à partir des 5 premières secondes de cette vidéo, en 320px de large, 10 fps.

**Méthodologie attendue** :
1. `ffmpeg -i input -t 5 -vf "fps=10,scale=320:-1:flags=lanczos" -loop 0 output.gif`

**Validation utilisateur** : le GIF est fluide et à la bonne taille.

**Validation technique** : vérifier les filtres `fps`, `scale`, `-t 5`.

---

### M05 — Analyse complète d'un fichier média

**Prérequis** : uploader une vidéo ou un audio

**Prompt** :
> Donne-moi une fiche technique complète de ce fichier : format, codec vidéo et audio, résolution, framerate, bitrate, durée, taille, et nombre de pistes.

**Méthodologie attendue** :
1. `ffprobe -v quiet -print_format json -show_format -show_streams`

**Validation utilisateur** : les informations sont complètes et correctes.

**Validation technique** : vérifier `ffprobe` avec output JSON.

---

### M06 — Redimensionnement et optimisation d'image

**Prérequis** : uploader une image haute résolution

**Prompt** :
> Redimensionne cette image à 800px de large (en gardant les proportions), optimise-la pour le web (JPEG qualité 85%), et dis-moi le gain de taille.

**Méthodologie attendue** :
1. `Pillow` : `Image.open()`, `.resize()`, `.save(quality=85)`

**Validation utilisateur** : l'image est plus petite, la qualité est acceptable.

**Validation technique** : vérifier `PIL/Pillow` (pas `ffmpeg` pour de l'image simple).

---

### M07 — Ajout de texte sur une image

**Prérequis** : uploader une image

**Prompt** :
> Ajoute en bas de cette image un bandeau noir semi-transparent avec le texte "© onbehalf.ai 2026" en blanc, centré.

**Méthodologie attendue** :
1. `Pillow` avec `ImageDraw`, `ImageFont`, et blending pour la transparence

**Validation utilisateur** : le bandeau est visible et le texte est lisible.

**Validation technique** : vérifier `PIL.ImageDraw`.

---

### M08 — Concaténation de vidéos

**Prérequis** : uploader 2-3 courtes vidéos

**Prompt** :
> Assemble ces vidéos bout à bout dans l'ordre dans lequel je les ai uploadées.

**Méthodologie attendue** :
1. Création d'un fichier de liste (`concat_list.txt`)
2. `ffmpeg -f concat -safe 0 -i concat_list.txt -c copy output.mp4`

**Validation utilisateur** : la vidéo résultante contient toutes les parties.

**Validation technique** : vérifier `-f concat` et `-c copy`.

---

### M09 — Ajout de musique de fond à une vidéo

**Prérequis** : uploader une vidéo et un fichier audio

**Prompt** :
> Ajoute cette musique en fond sonore à la vidéo, avec le volume de la musique réduit à 20% par rapport à l'audio original.

**Méthodologie attendue** :
1. `ffmpeg -i video -i music -filter_complex "[1:a]volume=0.2[bg];[0:a][bg]amix=inputs=2:duration=first[a]" -map 0:v -map "[a]"`

**Validation utilisateur** : la vidéo a les deux pistes audio mixées.

**Validation technique** : vérifier `amix` ou `amerge` dans le filtre complex.

---

### M10 — Création d'une mosaïque d'images

**Prérequis** : uploader 4 images

**Prompt** :
> Crée une mosaïque 2x2 à partir de ces 4 images, en les redimensionnant toutes à la même taille.

**Méthodologie attendue** :
1. `Pillow` pour redimensionner et assembler (`.paste()`)
2. OU `ffmpeg -i 1 -i 2 -i 3 -i 4 -filter_complex "xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0"`

**Validation utilisateur** : la mosaïque est régulière avec les 4 images.

**Validation technique** : vérifier `Pillow.paste()` ou `ffmpeg xstack`.

---

### M11 — Extraction d'une frame spécifique d'une vidéo

**Prérequis** : uploader une vidéo

**Prompt** :
> Extrais une capture d'écran de cette vidéo à exactement 1 minute et 23 secondes, en PNG pleine résolution.

**Méthodologie attendue** :
1. `ffmpeg -i input -ss 00:01:23 -frames:v 1 output.png`

**Validation utilisateur** : l'image correspond au bon moment de la vidéo.

**Validation technique** : vérifier `-ss` et `-frames:v 1`.

---

### M12 — Conversion batch d'images

**Prérequis** : uploader plusieurs images dans différents formats

**Prompt** :
> Convertis toutes ces images en JPEG, 1024px de large maximum (sans agrandir les plus petites), qualité 90%. Renomme-les photo_01.jpg, photo_02.jpg, etc.

**Méthodologie attendue** :
1. `Pillow` en boucle : `Image.open()`, `.thumbnail((1024, 9999))`, `.convert("RGB")`, `.save(quality=90)`

**Validation utilisateur** : toutes les images sont en JPEG, bien nommées, taille cohérente.

**Validation technique** : vérifier `Pillow.thumbnail()` (pas `resize`, qui agrandirait les petites).

---

## Agent 6 — Data Analysis & Visualization

### A01 — Analyse exploratoire d'un CSV commercial

**Prérequis** : uploader un CSV de données commerciales

**Prompt** :
> Fais une analyse exploratoire complète de ce fichier de ventes : aperçu des données, statistiques descriptives, valeurs manquantes, distribution des variables numériques, et les 3 insights les plus importants.

**Méthodologie attendue** :
1. `pandas` : `read_csv`, `info()`, `describe()`, `isna().sum()`, `value_counts()`
2. `matplotlib`/`seaborn` pour les distributions (histogrammes, boxplots)
3. Sauvegarde des graphiques en PNG

**Validation utilisateur** : les statistiques sont cohérentes, les graphiques sont lisibles.

**Validation technique** : vérifier `pandas` + `matplotlib.use("Agg")` + `plt.savefig()`.

---

### A02 — Dashboard de 4 graphiques

**Prérequis** : un CSV de données déjà chargé (ou demander la génération)

**Prompt** :
> Crée un dashboard en une seule image avec 4 graphiques : (1) évolution du CA mensuel en courbe, (2) répartition par catégorie en camembert, (3) top 10 clients en barres horizontales, (4) nuage de points quantité vs montant avec ligne de tendance.

**Méthodologie attendue** :
1. `pandas` pour les agrégations
2. `matplotlib` avec `plt.subplots(2, 2, figsize=(14, 10))` et palette OBA (`#2F5597`, `#5B9AD4`, `#FB840D`, `#FCA810`, `#1C244B`, `#DAE5EF`)
3. `seaborn` avec `sns.set_palette()` OBA
4. `scipy.stats.linregress` pour la ligne de tendance
5. `plt.savefig("/mnt/data/dashboard.png", dpi=150, bbox_inches="tight")`

**Validation utilisateur** : les 4 graphiques sont dans une seule image, lisibles, esthétiques, aux couleurs OBA.

**Validation technique** : vérifier `subplots(2,2)`, backend `Agg`, palette OBA, et `savefig`.

---

### A03 — Test statistique ANOVA

**Prérequis** : données avec une variable catégorielle et une variable numérique

**Prompt** :
> J'ai des données de satisfaction client par canal (boutique, web, téléphone). Fais un test ANOVA pour savoir si la satisfaction moyenne diffère significativement entre les canaux. Donne-moi le F-stat, la p-value, et un graphique de comparaison.

**Méthodologie attendue** :
1. `pandas` pour grouper
2. `scipy.stats.f_oneway` pour l'ANOVA
3. `seaborn.boxplot` pour la visualisation

**Validation utilisateur** : la conclusion statistique est correcte (significatif ou non selon p-value).

**Validation technique** : vérifier `scipy.stats.f_oneway` (pas un calcul manuel).

---

### A04 — Corrélation et heatmap

**Prérequis** : un dataset avec plusieurs variables numériques

**Prompt** :
> Calcule la matrice de corrélation entre toutes les variables numériques de ce dataset et affiche-la en heatmap annotée. Identifie les corrélations fortes (> 0.7 ou < -0.7).

**Méthodologie attendue** :
1. `pandas` : `df.corr()`
2. `seaborn.heatmap` avec `annot=True`, `cmap="RdBu_r"`, `vmin=-1, vmax=1`

**Validation utilisateur** : la heatmap est lisible, les corrélations fortes sont identifiées.

**Validation technique** : vérifier `seaborn.heatmap` avec annotations.

---

### A05 — Régression linéaire avec prédiction

**Prompt** :
> Génère un dataset de 200 points avec une relation linéaire bruitée entre "surface_m2" (30-150) et "prix_k_euros" (relation ~3.5k€/m² + bruit). Entraîne un modèle de régression linéaire, affiche le R², l'équation, et un graphique avec les points, la droite de régression et l'intervalle de confiance à 95%.

**Méthodologie attendue** :
1. `numpy` pour la génération
2. `sklearn.linear_model.LinearRegression` pour le modèle
3. `matplotlib` pour le graphique avec intervalle de confiance
4. `scipy.stats` pour l'intervalle de confiance

**Validation utilisateur** : le graphique montre la régression avec l'intervalle, le R² est cohérent.

**Validation technique** : vérifier `sklearn.LinearRegression` + `matplotlib`.

---

### A06 — Clustering K-Means

**Prérequis** : un CSV avec des données client (âge, revenu, dépenses)

**Prompt** :
> Segmente ces clients en clusters avec K-Means. Détermine le nombre optimal de clusters avec la méthode du coude, puis visualise les segments en 2D.

**Méthodologie attendue** :
1. `sklearn.preprocessing.StandardScaler` pour normaliser
2. `sklearn.cluster.KMeans` avec boucle pour le coude (inertia)
3. `sklearn.decomposition.PCA` pour la visualisation 2D
4. `matplotlib` pour le graphique du coude et le scatter plot coloré

**Validation utilisateur** : le graphique du coude montre un point d'inflexion, les clusters sont visuellement distincts.

**Validation technique** : vérifier `KMeans` + `PCA` + scaler.

---

### A07 — Analyse de séries temporelles

**Prérequis** : un CSV avec une colonne date et une colonne valeur (ex: CA quotidien)

**Prompt** :
> Analyse cette série temporelle : identifie la tendance, la saisonnalité, et fais une décomposition. Génère un graphique de décomposition et une prévision naïve sur les 30 prochains jours.

**Méthodologie attendue** :
1. `pandas` pour le resampling et le rolling mean
2. `statsmodels.tsa.seasonal.seasonal_decompose`
3. `matplotlib` pour la décomposition (4 subplots : observed, trend, seasonal, resid)

**Validation utilisateur** : la décomposition est visuellement claire, la tendance est identifiable.

**Validation technique** : vérifier `statsmodels.seasonal_decompose`.

---

### A08 — Export multi-onglets vers Excel

**Prérequis** : un dataset analysé

**Prompt** :
> Exporte cette analyse dans un fichier Excel avec 3 onglets : "Données brutes", "Statistiques" (describe par catégorie), et "Tableau croisé" (pivot du CA par mois et catégorie).

**Méthodologie attendue** :
1. `pandas` pour les calculs
2. `openpyxl` via `pd.ExcelWriter` avec `engine='openpyxl'`
3. Écriture dans 3 sheets

**Validation utilisateur** : le fichier Excel a 3 onglets avec les bonnes données.

**Validation technique** : vérifier `pd.ExcelWriter` avec `openpyxl`.

---

### A09 — Détection d'anomalies

**Prérequis** : un dataset de transactions

**Prompt** :
> Analyse ces transactions et identifie les anomalies (valeurs aberrantes). Utilise la méthode IQR et le Z-score. Visualise les outliers sur un graphique et liste-les.

**Méthodologie attendue** :
1. `pandas` + `numpy` pour IQR et Z-score
2. `scipy.stats.zscore`
3. `matplotlib`/`seaborn` pour le boxplot avec outliers marqués

**Validation utilisateur** : les outliers identifiés sont cohérents.

**Validation technique** : vérifier `scipy.stats.zscore` ou calcul IQR.

---

### A10 — Comparaison avant/après avec test t

**Prompt** :
> Génère deux échantillons simulés : "avant" (n=50, μ=72, σ=12) et "après" (n=50, μ=78, σ=11) représentant des scores de satisfaction avant/après une formation. Fais un test t de Student apparié, donne la conclusion, et visualise les deux distributions.

**Méthodologie attendue** :
1. `numpy.random.normal` pour la génération
2. `scipy.stats.ttest_rel` pour le test apparié
3. `seaborn` pour les distributions superposées (kdeplot ou histplot)

**Validation utilisateur** : la conclusion du test est claire, le graphique montre le décalage.

**Validation technique** : vérifier `scipy.stats.ttest_rel` (pas `ttest_ind`).

---

### A11 — Word cloud à partir de texte

**Prompt** :
> Génère un nuage de mots à partir du texte suivant (ou d'un fichier texte uploadé) : [coller un paragraphe de 200 mots sur un thème business]. Exclue les mots vides français. Utilise une palette de couleurs bleue.

**Méthodologie attendue** :
1. `wordcloud.WordCloud` avec stopwords français
2. `matplotlib` pour le rendu

**Validation utilisateur** : le word cloud est lisible, les mots pertinents ressortent.

**Validation technique** : vérifier `wordcloud` (le package est installé dans l'image).

---

### A12 — Pipeline complet : chargement → nettoyage → analyse → viz → export

**Prérequis** : uploader un CSV brut (avec des données sales)

**Prompt** :
> Fais un traitement complet de ce fichier : (1) charge et identifie les problèmes de qualité, (2) nettoie les données (doublons, manquants, types), (3) fais une analyse descriptive, (4) crée 3 visualisations pertinentes, (5) exporte un rapport Excel avec les données nettoyées, les stats, et les graphiques en images intégrées.

**Méthodologie attendue** :
1. `pandas` pour tout le pipeline data
2. `matplotlib`/`seaborn` pour les visualisations
3. `openpyxl` pour l'export Excel avec images intégrées (`ws.add_image`)

**Validation utilisateur** : le fichier Excel est un rapport complet et autonome.

**Validation technique** : vérifier la chaîne `pandas` → `matplotlib.savefig` → `openpyxl.add_image`.
