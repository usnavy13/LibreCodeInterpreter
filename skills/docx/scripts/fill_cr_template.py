#!/usr/bin/env python3
"""Fill the Compte-Rendu (CR) template with meeting data.

Usage:
    python3 fill_cr_template.py <template-compte-rendu.docx> <output.docx> <config.json>

Config JSON format:
{
    "meeting": {
        "title": "Compte-rendu d'atelier IA",
        "subtitle": "Client / Objet de la réunion",
        "date": "16/04/2026",
        "location": "Visioconférence Teams",
        "organizer": "Damien Juillard"
    },
    "participants": [
        {"name": "Sophie Martin", "role": "Directrice RH", "company": "Nextera Corp"},
        {"name": "Damien Juillard", "role": "Consultant IA", "company": "On Behalf AI"}
    ],
    "sections": [
        {
            "title": "Contexte",
            "level": 1,
            "content": [
                {"type": "text", "text": "Description du contexte..."},
                {"type": "bullets", "items": ["Point 1", "Point 2"]}
            ]
        }
    ]
}

Sections use the same content block types as fill_template.py:
    text, bullets, numbered, code, table, empty
"""

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

# Import builders from fill_template
sys.path.insert(0, str(Path(__file__).parent))
from fill_template import (
    _w, W_NS, XML_NS,
    _make_run, _make_text, _make_empty_para, _make_paragraph,
    _make_heading, _make_bullet, _make_numbered, _make_code_line,
    _make_table, _make_page_break,
    replace_placeholders,
    HEADING_STYLES, STYLE_LIST, STYLE_CODE, BULLET_NUM_ID,
)

SCRIPTS_DIR = Path(__file__).parent
OFFICE_DIR = SCRIPTS_DIR / "office"


def _set_cell_text(cell: etree._Element, text: str):
    """Set all <w:t> in a cell to text (first gets text, rest cleared)."""
    t_elems = list(cell.iter(_w("t")))
    if t_elems:
        t_elems[0].text = text
        t_elems[0].set(f"{{{XML_NS}}}space", "preserve")
        for t in t_elems[1:]:
            t.text = ""
    else:
        # No <w:t> found, add one
        paras = cell.findall(_w("p"))
        if paras:
            paras[0].append(_make_run(text))


def _clone_row(table: etree._Element) -> etree._Element:
    """Clone the last row of a table (deep copy with formatting)."""
    rows = table.findall(_w("tr"))
    if not rows:
        return None
    return copy.deepcopy(rows[-1])


def fill_meeting_metadata(body: etree._Element, meeting: dict) -> int:
    """Fill the 3 header tables with meeting data."""
    tables = body.findall(_w("tbl"))
    count = 0

    # Table 0: Title + logo
    if len(tables) >= 1:
        placeholders = {
            "[Titre du Compte-Rendu]": meeting.get("title", ""),
            "[Client / Objet]": meeting.get("subtitle", ""),
        }
        count += replace_placeholders(tables[0], placeholders)

    # Table 1: Date / Lieu / Organisateur
    if len(tables) >= 2:
        placeholders = {
            "[Date]": meeting.get("date", ""),
            "[Lieu]": meeting.get("location", ""),
            "[Organisateur]": meeting.get("organizer", ""),
        }
        count += replace_placeholders(tables[1], placeholders)

    return count


def fill_participants(body: etree._Element, participants: list) -> int:
    """Fill the participants table, adding rows as needed."""
    tables = body.findall(_w("tbl"))
    if len(tables) < 3:
        return 0

    ptable = tables[2]
    rows = ptable.findall(_w("tr"))

    # We need as many rows as participants
    # Template has 2 rows — add more if needed, remove if fewer
    while len(rows) < len(participants):
        new_row = _clone_row(ptable)
        if new_row is not None:
            ptable.append(new_row)
            rows = ptable.findall(_w("tr"))

    # Remove extra rows
    while len(rows) > len(participants):
        ptable.remove(rows[-1])
        rows = ptable.findall(_w("tr"))

    # Fill each row
    for i, participant in enumerate(participants):
        row = rows[i]
        cells = row.findall(_w("tc"))
        if len(cells) >= 3:
            _set_cell_text(cells[0], participant.get("name", ""))
            _set_cell_text(cells[1], participant.get("role", ""))
            _set_cell_text(cells[2], participant.get("company", ""))

    return len(participants)


def remove_cr_placeholder_body(body: etree._Element) -> int:
    """Remove CR placeholder paragraphs (after 3rd table, before sectPr)."""
    sect_pr = body.find(_w("sectPr"))
    if sect_pr is None:
        return 0

    tables = body.findall(_w("tbl"))
    if len(tables) < 3:
        return 0

    last_tbl = tables[2]
    last_tbl_idx = list(body).index(last_tbl)
    sect_pr_idx = list(body).index(sect_pr)

    to_remove = list(body)[last_tbl_idx + 1: sect_pr_idx]
    for elem in to_remove:
        body.remove(elem)

    return len(to_remove)


def _expand_list_items(items: list, make_func, level: int = 0) -> list:
    """Expand list items supporting nested sub-items.

    Items can be:
      - A string: "Simple item"
      - A dict with subitems: {"text": "Item", "subitems": ["Sub A", "Sub B"]}
      - A dict with deeper nesting: {"text": "Item", "subitems": [{"text": "Sub", "subitems": [...]}]}

    Returns a flat list of paragraph elements with correct indentation levels.
    """
    elements = []
    for item in items:
        if isinstance(item, str):
            elements.append(make_func(item, level=level))
        elif isinstance(item, dict):
            text = item.get("text", "")
            elements.append(make_func(text, level=level))
            # Process subitems as bullets one level deeper
            subitems = item.get("subitems", [])
            if subitems:
                elements.extend(_expand_list_items(subitems, _make_bullet, level=level + 1))
    return elements


def insert_cr_sections(body: etree._Element, sections: list) -> int:
    """Insert content sections before <w:sectPr>."""
    sect_pr = body.find(_w("sectPr"))
    if sect_pr is None:
        insert_point = len(list(body))
    else:
        insert_point = list(body).index(sect_pr)

    elements = [_make_empty_para(), _make_empty_para()]

    for section in sections:
        title = section.get("title", "")
        level = section.get("level", 1)
        content_blocks = section.get("content", [])

        if title:
            elements.append(_make_heading(title, level))

        for block in content_blocks:
            block_type = block.get("type", "text")

            if block_type == "text":
                elements.append(_make_paragraph(block.get("text", ""), bold=block.get("bold", False)))
            elif block_type == "bullets":
                items = block.get("items", [])
                elements.extend(_expand_list_items(items, _make_bullet, level=0))
            elif block_type == "numbered":
                items = block.get("items", [])
                elements.extend(_expand_list_items(items, _make_numbered, level=0))
            elif block_type == "code":
                for line in block.get("text", "").split("\n"):
                    elements.append(_make_code_line(line))
            elif block_type == "table":
                headers = block.get("headers", [])
                rows = block.get("rows", [])
                if headers:
                    elements.append(_make_table(headers, rows))
            elif block_type == "empty":
                elements.append(_make_empty_para())

        elements.append(_make_empty_para())

    for i, elem in enumerate(elements):
        body.insert(insert_point + i, elem)

    return len(elements)


def fill_cr_template(template_path: str, output_path: str, config: dict) -> str:
    """Fill a CR template with meeting data and sections."""
    meeting = config.get("meeting", {})
    participants = config.get("participants", [])
    sections = config.get("sections", [])

    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        unpack_dir = os.path.join(tmpdir, "unpacked")

        with zipfile.ZipFile(template_path) as z:
            z.extractall(unpack_dir)

        doc_path = os.path.join(unpack_dir, "word", "document.xml")
        tree = etree.parse(doc_path)
        root = tree.getroot()
        body = root.find(_w("body"))

        if body is None:
            return "Error: No <w:body> found"

        # Step 1: Fill meeting metadata (tables 0 and 1)
        n_meta = fill_meeting_metadata(body, meeting)
        print(f"Filled {n_meta} metadata field(s)")

        # Step 2: Fill participants (table 2)
        n_part = fill_participants(body, participants)
        print(f"Filled {n_part} participant(s)")

        # Step 3: Remove placeholder body
        n_removed = remove_cr_placeholder_body(body)
        print(f"Removed {n_removed} placeholder paragraph(s)")

        # Step 4: Insert sections
        n_inserted = insert_cr_sections(body, sections)
        print(f"Inserted {n_inserted} element(s)")

        tree.write(doc_path, xml_declaration=True, encoding="UTF-8", standalone=True)

        pack_result = subprocess.run(
            [sys.executable, str(OFFICE_DIR / "pack.py"), unpack_dir, output_path, "--validate", "false"],
            capture_output=True, text=True
        )
        if pack_result.returncode != 0:
            print(pack_result.stdout)
            print(pack_result.stderr, file=sys.stderr)
            return f"Error: pack.py failed"
        print(pack_result.stdout.strip())

        val_result = subprocess.run(
            [sys.executable, str(OFFICE_DIR / "validate.py"), output_path],
            capture_output=True, text=True
        )
        print(val_result.stdout.strip())
        if val_result.returncode != 0:
            print(val_result.stderr, file=sys.stderr)
            return f"Warning: validation issues (file still created)"

    return f"Success: {output_path}"


def main():
    parser = argparse.ArgumentParser(description="Fill a Compte-Rendu DOCX template")
    parser.add_argument("template", help="Path to CR template DOCX")
    parser.add_argument("output", help="Output DOCX file path")
    parser.add_argument("config", help="Path to JSON config file")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    result = fill_cr_template(args.template, args.output, config)
    print(result)

    if result.startswith("Error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
