# Ralph Wiggum Prompt — Agent Test Runner & Auto-Fix

## CONTEXTE

Tu travailles sur le repo LibreCodeInterpreter (`/home/damien/LibreCodeInterpreter`), branche `feat/agent-skills-runtime`.

Ce repo est le runtime d'exécution de code pour 6 agents LibreChat (chat-dev.onbehalf.ai) :
- **DOCX** (`agent_docx_complete`) — création/édition de documents Word via templates OBA + scripts Python
- **PPTX** (`agent_pptx_complete`) — création de présentations PowerPoint via pptxgenjs + templates OBA
- **XLSX** (`agent_xlsx_complete`) — manipulation de fichiers Excel via openpyxl/pandas
- **PDF** (`agent_pdf_complete`) — manipulation de PDF + création via pipeline DOCX→PDF
- **FFmpeg** (`agent_quick_edits`) — manipulation de fichiers médias
- **DataViz** (`agent_data_viz`) — analyse de données et visualisation matplotlib/seaborn

Chaque agent a :
- Des **instructions** (system prompt) stockées en MongoDB ET dans `skills/<agent>/AGENT_INSTRUCTIONS.md`
- Des **scripts** dans `skills/<agent>/scripts/`
- Des **templates OBA** (On Behalf AI) dans `skills/<agent>/templates/onbehalfai/`
- Son code s'exécute dans un **sandbox nsjail** via le container `code-interpreter-api` (image `code-interpreter:agent-skills`)

Les agents sont appelables via l'**API Open Responses** de LibreChat :
- Endpoint : `http://127.0.0.1:3080/api/agents/v1/responses`
- Auth : `Bearer <API_KEY>` (lire la clé depuis `/home/damien/LibreCodeInterpreter/.agent-api-key`)
- Payload : `{"model": "<agent_id>", "input": "<prompt>", "stream": false}`

## TA MISSION

Exécuter les **17 tests automatisés** définis dans `tests/agent_api_tests.py`, analyser les résultats, et **corriger les problèmes** trouvés. Chaque itération Ralph doit progresser vers l'objectif : **17/17 tests PASS**.

## ÉTAPE 1 — Vérifier l'état actuel

Avant de lancer les tests, vérifie :

1. **Statut des containers** :
   ```bash
   docker ps --filter "name=code-interpreter-api" --format "{{.Names}} {{.Status}}"
   docker ps --filter "name=LibreChat-API" --format "{{.Names}} {{.Status}}"
   ```
   Les deux doivent être "healthy". Si non, attends ou relance.

2. **API key accessible** :
   ```bash
   cat /home/damien/LibreCodeInterpreter/.agent-api-key
   ```

3. **Tests exécutables** :
   ```bash
   AGENT_API_KEY=$(cat .agent-api-key) python3 tests/agent_api_tests.py --list
   ```
   Doit lister 17 tests.

4. **Vérifier les résultats précédents** (si c'est une itération Ralph suivante) :
   ```bash
   ls -lt tests/results/report_*.json | head -3
   cat tests/results/report_*.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Last run: {d[\"passed\"]}/{d[\"total\"]} passed, {d[\"failed\"]} failed, {d[\"errors\"]} errors')" 2>/dev/null
   ```

## ÉTAPE 2 — Exécuter les tests

Lance TOUS les tests :
```bash
cd /home/damien/LibreCodeInterpreter
AGENT_API_KEY=$(cat .agent-api-key) python3 tests/agent_api_tests.py 2>&1
```

Chaque test appelle un agent via l'API, attend sa réponse (jusqu'à 5 min), et vérifie :
- La réponse est `status: completed`
- Les **methodology patterns** attendus sont présents dans le code exécuté (ex: `fill_template.py` pour la création DOCX)
- Les **methodology antipatterns** sont absents (ex: `content.replace` au lieu de lxml)

## ÉTAPE 3 — Analyser les résultats

Pour chaque test **FAIL** ou **ERROR**, diagnostiquer la cause :

### Catégories de problèmes et comment les corriger

**A) L'agent n'utilise pas le bon outil** (FAIL — methodology pattern MISSING)
- Cause : les instructions agent ne sont pas assez directives
- Fix : modifier `skills/<agent>/AGENT_INSTRUCTIONS.md` + mettre à jour MongoDB :
  ```bash
  # Préparer la mise à jour MongoDB
  cat > /tmp/update_agent.js << 'EOF'
  const fs = require('fs');
  const instructions = fs.readFileSync('/home/damien/LibreCodeInterpreter/skills/<agent>/AGENT_INSTRUCTIONS.md', 'utf8');
  const escaped = JSON.stringify(instructions);
  const cmd = `db.agents.updateOne({id:"<agent_id>"},{$set:{instructions:${escaped},"versions.0.instructions":${escaped}}})`;
  fs.writeFileSync('/tmp/update_mongo.js', cmd);
  EOF
  node /tmp/update_agent.js
  docker exec -i chat-mongodb mongosh --quiet LibreChat < /tmp/update_mongo.js
  ```

**B) L'agent utilise un outil interdit** (FAIL — methodology antipattern VIOLATION)
- Cause : les instructions ne disent pas assez clairement de NE PAS utiliser tel outil
- Fix : ajouter une règle explicite dans AGENT_INSTRUCTIONS.md (ex: "JAMAIS python-docx pour la création")

**C) Le script échoue** (ERROR — l'agent a appelé le bon script mais il a planté)
- Cause : bug dans le script, mauvais arguments, template corrompu
- Fix : corriger le script dans `skills/<agent>/scripts/`, ou le template dans `skills/<agent>/templates/`
- Tester le fix en local avant de rebuild :
  ```bash
  # Tester un script directement dans le container
  docker exec code-interpreter-api python3 /opt/skills/docx/scripts/fill_template.py --help
  ```

**D) Le test lui-même est incorrect** (le pattern regex ne matche pas le bon format)
- Cause : le code de l'agent est correct mais le regex du test ne le capture pas
- Fix : modifier la regex dans `tests/agent_api_tests.py`

**E) L'agent timeout ou ne produit pas de fichier**
- Cause : le code est trop long, ou le chaînage n'est pas fait dans un seul bloc
- Fix : ajouter/renforcer la règle de chaînage dans les instructions

### Après chaque fix

1. Si tu as modifié un `AGENT_INSTRUCTIONS.md` → mettre à jour MongoDB (voir script ci-dessus)
2. Si tu as modifié un fichier dans `skills/` → rebuild + restart :
   ```bash
   cd /home/damien/LibreCodeInterpreter
   docker build --target app -t code-interpreter:agent-skills . 2>&1 | tail -3
   cd /home/damien/LibreChat
   docker compose -f deploy-compose.yml -f deploy-compose.override.yml -p librechat_clean up -d code-interpreter-api 2>&1
   cd /home/damien/LibreCodeInterpreter
   # Attendre healthy
   until docker ps --filter "name=code-interpreter-api" --format "{{.Status}}" | grep -q "healthy"; do sleep 5; done
   echo "HEALTHY"
   ```
3. Si tu as modifié `tests/agent_api_tests.py` → pas de rebuild nécessaire
4. **Commit** chaque fix :
   ```bash
   git add <fichiers modifiés>
   git commit -m "fix: <description>

   Generated with [Claude Code](https://claude.ai/code)
   via [Happy](https://happy.engineering)

   Co-Authored-By: Claude <noreply@anthropic.com>
   Co-Authored-By: Happy <yesreply@happy.engineering>"
   ```

## ÉTAPE 4 — Re-exécuter les tests qui ont échoué

Après les corrections, relance UNIQUEMENT les tests qui ont échoué :
```bash
AGENT_API_KEY=$(cat .agent-api-key) python3 tests/agent_api_tests.py D01b D02 P01  # IDs des tests qui ont échoué
```

## ÉTAPE 5 — Itérer jusqu'à 17/17

Répète les étapes 2-4 jusqu'à ce que TOUS les tests passent.

## RÈGLES IMPORTANTES

- **Ne modifie JAMAIS** les fichiers en dehors du repo LibreCodeInterpreter
- **Ne touche JAMAIS** directement à la base de données MongoDB autrement que via les scripts de mise à jour d'instructions
- **Ne redémarre JAMAIS** des containers autres que `code-interpreter-api`
- **pack.py** : syntaxe positionnelle `pack.py <dir/> <output.docx>` (PAS de `-o`)
- **Style IDs DOCX** : francisés (`Titre1`, `Titre2`, `Paragraphedeliste`, `PrformatHTML`, `Code`)
- **pptxgenjs** : couleurs hex sans "#", `bullet: true` pas de "•" unicode
- **Palette OBA** : navy `1C244B`, blue `2F5597`, orange `FB840D`, blueLight `DAE5EF`
- **{{current_user}}** : résolu par LibreChat dans les instructions, devient le vrai nom de l'utilisateur
- Les fichiers temporaires dans `/tmp/`, les sorties dans `/mnt/data/`
- Le sandbox a 120s de timeout max par exécution

## CRITÈRE DE COMPLÉTION

Quand les 17 tests sont PASS :
1. Push final : `git push origin feat/agent-skills-runtime`
2. Affiche le rapport final
3. Écris : `<promise>ALL 17 TESTS PASS</promise>`

## INFORMATIONS DE RÉFÉRENCE

### Agent IDs
| Agent | ID MongoDB | Instructions |
|-------|-----------|--------------|
| DOCX | `agent_docx_complete` | `skills/docx/AGENT_INSTRUCTIONS.md` |
| PPTX | `agent_pptx_complete` | `skills/pptx/AGENT_INSTRUCTIONS.md` |
| XLSX | `agent_xlsx_complete` | `skills/xlsx/AGENT_INSTRUCTIONS.md` |
| PDF | `agent_pdf_complete` | `skills/pdf/AGENT_INSTRUCTIONS.md` |
| FFmpeg | `agent_quick_edits` | `skills/ffmpeg/AGENT_INSTRUCTIONS.md` |
| DataViz | `agent_data_viz` | `skills/dataviz/AGENT_INSTRUCTIONS.md` |

### Scripts clés
| Script | Usage |
|--------|-------|
| `skills/docx/scripts/fill_template.py` | Création DOCX depuis template-base.docx |
| `skills/docx/scripts/fill_cr_template.py` | Création CR depuis template-compte-rendu.docx |
| `skills/docx/scripts/office/unpack.py` | Décompresser DOCX/PPTX/XLSX en XML |
| `skills/docx/scripts/office/pack.py` | Recompresser XML en DOCX/PPTX/XLSX |
| `skills/docx/scripts/office/validate.py` | Valider un fichier Office |
| `skills/docx/scripts/tracked_replace.py` | Remplacement avec tracked changes |
| `skills/docx/scripts/office/soffice.py` | Wrapper LibreOffice headless |
| `skills/pptx/scripts/thumbnail.py` | Grille thumbnails PPTX |
| `skills/pptx/scripts/add_slide.py` | Dupliquer/ajouter un slide |
| `skills/pptx/scripts/clean.py` | Nettoyer fichiers orphelins PPTX |
| `skills/xlsx/scripts/recalc.py` | Recalculer formules Excel via LibreOffice |

### Templates OBA
| Template | Contenu |
|----------|---------|
| `skills/docx/templates/onbehalfai/template-base.docx` | Cover page + version table + logo |
| `skills/docx/templates/onbehalfai/template-compte-rendu.docx` | Header + metadata + participants |
| `skills/pptx/templates/onbehalfai/template-oba.pptx` | 5 slides (titre, section, contenu, 2 colonnes, closing) |

### Problèmes connus (déjà corrigés — ne pas régresser)
- `pack.py` n'accepte PAS `-o` flag (arguments positionnels)
- `[trash]/0000.dat` dans les templates causait des erreurs de validation (supprimé)
- `ListParagraph` est en réalité `Paragraphedeliste` dans les templates français
- `HTMLPreformatted` est en réalité `PrformatHTML` dans les templates français
- Le style `Code` (blanc sur noir) est préféré à `PrformatHTML` pour les snippets
- `{{current_user}}` n'est résolu que dans les instructions, pas dans le code Python exécuté
- Les commentaires XML (`<!-- -->`) cassent la validation OOXML
- L'agent doit utiliser lxml (jamais `content.replace()`) pour manipuler le XML
- `NODE_PATH=/usr/lib/node_modules` nécessaire pour trouver pptxgenjs dans le container
