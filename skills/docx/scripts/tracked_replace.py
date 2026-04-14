"""Find-and-replace with Word tracked changes (redlines).

Operates on an unpacked DOCX directory (produced by office/unpack.py).
Creates proper <w:del>/<w:ins> markup so changes appear as redlines in Word.

Usage:
    python tracked_replace.py UNPACKED_DIR \
        --old "old text" --new "new text" \
        [--old "another" --new "replacement"] \
        [--first] [--author "AI-Agent"]
"""

import argparse
import copy
import datetime
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"

NSMAP = {
    "w": W_NS,
    "w14": W14_NS,
}


def _tag(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


WT = _tag(W_NS, "t")
WR = _tag(W_NS, "r")
WRPR = _tag(W_NS, "rPr")
WP = _tag(W_NS, "p")
WDEL = _tag(W_NS, "del")
WINS = _tag(W_NS, "ins")
WDELTEXT = _tag(W_NS, "delText")
WDEL_RUN = _tag(W_NS, "del")
WINS_RUN = _tag(W_NS, "ins")


def _get_run_text(run: etree._Element) -> str:
    """Extract concatenated text from all <w:t> children of a run."""
    parts = []
    for t in run.findall(f"{{{W_NS}}}t"):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def _get_rpr(run: etree._Element) -> Optional[etree._Element]:
    """Get the <w:rPr> from a run, or None."""
    return run.find(f"{{{W_NS}}}rPr")


def _clone_rpr(run: etree._Element) -> Optional[etree._Element]:
    """Deep-copy the <w:rPr> from a run."""
    rpr = _get_rpr(run)
    if rpr is not None:
        return copy.deepcopy(rpr)
    return None


def _make_run(text: str, rpr: Optional[etree._Element] = None) -> etree._Element:
    """Create a <w:r> element with optional formatting."""
    r = etree.SubElement(etree.Element("dummy"), _tag(W_NS, "r"))
    r = copy.deepcopy(r)
    if rpr is not None:
        r.insert(0, copy.deepcopy(rpr))
    t = etree.SubElement(r, _tag(W_NS, "t"))
    t.text = text
    if text and (text[0] == " " or text[-1] == " "):
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r


def _make_del_run(text: str, rpr: Optional[etree._Element] = None) -> etree._Element:
    """Create a <w:r> with <w:delText> for deletion markup."""
    r = etree.Element(_tag(W_NS, "r"))
    if rpr is not None:
        r.insert(0, copy.deepcopy(rpr))
    dt = etree.SubElement(r, _tag(W_NS, "delText"))
    dt.text = text
    if text and (text[0] == " " or text[-1] == " "):
        dt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r


def _is_inside_tracked_change(run: etree._Element) -> bool:
    """Check if a run is already inside <w:del> or <w:ins>."""
    parent = run.getparent()
    while parent is not None:
        tag = parent.tag
        if tag == WDEL or tag == WINS:
            return True
        if tag == WP:
            break
        parent = parent.getparent()
    return False


def _get_next_rev_id(root: etree._Element) -> int:
    """Find the highest existing revision ID and return next value."""
    max_id = 0
    for elem in root.iter():
        rid = elem.get(f"{{{W_NS}}}id") or elem.get("id")
        if rid is not None:
            try:
                val = int(rid)
                if val > max_id:
                    max_id = val
            except ValueError:
                pass
    return max_id + 1


def _collect_paragraph_runs(para: etree._Element) -> List[Tuple[etree._Element, str]]:
    """Collect direct-child runs and their text from a paragraph.

    Skips runs already inside tracked changes.
    """
    runs = []
    for child in para:
        if child.tag == WR and not _is_inside_tracked_change(child):
            runs.append((child, _get_run_text(child)))
    return runs


def replace_in_paragraph(
    para: etree._Element,
    old_text: str,
    new_text: str,
    author: str,
    date_str: str,
    rev_id_start: int,
    first_only: bool,
) -> Tuple[int, int]:
    """Replace occurrences of old_text with tracked-change markup in a paragraph.

    Returns (replacements_made, next_rev_id).
    """
    runs = _collect_paragraph_runs(para)
    if not runs:
        return 0, rev_id_start

    full_text = "".join(text for _, text in runs)
    if old_text not in full_text:
        return 0, rev_id_start

    run_boundaries = []
    pos = 0
    for run, text in runs:
        run_boundaries.append((run, pos, pos + len(text), text))
        pos += len(text)

    matches = []
    start = 0
    while True:
        idx = full_text.find(old_text, start)
        if idx == -1:
            break
        matches.append((idx, idx + len(old_text)))
        if first_only:
            break
        start = idx + len(old_text)

    if not matches:
        return 0, rev_id_start

    rev_id = rev_id_start
    offset = 0

    for match_start, match_end in matches:
        adj_start = match_start + offset
        adj_end = match_end + offset

        runs = _collect_paragraph_runs(para)
        full_text_current = "".join(_get_run_text(r) for r, _ in [(r, None) for r in [child for child in para if child.tag == WR and not _is_inside_tracked_change(child)]])

        affected_runs = []
        current_pos = 0
        for child in list(para):
            if child.tag != WR or _is_inside_tracked_change(child):
                current_pos_text = _get_run_text(child) if child.tag == WR else ""
                if child.tag == WR:
                    current_pos += len(current_pos_text)
                continue
            run_text = _get_run_text(child)
            run_start = current_pos
            run_end = current_pos + len(run_text)
            if run_end > adj_start and run_start < adj_end:
                affected_runs.append((child, run_start, run_end, run_text))
            current_pos = run_end

        if not affected_runs:
            continue

        first_affected = affected_runs[0][0]
        insert_point = list(para).index(first_affected)
        rpr_template = _clone_rpr(first_affected)

        del_elem = etree.Element(_tag(W_NS, "del"))
        del_elem.set(_tag(W_NS, "id"), str(rev_id))
        del_elem.set(_tag(W_NS, "author"), author)
        del_elem.set(_tag(W_NS, "date"), date_str)

        ins_elem = etree.Element(_tag(W_NS, "ins"))
        ins_elem.set(_tag(W_NS, "id"), str(rev_id + 1))
        ins_elem.set(_tag(W_NS, "author"), author)
        ins_elem.set(_tag(W_NS, "date"), date_str)

        pre_runs = []
        post_runs = []

        for run_elem, run_start, run_end, run_text in affected_runs:
            local_del_start = max(0, adj_start - run_start)
            local_del_end = min(len(run_text), adj_end - run_start)

            run_rpr = _clone_rpr(run_elem)

            pre_text = run_text[:local_del_start]
            del_text = run_text[local_del_start:local_del_end]
            post_text = run_text[local_del_end:]

            if pre_text:
                pre_runs.append(_make_run(pre_text, run_rpr))

            if del_text:
                del_elem.append(_make_del_run(del_text, run_rpr))

            if post_text:
                post_runs.append(_make_run(post_text, run_rpr))

        if new_text:
            ins_elem.append(_make_run(new_text, rpr_template))

        for run_elem, _, _, _ in affected_runs:
            para.remove(run_elem)

        insert_idx = insert_point
        for pr in pre_runs:
            para.insert(insert_idx, pr)
            insert_idx += 1

        para.insert(insert_idx, del_elem)
        insert_idx += 1

        if new_text:
            para.insert(insert_idx, ins_elem)
            insert_idx += 1

        for pr in post_runs:
            para.insert(insert_idx, pr)
            insert_idx += 1

        rev_id += 2
        text_diff = len(new_text) - len(old_text)
        offset += text_diff

    return len(matches), rev_id


def process_file(
    xml_path: Path,
    replacements: List[Tuple[str, str]],
    author: str,
    first_only: bool,
) -> int:
    """Process a single XML file for tracked replacements."""
    try:
        tree = etree.parse(str(xml_path))
    except etree.XMLSyntaxError:
        return 0

    root = tree.getroot()
    rev_id = _get_next_rev_id(root)
    date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = 0

    for old_text, new_text in replacements:
        for para in root.iter(_tag(W_NS, "p")):
            count, rev_id = replace_in_paragraph(
                para, old_text, new_text, author, date_str, rev_id, first_only
            )
            total += count
            if first_only and total > 0:
                break
        if first_only and total > 0:
            break

    if total > 0:
        tree.write(
            str(xml_path),
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        )

    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find-and-replace with Word tracked changes"
    )
    parser.add_argument("unpacked_dir", type=Path, help="Path to unpacked DOCX directory")
    parser.add_argument("--old", action="append", required=True, help="Text to find (repeatable)")
    parser.add_argument("--new", action="append", required=True, help="Replacement text (repeatable)")
    parser.add_argument("--first", action="store_true", help="Replace only first occurrence per pair")
    parser.add_argument("--author", default="AI-Agent", help="Author for tracked changes (default: AI-Agent)")

    args = parser.parse_args()

    if len(args.old) != len(args.new):
        print("Error: --old and --new must appear the same number of times", file=sys.stderr)
        sys.exit(1)

    unpacked = args.unpacked_dir
    if not unpacked.is_dir():
        print(f"Error: {unpacked} is not a directory", file=sys.stderr)
        sys.exit(1)

    replacements = list(zip(args.old, args.new))
    total = 0

    word_dir = unpacked / "word"
    if not word_dir.is_dir():
        print(f"Error: {word_dir} not found — is this an unpacked DOCX?", file=sys.stderr)
        sys.exit(1)

    for xml_file in sorted(word_dir.rglob("*.xml")):
        count = process_file(xml_file, replacements, args.author, args.first)
        if count > 0:
            print(f"  {xml_file.relative_to(unpacked)}: {count} replacement(s)")
            total += count

    print(f"\nTotal: {total} replacement(s) with author '{args.author}'")
    if total == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
