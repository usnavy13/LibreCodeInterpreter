#!/usr/bin/env python3
"""Fill a DOCX template with structured content.

Usage:
    python3 fill_template.py <template.docx> <output.docx> <config.json>

Config JSON format:
{
    "placeholders": {
        "[TITRE DU DOCUMENT]": "Guide d'Installation n8n",
        "[Sous-titre du document]": "Automatisation Workflow",
        "[Auteur]": "Damien",
        "[Date]": "16/04/2026"
    },
    "sections": [
        {
            "title": "Introduction",
            "level": 0,
            "content": [
                {"type": "text", "text": "Paragraph text here."},
                {"type": "text", "text": "Bold text.", "bold": true},
                {"type": "bullets", "items": ["Item 1", "Item 2"]},
                {"type": "numbered", "items": ["Step 1", "Step 2"]},
                {"type": "code", "text": "docker compose up -d"},
                {"type": "table", "headers": ["Col A", "Col B"], "rows": [["a1", "b1"], ["a2", "b2"]]}
            ]
        }
    ]
}

Level mapping:
    0 = Titre1sansnumrotation (unnumbered heading)
    1 = Titre1 (numbered chapter: 1., 2., 3.)
    2 = Titre2 (numbered sub-chapter: 1.1, 1.2)
    3 = Titre3 (numbered sub-sub-chapter: 1.1.1)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

# === Namespaces ===
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"

etree.register_namespace("w", W_NS)


def _w(local: str) -> str:
    """Create a qualified name in the w: namespace."""
    return f"{{{W_NS}}}{local}"


# === Style IDs (francised, matching template-base.docx) ===
HEADING_STYLES = {
    0: "Titre1sansnumrotation",
    1: "Titre1",
    2: "Titre2",
    3: "Titre3",
}
STYLE_NORMAL = "Normal"
STYLE_LIST = "Paragraphedeliste"
STYLE_CODE = "Code"
BULLET_NUM_ID = "7"
NUMBERED_NUM_ID = "5"

# Table styling constants
TABLE_BORDER_COLOR = "CCCCCC"
TABLE_HEADER_FILL = "DAE5EF"
TABLE_WIDTH_DXA = 9360  # Full width for A4 with ~2cm margins

SCRIPTS_DIR = Path(__file__).parent
OFFICE_DIR = SCRIPTS_DIR / "office"


# === Smart quotes ===

def _smart_quotes(text: str) -> str:
    """Convert ASCII apostrophes and quotes to smart (typographic) equivalents."""
    if not text:
        return text
    # Apostrophe: ' → ' (right single quote U+2019)
    text = text.replace("\u0027", "\u2019")
    # Double quotes: simplistic approach — alternate left/right
    result = []
    open_dq = True
    for ch in text:
        if ch == '"':
            result.append("\u201C" if open_dq else "\u201D")
            open_dq = not open_dq
        else:
            result.append(ch)
    return "".join(result)


# === Element builders ===

def _make_text(text: str) -> etree._Element:
    """Create a <w:t> element with xml:space=preserve."""
    t = etree.Element(_w("t"))
    t.text = _smart_quotes(text or "")
    t.set(f"{{{XML_NS}}}space", "preserve")
    return t


def _make_run(text: str, bold: bool = False, font: str = None) -> etree._Element:
    """Create a <w:r> element with optional formatting."""
    r = etree.Element(_w("r"))
    if bold or font:
        rPr = etree.SubElement(r, _w("rPr"))
        if bold:
            etree.SubElement(rPr, _w("b"))
            etree.SubElement(rPr, _w("bCs"))
        if font:
            rFonts = etree.SubElement(rPr, _w("rFonts"))
            rFonts.set(_w("ascii"), font)
            rFonts.set(_w("hAnsi"), font)
    r.append(_make_text(text))
    return r


def _make_empty_para() -> etree._Element:
    """Create an empty <w:p/> spacer."""
    return etree.Element(_w("p"))


def _make_paragraph(text: str, bold: bool = False) -> etree._Element:
    """Create a Normal paragraph."""
    p = etree.Element(_w("p"))
    if text:
        p.append(_make_run(text, bold=bold))
    return p


def _make_heading(text: str, level: int) -> etree._Element:
    """Create a heading paragraph with the correct style."""
    p = etree.Element(_w("p"))
    pPr = etree.SubElement(p, _w("pPr"))
    pStyle = etree.SubElement(pPr, _w("pStyle"))
    pStyle.set(_w("val"), HEADING_STYLES.get(level, "Titre1"))
    p.append(_make_run(text))
    return p


def _make_bullet(text: str, level: int = 0) -> etree._Element:
    """Create a bullet list paragraph (dash style) at given indentation level."""
    p = etree.Element(_w("p"))
    pPr = etree.SubElement(p, _w("pPr"))
    pStyle = etree.SubElement(pPr, _w("pStyle"))
    pStyle.set(_w("val"), STYLE_LIST)
    numPr = etree.SubElement(pPr, _w("numPr"))
    ilvl = etree.SubElement(numPr, _w("ilvl"))
    ilvl.set(_w("val"), str(level))
    numId = etree.SubElement(numPr, _w("numId"))
    numId.set(_w("val"), BULLET_NUM_ID)
    p.append(_make_run(text))
    return p


def _make_code_line(text: str) -> etree._Element:
    """Create a single code line (PrformatHTML style)."""
    p = etree.Element(_w("p"))
    pPr = etree.SubElement(p, _w("pPr"))
    pStyle = etree.SubElement(pPr, _w("pStyle"))
    pStyle.set(_w("val"), STYLE_CODE)
    p.append(_make_run(text))
    return p


def _make_numbered(text: str, level: int = 0) -> etree._Element:
    """Create a numbered list paragraph (1., 2., 3.) at given indentation level."""
    p = etree.Element(_w("p"))
    pPr = etree.SubElement(p, _w("pPr"))
    pStyle = etree.SubElement(pPr, _w("pStyle"))
    pStyle.set(_w("val"), STYLE_LIST)
    numPr = etree.SubElement(pPr, _w("numPr"))
    ilvl = etree.SubElement(numPr, _w("ilvl"))
    ilvl.set(_w("val"), str(level))
    numId = etree.SubElement(numPr, _w("numId"))
    numId.set(_w("val"), NUMBERED_NUM_ID)
    p.append(_make_run(text))
    return p


def _make_table(headers: list, rows: list) -> etree._Element:
    """Create a table with header row and data rows."""
    num_cols = len(headers)
    col_width = TABLE_WIDTH_DXA // num_cols

    tbl = etree.Element(_w("tbl"))

    # Table properties
    tblPr = etree.SubElement(tbl, _w("tblPr"))
    tblStyle = etree.SubElement(tblPr, _w("tblStyle"))
    tblStyle.set(_w("val"), "TableGrid")
    tblW = etree.SubElement(tblPr, _w("tblW"))
    tblW.set(_w("w"), str(TABLE_WIDTH_DXA))
    tblW.set(_w("type"), "dxa")
    tblLook = etree.SubElement(tblPr, _w("tblLook"))
    tblLook.set(_w("val"), "04A0")
    tblLook.set(_w("firstRow"), "1")

    # Table grid (column widths)
    tblGrid = etree.SubElement(tbl, _w("tblGrid"))
    for _ in range(num_cols):
        gridCol = etree.SubElement(tblGrid, _w("gridCol"))
        gridCol.set(_w("w"), str(col_width))

    def _make_border_el(parent, name, color=TABLE_BORDER_COLOR):
        """Add a single border element (top/bottom/left/right)."""
        b = etree.SubElement(parent, _w(name))
        b.set(_w("val"), "single")
        b.set(_w("sz"), "4")
        b.set(_w("space"), "0")
        b.set(_w("color"), color)

    def _make_cell(text: str, bold: bool = False, shading: str = None) -> etree._Element:
        tc = etree.Element(_w("tc"))
        tcPr = etree.SubElement(tc, _w("tcPr"))
        tcW = etree.SubElement(tcPr, _w("tcW"))
        tcW.set(_w("w"), str(col_width))
        tcW.set(_w("type"), "dxa")
        # Borders (light gray)
        tcBorders = etree.SubElement(tcPr, _w("tcBorders"))
        for side in ("top", "left", "bottom", "right"):
            _make_border_el(tcBorders, side)
        if shading:
            shd = etree.SubElement(tcPr, _w("shd"))
            shd.set(_w("val"), "clear")
            shd.set(_w("color"), "auto")
            shd.set(_w("fill"), shading)
        p = etree.SubElement(tc, _w("p"))
        p.append(_make_run(text, bold=bold))
        return tc

    # Header row
    header_tr = etree.SubElement(tbl, _w("tr"))
    for h in headers:
        header_tr.append(_make_cell(h, bold=True, shading=TABLE_HEADER_FILL))

    # Data rows
    for row in rows:
        tr = etree.SubElement(tbl, _w("tr"))
        for i, cell_text in enumerate(row):
            tr.append(_make_cell(str(cell_text) if cell_text else ""))

    return tbl


def _make_page_break() -> etree._Element:
    """Create a page break paragraph."""
    p = etree.Element(_w("p"))
    r = etree.SubElement(p, _w("r"))
    br = etree.SubElement(r, _w("br"))
    br.set(_w("type"), "page")
    return p


# === Core logic ===

def replace_placeholders(root: etree._Element, placeholders: dict) -> int:
    """Replace placeholder text in all <w:t> elements. Returns count of replacements."""
    count = 0
    for t_elem in root.iter(_w("t")):
        if t_elem.text is None:
            continue
        for old, new in placeholders.items():
            if old in t_elem.text:
                t_elem.text = t_elem.text.replace(old, new)
                count += 1
    return count


def remove_placeholder_body(body: etree._Element) -> int:
    """Remove template placeholder paragraphs (after last table, before sectPr)."""
    # Find sectPr
    sect_pr = body.find(_w("sectPr"))
    if sect_pr is None:
        return 0

    # Find last table
    tables = body.findall(_w("tbl"))
    if not tables:
        return 0
    last_tbl = tables[-1]
    last_tbl_idx = list(body).index(last_tbl)

    # Find sectPr index
    sect_pr_idx = list(body).index(sect_pr)

    # Remove everything between last table and sectPr
    to_remove = list(body)[last_tbl_idx + 1: sect_pr_idx]
    for elem in to_remove:
        body.remove(elem)

    return len(to_remove)


def _expand_list_items(items: list, make_func, level: int = 0) -> list:
    """Expand list items supporting nested sub-items.

    Items can be:
      - A string: "Simple item"
      - A dict with subitems: {"text": "Item", "subitems": ["Sub A", "Sub B"]}
      - A dict with deeper nesting: {"text": "Item", "subitems": [{"text": ..., "subitems": [...]}]}

    Returns a flat list of paragraph elements with correct indentation levels.
    """
    elements = []
    for item in items:
        if isinstance(item, str):
            elements.append(make_func(item, level=level))
        elif isinstance(item, dict):
            text = item.get("text", "")
            elements.append(make_func(text, level=level))
            subitems = item.get("subitems", [])
            if subitems:
                elements.extend(_expand_list_items(subitems, _make_bullet, level=level + 1))
    return elements


def insert_sections(body: etree._Element, sections: list) -> int:
    """Insert structured content sections before <w:sectPr>."""
    sect_pr = body.find(_w("sectPr"))
    if sect_pr is None:
        print("WARNING: No <w:sectPr> found, appending at end", file=sys.stderr)
        insert_point = len(list(body))
    else:
        insert_point = list(body).index(sect_pr)

    # Add a page break before content (after cover page)
    elements = [_make_page_break()]

    for section in sections:
        title = section.get("title", "")
        level = section.get("level", 1)
        content_blocks = section.get("content", [])

        # Add heading
        if title:
            elements.append(_make_heading(title, level))

        # Add content blocks
        for block in content_blocks:
            block_type = block.get("type", "text")

            if block_type == "text":
                text = block.get("text", "")
                bold = block.get("bold", False)
                elements.append(_make_paragraph(text, bold=bold))

            elif block_type == "bullets":
                items = block.get("items", [])
                elements.extend(_expand_list_items(items, _make_bullet))

            elif block_type == "numbered":
                items = block.get("items", [])
                elements.extend(_expand_list_items(items, _make_numbered))

            elif block_type == "code":
                code_text = block.get("text", "")
                for line in code_text.split("\n"):
                    elements.append(_make_code_line(line))

            elif block_type == "table":
                headers = block.get("headers", [])
                rows = block.get("rows", [])
                if headers:
                    elements.append(_make_table(headers, rows))

            elif block_type == "empty":
                elements.append(_make_empty_para())

        # Add spacer after each section
        elements.append(_make_empty_para())

    # Insert all elements before sectPr
    for i, elem in enumerate(elements):
        body.insert(insert_point + i, elem)

    return len(elements)


def fill_template(template_path: str, output_path: str, config: dict) -> str:
    """Fill a DOCX template with structured content."""
    placeholders = config.get("placeholders", {})
    sections = config.get("sections", [])

    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        unpack_dir = os.path.join(tmpdir, "unpacked")

        # Unpack template using zipfile (raw, no pretty-print)
        with zipfile.ZipFile(template_path) as z:
            z.extractall(unpack_dir)

        # Parse document.xml
        doc_path = os.path.join(unpack_dir, "word", "document.xml")
        tree = etree.parse(doc_path)
        root = tree.getroot()
        body = root.find(_w("body"))

        if body is None:
            return "Error: No <w:body> found in document.xml"

        # Step 1: Replace placeholders
        n_replaced = replace_placeholders(root, placeholders)
        print(f"Replaced {n_replaced} placeholder(s)")

        # Step 2: Remove template placeholder body content
        n_removed = remove_placeholder_body(body)
        print(f"Removed {n_removed} placeholder paragraph(s)")

        # Step 3: Insert new sections
        n_inserted = insert_sections(body, sections)
        print(f"Inserted {n_inserted} element(s)")

        # Write modified XML
        tree.write(doc_path, xml_declaration=True, encoding="UTF-8", standalone=True)

        # Pack (skip internal validation — we validate separately)
        pack_result = subprocess.run(
            [sys.executable, str(OFFICE_DIR / "pack.py"), unpack_dir, output_path, "--validate", "false"],
            capture_output=True, text=True
        )
        if pack_result.returncode != 0:
            print(pack_result.stdout, file=sys.stdout)
            print(pack_result.stderr, file=sys.stderr)
            return f"Error: pack.py failed with exit code {pack_result.returncode}"

        print(pack_result.stdout.strip())

        # Validate
        val_result = subprocess.run(
            [sys.executable, str(OFFICE_DIR / "validate.py"), output_path],
            capture_output=True, text=True
        )
        print(val_result.stdout.strip())
        if val_result.returncode != 0:
            print(val_result.stderr, file=sys.stderr)
            return f"Warning: validation reported issues (file still created)"

    return f"Success: {output_path}"


def main():
    parser = argparse.ArgumentParser(description="Fill a DOCX template with structured content")
    parser.add_argument("template", help="Path to template DOCX file")
    parser.add_argument("output", help="Output DOCX file path")
    parser.add_argument("config", help="Path to JSON config file")
    args = parser.parse_args()

    # Load config
    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Validate config
    if "sections" not in config:
        print("Error: config must contain 'sections' key", file=sys.stderr)
        sys.exit(1)

    result = fill_template(args.template, args.output, config)
    print(result)

    if result.startswith("Error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
