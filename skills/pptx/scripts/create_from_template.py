#!/usr/bin/env python3
"""Create a presentation from the OBA corporate template.

Usage:
    python3 create_from_template.py <output.pptx> <config.json>

Config JSON format:
{
    "slides": [
        {
            "layout": "slideLayout2.xml",
            "content": {
                "ctrTitle": "Titre de la présentation",
                "subTitle": "Sous-titre"
            }
        },
        {
            "layout": "slideLayout38.xml",
            "content": {
                "ctrTitle": "Section 1",
                "subTitle": "Description de la section"
            }
        },
        {
            "layout": "slideLayout7.xml",
            "content": {
                "title": "Points clés",
                "body": "Premier point\\nDeuxième point\\nTroisième point"
            }
        }
    ]
}

Layout reference:
    slideLayout1.xml  - Title
    slideLayout2.xml  - Title + text
    slideLayout3.xml  - Title + image
    slideLayout5.xml  - Agenda
    slideLayout6.xml  - Title + Subtitle + Content #1
    slideLayout7.xml  - Title + Content #1
    slideLayout13.xml - Title + Chart #1
    slideLayout15.xml - Title + Table #1
    slideLayout19.xml - Title + Content + Image #1
    slideLayout21.xml - Title + 2 Content #1
    slideLayout27.xml - Title + 3 Content #1
    slideLayout38.xml - Section title - dark blue
    slideLayout39.xml - Section title - light blue
    slideLayout41.xml - Section title - orange
    slideLayout43.xml - Quote
    slideLayout44.xml - Team
    slideLayout49.xml - End - Thank you #2

Content keys match placeholder types:
    ctrTitle / title  - Main title
    subTitle          - Subtitle
    body              - Body text (use \\n for line breaks, lines starting with - become bullets)
    body14, body15... - Specific body placeholder by idx
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lxml import etree

SCRIPTS_DIR = Path(__file__).parent
OFFICE_DIR = SCRIPTS_DIR / "office"
TEMPLATE = SCRIPTS_DIR.parent / "templates" / "onbehalfai" / "template-oba-corporate.pptx"

# Namespaces
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _p(local): return f"{{{NS_P}}}{local}"
def _a(local): return f"{{{NS_A}}}{local}"
def _r(local): return f"{{{NS_R}}}{local}"


def add_slide_and_register(unpacked_dir, layout_file):
    """Run add_slide.py and automatically insert the sldId into presentation.xml."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "add_slide.py"), unpacked_dir, layout_file],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"  ERROR add_slide: {result.stderr.strip()}", file=sys.stderr)
        return None

    stdout = result.stdout.strip()
    print(f"  {stdout.split(chr(10))[0]}")

    # Parse the sldId from output: <p:sldId id="..." r:id="..."/>
    match = re.search(r'id="(\d+)".*r:id="(rId\d+)"', stdout)
    if not match:
        print(f"  WARNING: could not parse sldId from output", file=sys.stderr)
        return None

    slide_id = match.group(1)
    rid = match.group(2)

    # Insert into presentation.xml
    pres_path = os.path.join(unpacked_dir, "ppt", "presentation.xml")
    tree = etree.parse(pres_path)
    root = tree.getroot()

    sld_id_lst = root.find(_p("sldIdLst"))
    if sld_id_lst is None:
        sld_id_lst = etree.SubElement(root, _p("sldIdLst"))

    new_sld_id = etree.SubElement(sld_id_lst, _p("sldId"))
    new_sld_id.set("id", slide_id)
    new_sld_id.set(_r("id"), rid)

    tree.write(pres_path, xml_declaration=True, encoding="UTF-8", standalone=True)

    # Return the slide filename
    slide_match = re.search(r'Created (\S+\.xml)', stdout)
    return slide_match.group(1) if slide_match else None


def fill_slide_content(unpacked_dir, slide_filename, content):
    """Fill placeholder text in a slide XML file."""
    slide_path = os.path.join(unpacked_dir, "ppt", "slides", slide_filename)
    if not os.path.exists(slide_path):
        print(f"  WARNING: {slide_path} not found", file=sys.stderr)
        return

    tree = etree.parse(slide_path)
    root = tree.getroot()

    for sp in root.findall(f".//{_p('sp')}"):
        ph = sp.find(f".//{_p('ph')}")
        if ph is None:
            continue

        ph_type = ph.get("type", "body")
        ph_idx = ph.get("idx", "")

        # Determine which content key matches this placeholder
        text = None
        if ph_type in ("ctrTitle", "title"):
            text = content.get("ctrTitle") or content.get("title")
        elif ph_type == "subTitle":
            text = content.get("subTitle") or content.get("subtitle")
        elif ph_type == "body":
            # Try specific key first (body14, body15...), then generic "body"
            text = content.get(f"body{ph_idx}") or content.get("body")
        elif ph_type == "dt":
            text = content.get("date")

        if text is None:
            continue

        # Clear existing text and replace
        _set_placeholder_text(sp, text)

    tree.write(slide_path, xml_declaration=True, encoding="UTF-8", standalone=True)


def _set_placeholder_text(sp, text):
    """Set text content of a shape placeholder, handling multi-line and bullets."""
    txBody = sp.find(f".//{_p('txBody')}")
    if txBody is None:
        txBody = sp.find(f".//{_a('txBody')}")
    if txBody is None:
        return

    # Collect existing paragraph formatting from the first paragraph
    existing_paras = txBody.findall(_a("p"))
    first_pPr = None
    first_rPr = None
    if existing_paras:
        pPr = existing_paras[0].find(_a("pPr"))
        if pPr is not None:
            first_pPr = pPr
        for r in existing_paras[0].findall(_a("r")):
            rPr = r.find(_a("rPr"))
            if rPr is not None:
                first_rPr = rPr
                break

    # Remove all existing paragraphs
    for p in existing_paras:
        txBody.remove(p)

    # Split text into lines
    lines = text.split("\n")

    for i, line in enumerate(lines):
        p = etree.SubElement(txBody, _a("p"))

        # Copy paragraph properties from first paragraph
        if first_pPr is not None and i == 0:
            from copy import deepcopy
            p.insert(0, deepcopy(first_pPr))

        # Check if line is a bullet (starts with "- ")
        is_bullet = line.strip().startswith("- ")
        if is_bullet:
            line = line.strip()[2:]  # Remove "- " prefix
            # Add bullet paragraph properties
            pPr = p.find(_a("pPr"))
            if pPr is None:
                pPr = etree.SubElement(p, _a("pPr"))
                p.insert(0, pPr)
            buChar = etree.SubElement(pPr, _a("buChar"))
            buChar.set("char", "\u2022")

        if not line.strip():
            # Empty line — just an empty paragraph
            etree.SubElement(p, _a("endParaRPr"))
            continue

        r = etree.SubElement(p, _a("r"))

        # Copy run properties
        if first_rPr is not None:
            from copy import deepcopy
            r.insert(0, deepcopy(first_rPr))

        t = etree.SubElement(r, _a("t"))
        t.text = text if len(lines) == 1 else line
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

        if len(lines) == 1:
            break  # Single line, done


def remove_initial_slide(unpacked_dir):
    """Remove slide1.xml (the empty template slide) from the presentation."""
    pres_path = os.path.join(unpacked_dir, "ppt", "presentation.xml")
    tree = etree.parse(pres_path)
    root = tree.getroot()

    sld_id_lst = root.find(_p("sldIdLst"))
    if sld_id_lst is not None:
        # Find the sldId for slide1 (it references rId that points to slides/slide1.xml)
        # Read the rels to find which rId points to slide1
        rels_path = os.path.join(unpacked_dir, "ppt", "_rels", "presentation.xml.rels")
        rels_tree = etree.parse(rels_path)
        slide1_rid = None
        for rel in rels_tree.getroot():
            if rel.get("Target", "") == "slides/slide1.xml":
                slide1_rid = rel.get("Id")
                break

        if slide1_rid:
            for sld_id in sld_id_lst.findall(_p("sldId")):
                if sld_id.get(_r("id")) == slide1_rid:
                    sld_id_lst.remove(sld_id)
                    print("  Removed slide1 from sldIdLst")
                    break

    tree.write(pres_path, xml_declaration=True, encoding="UTF-8", standalone=True)


def create_presentation(output_path, config):
    """Create a presentation from the OBA corporate template."""
    slides_config = config.get("slides", [])

    if not slides_config:
        print("Error: no slides defined in config", file=sys.stderr)
        return False

    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        work_pptx = os.path.join(tmpdir, "work.pptx")
        unpacked = os.path.join(tmpdir, "unpacked")

        # 1. Copy template
        shutil.copy(str(TEMPLATE), work_pptx)
        print(f"Copied template ({os.path.getsize(work_pptx):,} bytes)")

        # 2. Unpack
        subprocess.run(
            [sys.executable, str(OFFICE_DIR / "unpack.py"), work_pptx, unpacked],
            capture_output=True, text=True, check=True
        )
        print("Unpacked template")

        # 3. Remove initial empty slide
        remove_initial_slide(unpacked)

        # 4. Add slides from layouts and fill content
        for i, slide_cfg in enumerate(slides_config):
            layout = slide_cfg.get("layout", "slideLayout7.xml")
            content = slide_cfg.get("content", {})

            print(f"\nSlide {i+1}: {layout}")
            slide_file = add_slide_and_register(unpacked, layout)

            if slide_file and content:
                fill_slide_content(unpacked, slide_file, content)
                print(f"  Filled content: {list(content.keys())}")

        # 5. Clean
        clean_result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "clean.py"), unpacked],
            capture_output=True, text=True
        )
        if clean_result.stdout.strip():
            print(f"\nClean: {clean_result.stdout.strip()[:200]}")

        # 6. Pack
        pack_result = subprocess.run(
            [sys.executable, str(OFFICE_DIR / "pack.py"), unpacked, output_path,
             "--validate", "false"],
            capture_output=True, text=True
        )
        if pack_result.returncode != 0:
            print(f"Pack error: {pack_result.stderr}", file=sys.stderr)
            return False

        print(f"\nCreated: {output_path} ({os.path.getsize(output_path):,} bytes)")

    return True


def main():
    parser = argparse.ArgumentParser(description="Create presentation from OBA corporate template")
    parser.add_argument("output", help="Output PPTX file path")
    parser.add_argument("config", help="Path to JSON config file")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    success = create_presentation(args.output, config)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
