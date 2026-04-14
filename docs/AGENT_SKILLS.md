# Agent Skills Runtime

This document describes the agent skill dependencies embedded in the LibreCodeInterpreter Docker image to support specialized LibreChat agents.

## Overview

The image includes pre-installed skills, system binaries, and Python/Node packages required by the following LibreChat agents:

| Agent | Key Dependencies | Skills Directory |
|-------|-----------------|-----------------|
| Word DOCX "Complete" | LibreOffice, pandoc, python-docx, lxml | `/opt/skills/docx/` |
| Excel/XLSX | LibreOffice Calc, openpyxl, pandas | `/opt/skills/xlsx/` |
| PDF | qpdf, pdfplumber, pypdf, tesseract-ocr, poppler-utils | — |
| Quick Edits | ffmpeg, ffprobe, Pillow | — |
| Data Analysis & Visualization | pandas, numpy, matplotlib, seaborn, scipy | — |
| YouTube Assistant | No special system deps | — |

## Skills Directory

Skills are embedded in the image at `/opt/skills/` and mounted read-only inside nsjail sandboxes.

```
/opt/skills/
├── docx/
│   ├── SKILL.md
│   └── scripts/
│       ├── accept_changes.py     # Accept tracked changes via LibreOffice
│       ├── comment.py            # Add/reply comments in unpacked DOCX XML
│       ├── tracked_replace.py    # Find-and-replace with redline markup
│       ├── office/
│       │   ├── soffice.py        # LibreOffice helper (sandbox-aware)
│       │   ├── unpack.py         # Unpack DOCX/PPTX/XLSX to XML
│       │   ├── pack.py           # Repack XML to DOCX with validation
│       │   ├── validate.py       # XSD + redlining validation
│       │   ├── helpers/
│       │   │   ├── merge_runs.py
│       │   │   └── simplify_redlines.py
│       │   ├── validators/
│       │   │   ├── base.py
│       │   │   ├── docx.py
│       │   │   ├── pptx.py
│       │   │   └── redlining.py
│       │   └── schemas/          # XSD schemas (ISO 29500, ECMA, Microsoft)
│       └── templates/            # XML templates for comments
└── xlsx/
    ├── SKILL.md
    └── scripts/
        ├── recalc.py             # Formula recalculation via LibreOffice
        └── office/
            └── soffice.py        # LibreOffice helper
```

## Environment Variable

The `SKILLS_ROOT` environment variable is set to `/opt/skills` in all sandbox executions. Agents reference scripts via `$SKILLS_ROOT/docx/scripts/...`.

## System Dependencies Added for Agents

| Package | Purpose | Used By |
|---------|---------|---------|
| `libreoffice-writer` | DOCX conversion, accept tracked changes | Word, XLSX agents |
| `libreoffice-calc` | Formula recalculation | XLSX agent |
| `libreoffice-core`, `libreoffice-common` | Shared LibreOffice runtime | Word, XLSX agents |
| `fonts-liberation`, `fonts-dejavu-core`, `fonts-noto-core` | Document rendering fonts | Word, XLSX, PDF agents |
| `qpdf` | PDF manipulation, linearization, repair | PDF agent |
| `pdfplumber` (Python) | PDF table extraction | PDF agent |
| `pypdf` (Python) | Modern PDF library | PDF agent |
| `docx` (Node.js) | Word document generation from JS | Word agent |

Already present in base image: `pandoc`, `poppler-utils`, `tesseract-ocr`, `ffmpeg`, `python-docx`, `openpyxl`, `matplotlib`, `seaborn`, `pandas`, `numpy`, `scipy`, `Pillow`, `pdf-lib` (Node), `xlsx`/`exceljs` (Node).

## nsjail Sandbox Visibility

The following mounts are added to `docker/nsjail-base.cfg` to make skills and LibreOffice accessible inside sandboxes:

| Host Path | Sandbox Path | Access | Purpose |
|-----------|-------------|--------|---------|
| `/opt/skills` | `/opt/skills` | read-only | Skill scripts |
| `/etc/libreoffice` | `/etc/libreoffice` | read-only | LO configuration |
| `/etc/fonts` | `/etc/fonts` | read-only | Font configuration |
| `/usr/share/fonts` | `/usr/share/fonts` | read-only | Font files |

LibreOffice also needs a writable profile directory; the `soffice.py` helper creates one under `/tmp/` at runtime.

## LibreChat Configuration

Point LibreChat to this instance with these environment variables:

```env
# In LibreChat's .env
LIBRECHAT_CODE_API_KEY=your-api-key-here
LIBRECHAT_CODE_BASEURL=http://your-host:8000
```

## Smoke Tests

Run inside the container:

```bash
docker exec <container> bash /app/tests/smoke/test_agent_skills.sh
```

Tests verify: binary availability, Python/Node package imports, functional DOCX/XLSX/PDF/media operations, skills directory structure, and matplotlib headless rendering.

## tracked_replace.py

Custom implementation for Word tracked changes (redlines). Not part of the upstream Anthropic skill.

Usage:
```bash
python $SKILLS_ROOT/docx/scripts/tracked_replace.py UNPACKED_DIR \
    --old "original text" --new "replacement text" \
    [--old "another" --new "replacement"] \
    [--first] \
    [--author "AI-Agent"]
```

Features:
- Proper `<w:del>`/`<w:ins>` XML markup
- Preserves `<w:rPr>` formatting
- Multiple replacement pairs in one pass
- `--first` flag for single occurrence
- Configurable author (default: `AI-Agent`)
- Skips zones already under tracked changes
