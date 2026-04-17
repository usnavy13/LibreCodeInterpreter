# Guide de gestion des logos dans les templates DOCX

## Leçons apprises

### Word utilise le SVG en priorité sur le PNG

Dans un DOCX, un logo peut être référencé avec deux formats simultanément :
- **PNG** : via `<a:blip r:embed="rIdX"/>` — fallback pour les anciens Word
- **SVG** : via `<asvg:svgBlip r:embed="rIdY"/>` dans `<a:extLst>` — prioritaire dans Word moderne

**Conséquence** : si on remplace le PNG mais pas le SVG, l'ancien logo reste visible.

### Structure XML d'une image dans un DOCX

```xml
<w:drawing>
  <wp:inline distT="0" distB="0" distL="0" distR="0">
    <!-- Taille d'affichage (ce qui compte pour le rendu) -->
    <wp:extent cx="1647542" cy="378745"/>
    
    <!-- Marges d'effet (espace autour de l'image) -->
    <wp:effectExtent l="0" t="0" r="0" b="2540"/>
    
    <!-- ID et nom de l'objet -->
    <wp:docPr id="1001" name="Graphic 3"/>
    
    <a:graphic>
      <a:graphicData>
        <pic:pic>
          <pic:blipFill>
            <a:blip r:embed="rId12">  <!-- PNG -->
              <a:extLst>
                <a:ext uri="{...SVG_URI...}">
                  <asvg:svgBlip r:embed="rId13"/>  <!-- SVG prioritaire -->
                </a:ext>
              </a:extLst>
            </a:blip>
          </pic:blipFill>
          <pic:spPr>
            <a:xfrm>
              <a:off x="0" y="0"/>
              <!-- Taille de transformation (légèrement plus grand que extent) -->
              <a:ext cx="1699459" cy="390680"/>
            </a:xfrm>
          </pic:spPr>
        </pic:pic>
      </a:graphicData>
    </a:graphic>
  </wp:inline>
</w:drawing>
```

### Les 3 dimensions à gérer

| Élément | Rôle | Relation |
|---------|------|----------|
| `wp:extent` | Taille d'affichage dans le document | **Taille visible** — c'est celle-ci qui compte |
| `a:xfrm/a:ext` | Taille de transformation du graphique | Légèrement plus grand que wp:extent (~3%) |
| `a:ext` dans `a:extLst` | Dimensions hardcodées des extensions SVG | **Ne pas renseigner** — laisser vide ou retirer cx/cy |

**Ratio entre wp:extent et a:xfrm** : le xfrm est ~3% plus grand que l'extent.
- `xfrm.cx ≈ extent.cx × 1.032`
- `xfrm.cy ≈ extent.cy × 1.031`

### Comment dimensionner un logo

1. **Mesurer la cellule** qui contient le logo :
   ```python
   # Trouver la largeur de la cellule dans le XML
   tcPr = cell.find(f"{{{ns}}}tcPr")
   tcW = tcPr.find(f"{{{ns}}}tcW")
   cell_width_dxa = int(tcW.get(f"{{{ns}}}w"))  # en DXA (1440 DXA = 1 inch)
   cell_width_emu = cell_width_dxa * 635         # conversion DXA → EMU
   ```

2. **Calculer les dimensions du logo** :
   ```python
   # Ratio du logo (largeur / hauteur)
   logo_ratio = logo_width_px / logo_height_px  # ex: 943/217 = 4.35
   
   # Viser ~50% de la largeur de la cellule pour laisser de la marge
   target_width_emu = int(cell_width_emu * 0.50)
   target_height_emu = int(target_width_emu / logo_ratio)
   
   # xfrm = extent × 1.032
   xfrm_width = int(target_width_emu * 1.032)
   xfrm_height = int(target_height_emu * 1.032)
   ```

3. **Valeurs de référence pour le template CR On Behalf AI** :
   - Cellule logo : 5040 DXA = 3.50 pouces
   - Logo OBA horizontal : 943×217 px (ratio 4.35:1)
   - wp:extent : **1647542 × 378745** EMU (1.80 × 0.41 pouces)
   - a:xfrm : **1699459 × 390680** EMU (1.86 × 0.43 pouces)

## Ajout d'un logo tiers (client)

### Règle métier (définie dans les instructions agent)

- Si CR avec une tierce partie identifiable → logo client + logo OBA
- Si pas de logo client disponible → logo OBA seul
- Si réunion interne ou trop de parties → logo OBA seul

### Procédure technique pour ajouter un logo client

Le template CR a une cellule de 5.04 pouces (colonne droite, row 0). Actuellement le logo OBA y est seul. Pour ajouter un logo client :

**Option A — Deux logos côte à côte dans la même cellule** :

1. Réduire le logo OBA à ~1.2 pouces de large
2. Ajouter le logo client à ~1.2 pouces de large
3. Les séparer par un espace (paragraphe vide ou tab)

```python
# Dimensions pour 2 logos côte à côte dans une cellule de 3.5"
# Chaque logo : ~1.2" de large, avec ~0.3" d'espace entre
LOGO_WIDTH_EMU = int(1.2 * 914400)  # 1,097,280 EMU
# Hauteur calculée selon le ratio de chaque logo
```

**Option B — Logo client au-dessus, logo OBA en dessous** :

1. Deux paragraphes dans la même cellule
2. Chaque paragraphe contient un logo
3. Largeur max ~2.5 pouces chacun

### Code Python pour ajouter un logo au XML

```python
from lxml import etree
import zipfile

ns_w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ns_wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
ns_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ns_pic = "http://schemas.openxmlformats.org/drawingml/2006/picture"

def add_image_to_docx(unpacked_dir, image_path, image_name, width_emu, height_emu):
    """
    Ajoute une image au DOCX décompressé.
    
    1. Copier l'image dans word/media/
    2. Ajouter la relation dans word/_rels/document.xml.rels
    3. Ajouter le Content-Type si nécessaire
    4. Retourner le rId pour référencer dans le XML
    """
    import shutil, os
    
    # 1. Copier l'image
    media_dir = os.path.join(unpacked_dir, "word", "media")
    os.makedirs(media_dir, exist_ok=True)
    dest = os.path.join(media_dir, image_name)
    shutil.copy(image_path, dest)
    
    # 2. Ajouter la relation
    rels_path = os.path.join(unpacked_dir, "word", "_rels", "document.xml.rels")
    rels_tree = etree.parse(rels_path)
    rels_root = rels_tree.getroot()
    
    # Trouver le prochain rId
    existing_ids = [int(r.get("Id").replace("rId", "")) 
                    for r in rels_root if r.get("Id", "").startswith("rId")]
    new_id = f"rId{max(existing_ids) + 1}"
    
    # Déterminer le type de relation selon l'extension
    ext = os.path.splitext(image_name)[1].lower()
    rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    
    new_rel = etree.SubElement(rels_root, "Relationship")
    new_rel.set("Id", new_id)
    new_rel.set("Type", rel_type)
    new_rel.set("Target", f"media/{image_name}")
    
    rels_tree.write(rels_path, xml_declaration=True, encoding="UTF-8", standalone=True)
    
    return new_id


def create_inline_image(rId, width_emu, height_emu, doc_pr_id, name="Image"):
    """
    Crée un élément <w:drawing><wp:inline>...</wp:inline></w:drawing>
    pour insérer dans un paragraphe.
    """
    xfrm_cx = int(width_emu * 1.032)
    xfrm_cy = int(height_emu * 1.032)
    
    drawing = etree.Element(f"{{{ns_w}}}drawing")
    inline = etree.SubElement(drawing, f"{{{ns_wp}}}inline")
    inline.set("distT", "0")
    inline.set("distB", "0") 
    inline.set("distL", "0")
    inline.set("distR", "0")
    
    extent = etree.SubElement(inline, f"{{{ns_wp}}}extent")
    extent.set("cx", str(width_emu))
    extent.set("cy", str(height_emu))
    
    effect = etree.SubElement(inline, f"{{{ns_wp}}}effectExtent")
    effect.set("l", "0")
    effect.set("t", "0")
    effect.set("r", "0")
    effect.set("b", "2540")
    
    docPr = etree.SubElement(inline, f"{{{ns_wp}}}docPr")
    docPr.set("id", str(doc_pr_id))
    docPr.set("name", name)
    
    graphic = etree.SubElement(inline, f"{{{ns_a}}}graphic")
    graphicData = etree.SubElement(graphic, f"{{{ns_a}}}graphicData")
    graphicData.set("uri", "http://schemas.openxmlformats.org/drawingml/2006/picture")
    
    pic = etree.SubElement(graphicData, f"{{{ns_pic}}}pic")
    
    nvPicPr = etree.SubElement(pic, f"{{{ns_pic}}}nvPicPr")
    cNvPr = etree.SubElement(nvPicPr, f"{{{ns_pic}}}cNvPr")
    cNvPr.set("id", "0")
    cNvPr.set("name", name)
    cNvPicPr = etree.SubElement(nvPicPr, f"{{{ns_pic}}}cNvPicPr")
    
    blipFill = etree.SubElement(pic, f"{{{ns_pic}}}blipFill")
    blip = etree.SubElement(blipFill, f"{{{ns_a}}}blip")
    blip.set(f"{{{ns_r}}}embed", rId)
    stretch = etree.SubElement(blipFill, f"{{{ns_a}}}stretch")
    etree.SubElement(stretch, f"{{{ns_a}}}fillRect")
    
    spPr = etree.SubElement(pic, f"{{{ns_pic}}}spPr")
    xfrm = etree.SubElement(spPr, f"{{{ns_a}}}xfrm")
    off = etree.SubElement(xfrm, f"{{{ns_a}}}off")
    off.set("x", "0")
    off.set("y", "0")
    ext = etree.SubElement(xfrm, f"{{{ns_a}}}ext")
    ext.set("cx", str(xfrm_cx))
    ext.set("cy", str(xfrm_cy))
    
    prstGeom = etree.SubElement(spPr, f"{{{ns_a}}}prstGeom")
    prstGeom.set("prst", "rect")
    
    return drawing
```

### Calcul des dimensions pour un logo quelconque

```python
from PIL import Image

def calculate_logo_dimensions(image_path, max_width_inches=1.8):
    """
    Calcule les dimensions EMU pour un logo en préservant les proportions.
    
    Args:
        image_path: chemin vers l'image (PNG, JPEG, SVG rendu en PNG)
        max_width_inches: largeur maximale souhaitée en pouces
    
    Returns:
        (extent_cx, extent_cy, xfrm_cx, xfrm_cy) en EMU
    """
    img = Image.open(image_path)
    ratio = img.width / img.height  # ex: 4.35 pour un logo horizontal
    
    EMU_PER_INCH = 914400
    width_emu = int(max_width_inches * EMU_PER_INCH)
    height_emu = int(width_emu / ratio)
    
    xfrm_cx = int(width_emu * 1.032)
    xfrm_cy = int(height_emu * 1.032)
    
    return width_emu, height_emu, xfrm_cx, xfrm_cy
```

## Unités de mesure

| Unité | Nom | Conversion |
|-------|-----|------------|
| EMU | English Metric Unit | 914400 EMU = 1 pouce |
| DXA | Twentieth of a point | 1440 DXA = 1 pouce |
| EMU ↔ DXA | | 1 DXA = 635 EMU |
| Pixel (96 dpi) | | 1 pouce = 96 px |
