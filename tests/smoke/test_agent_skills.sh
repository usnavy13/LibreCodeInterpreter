#!/usr/bin/env bash
# Smoke tests for agent skill dependencies.
# Run inside the Docker container: docker exec <container> bash /app/tests/smoke/test_agent_skills.sh
# Exit codes: 0 = all pass, 1 = failures detected
set -euo pipefail

PASS=0
FAIL=0
WARN=0

pass() { echo "  ✓ $1"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }
warn() { echo "  ⚠ $1"; WARN=$((WARN+1)); }

check_binary() {
    if command -v "$1" &>/dev/null; then pass "$1 found"; else fail "$1 NOT found"; fi
}

check_python() {
    if python3 -c "import $1" 2>/dev/null; then pass "python3: $1"; else fail "python3: $1 NOT importable"; fi
}

check_node() {
    if NODE_PATH="/usr/lib/node_modules:/usr/local/lib/node_modules" node -e "require('$1')" 2>/dev/null; then pass "node: $1"; else fail "node: $1 NOT importable"; fi
}

echo "=== System Binaries ==="
check_binary pandoc
check_binary soffice
check_binary qpdf
check_binary tesseract
check_binary pdftotext
check_binary pdftoppm
check_binary ffmpeg
check_binary ffprobe

echo ""
echo "=== LibreOffice ==="
if soffice --headless --version 2>/dev/null; then
    pass "soffice --headless --version"
else
    fail "soffice --headless --version"
fi

echo ""
echo "=== Python Packages (PDF) ==="
check_python pypdf
check_python pdfplumber
check_python pdf2image
check_python pytesseract
check_python PyPDF2
check_python pdfminer

echo ""
echo "=== Python Packages (Office) ==="
check_python docx
check_python openpyxl
check_python lxml

echo ""
echo "=== Python Packages (Data/Viz) ==="
check_python pandas
check_python numpy
check_python matplotlib
check_python seaborn
check_python scipy
check_python PIL

echo ""
echo "=== Python Packages (Media) ==="
check_python cv2

echo ""
echo "=== Python Packages (PPTX) ==="
check_python pptx
check_python markitdown

echo ""
echo "=== Node.js Packages ==="
check_node docx
check_node pdf-lib
check_node xlsx
check_node exceljs
check_node pptxgenjs

echo ""
echo "=== Skills Directory ==="
SKILLS_ROOT="${SKILLS_ROOT:-/opt/skills}"
if [ -d "$SKILLS_ROOT" ]; then
    pass "SKILLS_ROOT=$SKILLS_ROOT exists"
else
    fail "SKILLS_ROOT=$SKILLS_ROOT missing"
fi

for f in \
    docx/scripts/accept_changes.py \
    docx/scripts/comment.py \
    docx/scripts/tracked_replace.py \
    docx/scripts/office/soffice.py \
    docx/scripts/office/unpack.py \
    docx/scripts/office/pack.py \
    docx/scripts/office/validate.py \
    xlsx/scripts/recalc.py \
    xlsx/scripts/office/soffice.py \
    pptx/scripts/add_slide.py \
    pptx/scripts/clean.py \
    pptx/scripts/thumbnail.py \
    pptx/scripts/office/soffice.py; do
    if [ -f "$SKILLS_ROOT/$f" ]; then pass "$f"; else fail "$f missing"; fi
done

echo ""
echo "=== Functional: Matplotlib headless PNG ==="
if python3 -c "
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
fig, ax = plt.subplots()
ax.plot([1,2,3],[1,4,9])
buf = io.BytesIO()
fig.savefig(buf, format='png')
assert buf.tell() > 0
print('  PNG size:', buf.tell(), 'bytes')
" 2>/dev/null; then
    pass "matplotlib headless render"
else
    fail "matplotlib headless render"
fi

echo ""
echo "=== Functional: openpyxl create XLSX ==="
if python3 -c "
import openpyxl, tempfile, os
wb = openpyxl.Workbook()
ws = wb.active
ws['A1'] = 10
ws['B1'] = 20
ws['C1'] = '=A1+B1'
path = tempfile.mktemp(suffix='.xlsx')
wb.save(path)
assert os.path.getsize(path) > 0
print('  XLSX size:', os.path.getsize(path), 'bytes')
os.unlink(path)
" 2>/dev/null; then
    pass "openpyxl create XLSX with formula"
else
    fail "openpyxl create XLSX with formula"
fi

echo ""
echo "=== Functional: DOCX roundtrip (pandoc) ==="
if python3 -c "
import subprocess, tempfile, os
md = tempfile.mktemp(suffix='.md')
docx = tempfile.mktemp(suffix='.docx')
with open(md, 'w') as f:
    f.write('# Test\\n\\nHello world\\n')
r = subprocess.run(['pandoc', md, '-o', docx], capture_output=True)
assert r.returncode == 0
assert os.path.getsize(docx) > 0
print('  DOCX size:', os.path.getsize(docx), 'bytes')
os.unlink(md)
os.unlink(docx)
" 2>/dev/null; then
    pass "pandoc markdown→docx"
else
    fail "pandoc markdown→docx"
fi

echo ""
echo "=== Functional: tracked_replace.py import ==="
if python3 -c "
import sys; sys.path.insert(0, '${SKILLS_ROOT}/docx/scripts')
from tracked_replace import replace_in_paragraph
print('  Import OK')
" 2>/dev/null; then
    pass "tracked_replace.py importable"
else
    fail "tracked_replace.py importable"
fi

echo ""
echo "=== Functional: ffmpeg encode test ==="
if ffmpeg -f lavfi -i "sine=frequency=440:duration=1" -f wav -y /tmp/_smoke_test.wav </dev/null 2>/dev/null; then
    pass "ffmpeg audio encode"
    rm -f /tmp/_smoke_test.wav
else
    fail "ffmpeg audio encode"
fi

echo ""
echo "=== Functional: qpdf version ==="
if qpdf --version 2>/dev/null; then
    pass "qpdf operational"
else
    fail "qpdf operational"
fi

echo ""
echo "========================================="
echo "Results: $PASS passed, $FAIL failed, $WARN warnings"
echo "========================================="

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
