#!/usr/bin/env python3
"""Fill the Courrier (letter) template with sender/recipient data and body text.

Usage:
    python3 fill_courrier_template.py <template-courrier.docx> <output.docx> <config.json>

Config JSON format:
{
    "date": "20 avril 2026",
    "recipient": {
        "name": "M. Jean Dupont",
        "address_line1": "123 rue de la Paix",
        "address_line2": "75001 Paris"
    },
    "subject": "Proposition de mission IA",
    "salutation": "Madame, Monsieur",
    "body": [
        "Premier paragraphe du courrier.",
        "Deuxième paragraphe avec **mots en gras** si besoin.",
        "Troisième paragraphe."
    ],
    "closing": "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.",
    "sender": {
        "name": "Damien Juillard",
        "title": "Consultant IA",
        "email": "damien@onbehalf.ai",
        "phone": "+33 6 XX XX XX XX"
    }
}
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
from fill_template import (
    _w, W_NS, XML_NS,
    _make_run, _make_runs_from_text, _make_empty_para,
    replace_placeholders,
)

SCRIPTS_DIR = Path(__file__).parent
OFFICE_DIR = SCRIPTS_DIR / "office"


def fill_courrier(template_path: str, output_path: str, config: dict) -> str:
    """Fill a courrier template with letter data."""
    date = config.get("date", "")
    recipient = config.get("recipient", {})
    subject = config.get("subject", "")
    salutation = config.get("salutation", "Madame, Monsieur")
    body_paragraphs = config.get("body", [])
    closing = config.get("closing", "")
    sender = config.get("sender", {})

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

        # Step 1: Replace simple placeholders
        placeholders = {
            "[Date]": date,
            "[Destinataire]": recipient.get("name", ""),
            "[Adresse ligne 1]": recipient.get("address_line1", ""),
            "[Adresse ligne 2]": recipient.get("address_line2", ""),
            "[Objet du courrier]": subject,
            "[Madame, Monsieur]": salutation,
            "[Formule de politesse]": closing,
            "[Prénom Nom]": sender.get("name", ""),
        }
        n_replaced = replace_placeholders(body, placeholders)
        print(f"Replaced {n_replaced} placeholder(s)")

        # Step 2: Replace function/email/phone in sender block
        sender_placeholders = {
            "Fonction": sender.get("title", ""),
            "email@onbehalf.ai": sender.get("email", ""),
            "+33 X XX  XX   XX  XX": sender.get("phone", ""),
            "+33 X XX XX XX XX": sender.get("phone", ""),
        }
        for t_elem in body.iter(_w("t")):
            if t_elem.text is None:
                continue
            for old, new in sender_placeholders.items():
                if old in t_elem.text:
                    t_elem.text = t_elem.text.replace(old, new)

        # Step 3: Replace body placeholder with actual paragraphs
        # Find the paragraph containing "[Corps du courrier"
        sect_pr = body.find(_w("sectPr"))
        body_para = None
        body_para_idx = None
        for i, child in enumerate(list(body)):
            if child.tag != _w("p"):
                continue
            texts = [t.text for t in child.iter(_w("t")) if t.text]
            full_text = " ".join(texts)
            if "Corps du courrier" in full_text or "Lorem ipsum" in full_text:
                body_para = child
                body_para_idx = i
                break

        if body_para is not None and body_paragraphs:
            # Remove the placeholder paragraph
            body.remove(body_para)

            # Insert actual body paragraphs
            for j, para_text in enumerate(body_paragraphs):
                p = etree.Element(_w("p"))
                # Copy formatting from original placeholder (if any)
                for run in _make_runs_from_text(para_text):
                    p.append(run)
                body.insert(body_para_idx + j, p)
                # Add spacing between paragraphs
                if j < len(body_paragraphs) - 1:
                    body.insert(body_para_idx + j + 1, _make_empty_para())
                    body_para_idx += 1  # Adjust for inserted empty para

        print(f"Inserted {len(body_paragraphs)} body paragraph(s)")

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

    return f"Success: {output_path}"


def main():
    parser = argparse.ArgumentParser(description="Fill a Courrier DOCX template")
    parser.add_argument("template", help="Path to courrier template DOCX")
    parser.add_argument("output", help="Output DOCX file path")
    parser.add_argument("config", help="Path to JSON config file")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    result = fill_courrier(args.template, args.output, config)
    print(result)

    if result.startswith("Error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
