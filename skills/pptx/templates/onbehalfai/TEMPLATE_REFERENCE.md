# Template OBA Corporate — Référence complète

## Fichier : `template-oba-corporate.pptx`

Template PowerPoint professionnel On Behalf AI avec 50 layouts, thème corporate et 1 slide master.

## Propriétés

- **Dimensions** : 13.33 × 7.50 pouces (format widescreen 16:9)
- **Police majeure** : Poppins Medium (titres)
- **Police mineure** : Poppins Light (corps)
- **1 slide master** : `slideMaster1.xml`
- **50 slide layouts** disponibles
- **1 slide existant** : slide1 (vierge, à supprimer lors de la création)

## Thème couleurs "On Behalf AI"

| Rôle | Couleur | Hex |
|------|---------|-----|
| dk1 (texte principal) | Navy | #1C244B |
| lt1 (fond principal) | Blanc | #FFFFFF |
| dk2 (texte secondaire) | Gris bleu | #44546A |
| lt2 (fond secondaire) | Gris très clair | #F3F5F8 |
| accent1 | Bleu | #2F5597 |
| accent2 | Bleu clair | #DAE5EF |
| accent3 | Bleu ciel | #5B9AD4 |
| accent4 | Orange | #FB840D |
| accent5 | Ambre | #FCA810 |
| accent6 | Or | #FEB501 |
| hlink | Lien | #0563C1 |

## Catalogue des layouts

### Layouts recommandés par usage

#### Pour un deck type (5-10 slides)

| Usage | Layout recommandé | Fichier |
|-------|-------------------|---------|
| Slide de titre | Title | slideLayout1.xml |
| Titre + description | Title + text | slideLayout2.xml |
| Titre + image | Title + image | slideLayout3.xml |
| Section divider | Section title - dark blue | slideLayout38.xml |
| Contenu (bullets) | Title + Content #1 | slideLayout7.xml |
| Contenu + sous-titre | Title + Subtitle + Content #1 | slideLayout6.xml |
| 2 colonnes | Title + 2 Content #1 | slideLayout21.xml |
| 3 colonnes | Title + 3 Content #1 | slideLayout27.xml |
| Contenu + image | Title + Content + Image #1 | slideLayout19.xml |
| Contenu + tableau | Title + Content + Table #1 | slideLayout23.xml |
| Graphique | Title + Chart #1 | slideLayout13.xml |
| Citation | Quote | slideLayout43.xml |
| Équipe (4 membres) | Team | slideLayout44.xml |
| Équipe (8 membres) | Whole team | slideLayout47.xml |
| Agenda | Agenda | slideLayout5.xml |
| Slide de fin | End - Thank you #2 | slideLayout49.xml |

#### Section dividers (5 variantes de couleur)

| Layout | Fichier | Couleur fond |
|--------|---------|-------------|
| Section title - dark blue | slideLayout38.xml | Navy (#1C244B) |
| Section title - light blue | slideLayout39.xml | Bleu clair |
| Section title - light grey blue | slideLayout40.xml | Gris bleu |
| Section title - orange | slideLayout41.xml | Orange |
| Section title - light orange | slideLayout42.xml | Orange clair |

#### Variantes de contenu (avec/sans sous-titre)

Chaque layout de contenu existe en 2 versions :
- **Sans sous-titre** : Titre + zone de contenu directement
- **Avec sous-titre** : Titre + ligne de sous-titre + zone de contenu

Les variantes #1 à #6 ont des styles graphiques différents (décorations, fond, position du titre).

## Positions des placeholders (en pouces)

### Zone de titre
- Titre centré (ctrTitle) : x=1.3, y=0.2-2.0, w=6.8-8.5"
- Titre standard (title) : en haut du slide, pleine largeur

### Zone de sous-titre
- subTitle : x=1.3, y=1.2, w=11.1, h=0.7"

### Zone de contenu principal
- body : x=1.3, y=1.9, w=11.1, h=4.9" (pleine largeur)
- body (2 colonnes) : gauche x=1.3 w=5.4", droite x=7.0 w=5.4"
- body (3 colonnes) : x=1.3/5.1/8.9, w=3.5" chacune
- body (contenu + image) : contenu x=1.3 w=7.3", image x=8.7 w=3.7"

### Date et numéro de slide
- dt (date) : toujours présent, petit placeholder
- sldNum : numéro de slide, toujours présent

## Comment utiliser ce template

### Pipeline de création (unpack → create → pack)

```python
import subprocess, shutil, os
os.chdir('/mnt/data')

# 1. Copier le template
shutil.copy('/opt/skills/pptx/templates/onbehalfai/template-oba-corporate.pptx', 'presentation.pptx')

# 2. Unpack
subprocess.run(["python3", "/opt/skills/pptx/scripts/office/unpack.py", 
    "presentation.pptx", "unpacked/"], check=True)

# 3. Supprimer le slide vide existant (slide1.xml)
# Modifier ppt/presentation.xml pour retirer le sldId de slide1
# OU garder slide1 et le modifier

# 4. Ajouter des slides depuis les layouts
# add_slide.py crée un nouveau slide à partir d'un layout
subprocess.run(["python3", "/opt/skills/pptx/scripts/add_slide.py",
    "unpacked/", "slideLayout7.xml"], check=True)  # Title + Content

# 5. Éditer le contenu des slides (XML)
# Chaque slide a des placeholders avec des idx spécifiques
# Remplir les <a:t> elements dans chaque placeholder

# 6. Clean + Pack
subprocess.run(["python3", "/opt/skills/pptx/scripts/clean.py", "unpacked/"], check=True)
subprocess.run(["python3", "/opt/skills/pptx/scripts/office/pack.py",
    "unpacked/", "presentation.pptx"], check=True)
```

### Choix du layout selon le contenu

```
Si titre seul → slideLayout1.xml (Title)
Si titre + sous-titre → slideLayout2.xml (Title + text)
Si titre + image → slideLayout3.xml (Title + image)
Si section divider → slideLayout38-42.xml (choisir la couleur)
Si contenu texte/bullets → slideLayout7.xml (Title + Content #1)
Si contenu + sous-titre → slideLayout6.xml (Title + Subtitle + Content #1)
Si 2 colonnes → slideLayout21.xml (Title + 2 Content #1)
Si 3 colonnes → slideLayout27.xml (Title + 3 Content #1)
Si contenu + image côte à côte → slideLayout19.xml
Si contenu + tableau → slideLayout23.xml
Si graphique pleine page → slideLayout13.xml
Si tableau plein → slideLayout15.xml
Si agenda → slideLayout5.xml
Si citation → slideLayout43.xml
Si équipe 4 personnes → slideLayout44.xml
Si équipe 8 personnes → slideLayout47.xml
Si slide de fin → slideLayout49.xml
Si fond bleu + contenu → slideLayout36.xml
```

## Images embarquées dans le template

Le template contient des images décoratives utilisées par le slide master et les layouts :
- Logo OBA : `image2.png` (87 KB), `image3.svg` (10 KB)
- Éléments graphiques : vagues, formes décoratives
- Image de fond : `image13.png` (523 KB), `image14.svg` (367 KB)

Ces images sont automatiquement héritées par les slides créés depuis les layouts.
