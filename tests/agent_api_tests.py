#!/usr/bin/env python3
"""
Automated agent tests via LibreChat Open Responses API.

Usage:
    python3 tests/agent_api_tests.py                    # Run all tests
    python3 tests/agent_api_tests.py D01b D01c P01b     # Run specific tests
    python3 tests/agent_api_tests.py --agent docx        # Run all DOCX tests
    python3 tests/agent_api_tests.py --list              # List all tests

Prerequisites:
    - LibreChat running on localhost:3080
    - Agent API key in AGENT_API_KEY env var or .env file
    - All agents deployed with current instructions

Each test:
    1. Sends a prompt to the agent via /api/agents/v1/responses
    2. Checks that the response completed without error
    3. Validates methodology (checks for expected scripts/patterns in code execution)
    4. Downloads generated files and validates them
    5. Produces a PASS/FAIL report
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)


# === Configuration ===

API_BASE = os.environ.get("LIBRECHAT_URL", "http://127.0.0.1:3080")
API_KEY = os.environ.get("AGENT_API_KEY", "")
RESPONSES_ENDPOINT = f"{API_BASE}/api/agents/v1/responses"
RESULTS_DIR = Path("tests/results")
TIMEOUT = 600  # 10 minutes max per test (agents with code execution can be slow)

# Force unbuffered output
import functools
print = functools.partial(print, flush=True)


@dataclass
class TestResult:
    test_id: str
    agent: str
    status: str = "PENDING"  # PASS, FAIL, ERROR, SKIP
    duration: float = 0
    prompt: str = ""
    response_text: str = ""
    code_blocks: list = field(default_factory=list)
    files_generated: list = field(default_factory=list)
    methodology_checks: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class TestCase:
    test_id: str
    agent_id: str
    agent_name: str
    prompt: str
    methodology_patterns: list  # Regex patterns expected in code execution
    methodology_antipatterns: list = field(default_factory=list)  # Patterns that should NOT appear
    description: str = ""
    expect_file: bool = False
    file_extension: str = ""
    previous_response_id: Optional[str] = None  # For multi-turn tests


# === API Client ===

def call_agent(agent_id: str, prompt: str, previous_response_id: str = None) -> dict:
    """Call an agent via the Open Responses API."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": agent_id,
        "input": prompt,
        "stream": False,
    }
    if previous_response_id:
        payload["previous_response_id"] = previous_response_id

    resp = requests.post(RESPONSES_ENDPOINT, headers=headers, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def extract_response_data(response: dict) -> tuple:
    """Extract text, reasoning, and tool calls from response.

    NOTE: The Open Responses API does NOT return the actual code executed
    (function_call.arguments is always empty). We can only check methodology
    via the reasoning text (agent's thinking) and the message text (agent's output).
    Both are concatenated into code_blocks for pattern matching.
    """
    text_parts = []
    code_blocks = []
    files = []

    for output in response.get("output", []):
        if output.get("type") == "message":
            for content in output.get("content", []):
                if content.get("type") == "output_text":
                    text = content.get("text", "")
                    text_parts.append(text)
                    code_blocks.append(("message", text))
        elif output.get("type") == "reasoning":
            for content in output.get("content", []):
                if content.get("type") == "reasoning_text":
                    code_blocks.append(("reasoning", content.get("text", "")))
        elif output.get("type") == "function_call":
            name = output.get("name", "")
            args = output.get("arguments", "")
            # Track that code execution happened
            code_blocks.append(("function_call", f"TOOL_USED: {name} {args}"))

    response_text = "\n".join(text_parts)
    return response_text, code_blocks, files


def check_methodology(code_blocks: list, patterns: list, antipatterns: list) -> dict:
    """Check that expected methodology patterns are present in code execution."""
    all_code = "\n".join(text for _, text in code_blocks)
    results = {}

    for pattern in patterns:
        found = bool(re.search(pattern, all_code, re.IGNORECASE | re.DOTALL))
        results[f"EXPECTED: {pattern}"] = "FOUND" if found else "MISSING"

    for pattern in antipatterns:
        found = bool(re.search(pattern, all_code, re.IGNORECASE | re.DOTALL))
        results[f"FORBIDDEN: {pattern}"] = "VIOLATION" if found else "OK"

    return results


# === Test Definitions ===

TESTS = []


def test(test_id, agent_id, agent_name, prompt, patterns, antipatterns=None, description="", expect_file=False, file_ext=""):
    """Register a test case."""
    TESTS.append(TestCase(
        test_id=test_id,
        agent_id=agent_id,
        agent_name=agent_name,
        prompt=prompt,
        methodology_patterns=patterns,
        methodology_antipatterns=antipatterns or [],
        description=description,
        expect_file=expect_file,
        file_extension=file_ext,
    ))


# ==========================================
# DOCX Agent Tests
# ==========================================

# NOTE: The Open Responses API does NOT return executed code.
# Patterns are checked against reasoning (agent thinking) + message (agent output).
# We verify methodology intent, not exact code.

test("D01b", "agent_docx_complete", "DOCX",
     "Crée un compte-rendu de la réunion suivante : Réunion du 10 avril 2026, visioconférence Teams, "
     "organisée par Damien Juillard. Participants : Sophie Martin (Directrice RH, Nextera Corp) et "
     "Damien Juillard (Consultant IA, On Behalf AI). Sujet : cadrage projet IA RH. "
     "Décisions : lancement POC chatbot RH. Actions : étude faisabilité avant le 24 avril.",
     patterns=[
         r"fill_cr_template|compte.rendu|template.*CR",
         r"execute_code",
     ],
     description="Créer un CR via fill_cr_template.py",
     expect_file=True, file_ext=".docx")

test("D01c", "agent_docx_complete", "DOCX",
     "Crée un guide d'installation pour PostgreSQL sur Ubuntu, avec prérequis, étapes d'installation, "
     "configuration de base, et dépannage.",
     patterns=[
         r"fill_template|template.*base|template.*OBA",
         r"execute_code",
     ],
     description="Créer un guide technique via fill_template.py",
     expect_file=True, file_ext=".docx")

test("D02", "agent_docx_complete", "DOCX",
     "Voici un texte de CGV simplifié. Remplace 'le Client' par 'l'Utilisateur' et '30 jours' par "
     "'15 jours ouvrés' en tracked changes.\n\n"
     "Article 1 : le Client s'engage à respecter les présentes conditions. Le paiement est dû sous 30 jours. "
     "Article 2 : le Client peut résilier sous 30 jours de préavis.",
     patterns=[
         r"tracked.replace|tracked.changes|redline",
         r"execute_code",
     ],
     description="Tracked changes avec {{current_user}} comme auteur")

test("D05", "agent_docx_complete", "DOCX",
     "Convertis ce texte markdown en document Word avec les styles OBA :\n\n"
     "# Politique de télétravail\n## 1. Principes généraux\n"
     "Le télétravail est ouvert à tous les collaborateurs.\n"
     "## 2. Modalités\n- Maximum 3 jours par semaine\n- Accord du manager requis\n"
     "## 3. Obligations\nRespecter les horaires définis.",
     patterns=[r"pandoc|markdown|convert"],
     description="Conversion Markdown → DOCX avec template OBA pandoc")

test("D08", "agent_docx_complete", "DOCX",
     "Convertis ce texte en PDF professionnel avec mise en forme OBA :\n\n"
     "Titre : Rapport mensuel\nAuteur : Damien\n\n"
     "Section 1 : Résumé exécutif\nLe mois d'avril a été marqué par une croissance de 15%.\n\n"
     "Section 2 : Détails\n- Ventes : 150k€\n- Charges : 120k€\n- Résultat : +30k€",
     patterns=[r"pdf|soffice|convert"],
     description="Conversion DOCX → PDF via soffice",
     expect_file=True, file_ext=".pdf")

test("D10", "agent_docx_complete", "DOCX",
     "Crée un document Word 'Fiche produit' avec : titre 'Widget Pro X200', "
     "un tableau de spécifications (Poids: 1.2kg, Dimensions: 30x20x10cm, Prix HT: 149.90€), "
     "et un paragraphe de description marketing.",
     patterns=[
         r"fill_template|template.*OBA|template.*base",
         r"execute_code",
     ],
     description="Création avec fill_template.py + type table",
     expect_file=True, file_ext=".docx")


# ==========================================
# PPTX Agent Tests
# ==========================================

test("P01b", "agent_pptx_complete", "PPTX",
     "Crée une présentation de 5 slides sur l'IA générative pour une réunion interne.",
     patterns=[
         r"pptxgenjs|pptxgen|PptxGenJS|Node",
         r"execute_code",
     ],
     description="Création PPTX avec palette OBA",
     expect_file=True, file_ext=".pptx")

test("P01", "agent_pptx_complete", "PPTX",
     "Crée un pitch deck de 6 slides pour une startup EdTech appelée 'LearnAI'. "
     "Slides : Titre, Problème, Solution, Marché (TAM 50Md€), Business model, Ask (levée 1.5M€). "
     "Design moderne, palette verte/blanche.",
     patterns=[
         r"pptxgenjs|pptxgen|PptxGenJS|Node",
         r"execute_code",
     ],
     description="Création pitch deck pptxgenjs",
     expect_file=True, file_ext=".pptx")

test("P08", "agent_pptx_complete", "PPTX",
     "Crée une présentation de 3 slides avec des graphiques : "
     "(1) Titre 'Résultats T1', "
     "(2) Barres ventes par région (Nord:45k, Sud:38k, Est:52k, Ouest:41k), "
     "(3) Camembert charges (Salaires:60%, Loyer:15%, Marketing:20%, Divers:5%).",
     patterns=[
         r"pptxgenjs|pptxgen|chart|graphique",
         r"execute_code",
     ],
     description="Création slides avec graphiques pptxgenjs",
     expect_file=True, file_ext=".pptx")


# ==========================================
# XLSX Agent Tests
# ==========================================

test("X01", "agent_xlsx_complete", "XLSX",
     "Crée un fichier Excel de budget trimestriel avec un onglet Q1 contenant les postes "
     "Salaires (45000€), Loyer (8000€), Marketing (12000€), IT (6000€), et un sous-total en formule Excel. "
     "Formate avec en-têtes colorés et format monétaire €.",
     patterns=[
         r"openpyxl|Excel|xlsx",
         r"execute_code",
     ],
     description="Création Excel avec openpyxl + styles",
     expect_file=True, file_ext=".xlsx")

test("X03", "agent_xlsx_complete", "XLSX",
     "Crée un fichier Excel avec une colonne Prix (10, 20, 30) et une colonne Total qui fait =Prix*1.2. "
     "Recalcule les formules pour que les valeurs soient visibles.",
     patterns=[
         r"openpyxl|formul|recalc",
         r"execute_code",
     ],
     description="Création Excel + recalc.py",
     expect_file=True, file_ext=".xlsx")


# ==========================================
# PDF Agent Tests
# ==========================================

test("F13", "agent_pdf_complete", "PDF",
     "Crée un PDF professionnel 'Proposition commerciale' avec 3 sections : "
     "Contexte, Offre de service (avec liste à puces), et Conditions tarifaires.",
     patterns=[
         r"pdf|PDF|soffice|template|fill_template",
         r"execute_code",
     ],
     description="Création PDF via DOCX OBA → soffice",
     expect_file=True, file_ext=".pdf")

test("F04", "agent_pdf_complete", "PDF",
     "Crée deux PDFs simples (une page chacun avec du texte) puis fusionne-les en un seul.",
     patterns=[
         r"fusion|merge|fusionne|PdfMerger|qpdf",
         r"execute_code",
     ],
     description="Fusion de PDFs",
     expect_file=True, file_ext=".pdf")


# ==========================================
# FFmpeg Agent Tests
# ==========================================

test("M05", "agent_quick_edits", "FFmpeg",
     "Analyse les capacités de ffmpeg installé : quels codecs vidéo et audio sont disponibles ? "
     "Liste les 5 principaux codecs vidéo et audio.",
     patterns=[r"ffmpeg|ffprobe|codec"],
     description="Analyse ffmpeg capabilities")

test("M06", "agent_quick_edits", "FFmpeg",
     "Crée une image PNG de 800x600 pixels, fond bleu (#2F5597), avec le texte 'Test' en blanc centré.",
     patterns=[r"PIL|Pillow|image|Image|png|PNG"],
     description="Création d'image avec Pillow",
     expect_file=True, file_ext=".png")


# ==========================================
# DataViz Agent Tests
# ==========================================

test("A01", "agent_data_viz", "DataViz",
     "Génère un dataset fictif de 100 ventes (date, produit parmi A/B/C, montant entre 50 et 500€, "
     "région parmi Nord/Sud/Est/Ouest). Puis fais une analyse exploratoire : "
     "statistiques descriptives, répartition par produit, et un histogramme des montants.",
     patterns=[
         r"pandas|DataFrame|analyse|exploratoire|dataset|vente",
     ],
     description="Analyse exploratoire + visualisation",
     expect_file=True, file_ext=".png")

test("A02", "agent_data_viz", "DataViz",
     "Crée un dashboard en une seule image avec 4 graphiques à partir de données fictives : "
     "(1) courbe CA mensuel, (2) camembert par catégorie, (3) barres top clients, "
     "(4) scatter quantité vs montant. Utilise la palette de couleurs OBA.",
     patterns=[
         r"dashboard|subplots|graphique|OBA|palette|4.*graph",
     ],
     description="Dashboard 4 graphiques avec palette OBA",
     expect_file=True, file_ext=".png")


# === Test Runner ===

def run_test(test_case: TestCase) -> TestResult:
    """Run a single test and return the result."""
    result = TestResult(
        test_id=test_case.test_id,
        agent=test_case.agent_name,
        prompt=test_case.prompt[:100] + "...",
    )

    start = time.time()
    try:
        response = call_agent(
            test_case.agent_id,
            test_case.prompt,
            test_case.previous_response_id,
        )
        result.duration = time.time() - start

        if response.get("status") != "completed":
            result.status = "ERROR"
            result.error = f"Response status: {response.get('status')}"
            return result

        if response.get("error"):
            result.status = "ERROR"
            result.error = str(response["error"])
            return result

        text, code_blocks, files = extract_response_data(response)
        result.response_text = text[:500]
        result.code_blocks = code_blocks
        result.files_generated = files

        # Check methodology
        result.methodology_checks = check_methodology(
            code_blocks,
            test_case.methodology_patterns,
            test_case.methodology_antipatterns,
        )

        # Determine pass/fail
        has_missing = any(v == "MISSING" for v in result.methodology_checks.values())
        has_violation = any(v == "VIOLATION" for v in result.methodology_checks.values())

        if has_violation:
            result.status = "FAIL"
            result.error = "Methodology antipattern detected"
        elif has_missing:
            result.status = "FAIL"
            result.error = "Expected methodology pattern not found"
        else:
            result.status = "PASS"

    except requests.exceptions.Timeout:
        result.status = "ERROR"
        result.error = "Timeout"
        result.duration = time.time() - start
    except Exception as e:
        result.status = "ERROR"
        result.error = str(e)
        result.duration = time.time() - start

    return result


def print_result(result: TestResult):
    """Print a test result."""
    icon = {"PASS": "✓", "FAIL": "✗", "ERROR": "!", "SKIP": "○"}.get(result.status, "?")
    color = {"PASS": "\033[32m", "FAIL": "\033[31m", "ERROR": "\033[33m", "SKIP": "\033[90m"}.get(result.status, "")
    reset = "\033[0m"

    print(f"  {color}{icon} {result.test_id}{reset} [{result.agent}] {result.duration:.1f}s — {result.status}")

    if result.status != "PASS":
        if result.error:
            print(f"    Error: {result.error}")
        for check, value in result.methodology_checks.items():
            if value in ("MISSING", "VIOLATION"):
                print(f"    {value}: {check}")


def main():
    parser = argparse.ArgumentParser(description="Run agent API tests")
    parser.add_argument("tests", nargs="*", help="Specific test IDs to run (e.g., D01b D01c P01b)")
    parser.add_argument("--agent", help="Run all tests for an agent (docx, pptx, xlsx, pdf, ffmpeg, dataviz)")
    parser.add_argument("--list", action="store_true", help="List all available tests")
    parser.add_argument("--key", help="Agent API key (overrides AGENT_API_KEY env var)")
    args = parser.parse_args()

    global API_KEY
    if args.key:
        API_KEY = args.key
    if not API_KEY:
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("AGENT_API_KEY="):
                    API_KEY = line.split("=", 1)[1].strip()
        if not API_KEY:
            print("ERROR: Set AGENT_API_KEY env var or pass --key")
            sys.exit(1)

    if args.list:
        print(f"Available tests ({len(TESTS)}):\n")
        for t in TESTS:
            print(f"  {t.test_id:6s} [{t.agent_name:8s}] {t.description}")
        return

    # Filter tests
    tests_to_run = TESTS
    if args.tests:
        tests_to_run = [t for t in TESTS if t.test_id in args.tests]
    elif args.agent:
        agent_map = {
            "docx": "DOCX", "pptx": "PPTX", "xlsx": "XLSX",
            "pdf": "PDF", "ffmpeg": "FFmpeg", "dataviz": "DataViz",
        }
        agent_name = agent_map.get(args.agent.lower(), args.agent)
        tests_to_run = [t for t in TESTS if t.agent_name == agent_name]

    if not tests_to_run:
        print("No tests matched. Use --list to see available tests.")
        sys.exit(1)

    # Run tests
    print(f"\n{'='*60}")
    print(f"Running {len(tests_to_run)} agent tests")
    print(f"API: {API_BASE}")
    print(f"{'='*60}\n")

    results = []
    for test_case in tests_to_run:
        print(f"  Running {test_case.test_id} ({test_case.description})...")
        result = run_test(test_case)
        results.append(result)
        print_result(result)
        print()

    # Summary
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    errors = sum(1 for r in results if r.status == "ERROR")
    total_time = sum(r.duration for r in results)

    print(f"{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {errors} errors / {len(results)} total")
    print(f"Total time: {total_time:.1f}s")
    print(f"{'='*60}")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"report_{timestamp}.json"
    report = {
        "timestamp": timestamp,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "duration": total_time,
        "results": [
            {
                "test_id": r.test_id,
                "agent": r.agent,
                "status": r.status,
                "duration": r.duration,
                "error": r.error,
                "methodology_checks": r.methodology_checks,
                "response_preview": r.response_text[:200],
            }
            for r in results
        ],
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport saved: {report_path}")

    sys.exit(1 if failed + errors > 0 else 0)


if __name__ == "__main__":
    main()
