# Guide d'installation — LibreCodeInterpreter Agent Runtime

Ce guide permet de déployer LibreCodeInterpreter avec le runtime enrichi pour les 6 agents LibreChat (DOCX, PPTX, XLSX, PDF, FFmpeg, DataViz) sur un serveur où LibreChat est déjà en place.

## Prérequis

- **LibreChat** déjà installé et fonctionnel (avec MongoDB, API, NGINX)
- **Docker** 24.0+ avec Docker Compose v2.20+
- **Git** configuré avec accès au repo `On-Behalf-AI/LibreCodeInterpreter`
- **~10 Go** d'espace disque pour l'image Docker (~8.8 Go)
- Le serveur doit avoir les ports suivants libres en local :
  - `8010` (code-interpreter API, bind sur 127.0.0.1)
  - Redis et MinIO internes au compose

## Étape 1 — Cloner le repo et basculer sur la branche

```bash
cd /home/damien  # ou votre répertoire de travail
git clone https://github.com/On-Behalf-AI/LibreCodeInterpreter.git
cd LibreCodeInterpreter
git checkout feat/agent-skills-runtime
git pull origin feat/agent-skills-runtime
```

## Étape 2 — Configurer le .env

Créer `/home/damien/LibreCodeInterpreter/.env` :

```bash
cat > .env << 'EOF'
# Code Interpreter API Configuration

# ── Authentication ──────────────────────────────────────────────
API_KEY=<GÉNÉRER_AVEC: openssl rand -hex 32>

# ── Redis ───────────────────────────────────────────────────────
REDIS_HOST=code-interpreter-redis
REDIS_PORT=6379

# ── MinIO / S3 ─────────────────────────────────────────────────
MINIO_ENDPOINT=code-interpreter-minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin

# ── Sandbox Pool ────────────────────────────────────────────────
SANDBOX_POOL_ENABLED=true
REPL_ENABLED=true

# ── Sandbox Network ──────────────────────────────────────────
ENABLE_SANDBOX_NETWORK=false

# ── Sandbox Timeout ──────────────────────────────────────────
MAX_EXECUTION_TIME=120

# ── Logging ────────────────────────────────────────────────────
LOG_LEVEL=info
LOG_FORMAT=json
EOF
```

> **IMPORTANT** : Le `API_KEY` ici est celui que LibreChat utilise pour communiquer avec le code-interpreter. Il doit correspondre à la configuration de LibreChat.

## Étape 3 — Build de l'image Docker

```bash
docker build --target app -t code-interpreter:agent-skills .
```

> **Durée** : 30-45 min la première fois (téléchargement de LibreOffice, Node.js packages, Python packages). Les builds suivants sont rapides (~2-5s) grâce au cache Docker — seul le layer `COPY skills/` est recalculé.

> **Si le build échoue** sur `apt-get update` : les mirrors Ubuntu peuvent être en sync. Relancez le build après quelques minutes.

## Étape 4 — Intégrer dans le compose LibreChat

Ajouter dans le `deploy-compose.override.yml` de LibreChat (typiquement `/home/damien/LibreChat/deploy-compose.override.yml`) :

```yaml
services:
  code-interpreter-api:
    build: /home/damien/LibreCodeInterpreter
    container_name: code-interpreter-api
    image: code-interpreter:agent-skills
    init: true
    privileged: true
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 1G
    env_file:
      - /home/damien/LibreCodeInterpreter/.env
    environment:
      - REDIS_HOST=code-interpreter-redis
      - MINIO_ENDPOINT=code-interpreter-minio:9000
    healthcheck:
      test: ["CMD-SHELL", "curl -fs http://localhost:8000/health"]
      interval: 30s
      timeout: 15s
      retries: 3
      start_period: 30s
    ports:
      - 127.0.0.1:8010:8000
    tmpfs:
      - /app/data:size=100m
    volumes:
      - code-interpreter-sandbox-data:/var/lib/code-interpreter/sandboxes
      - /home/damien/LibreCodeInterpreter/ssl:/app/ssl:ro
    depends_on:
      code-interpreter-minio-init:
        condition: service_completed_successfully
      code-interpreter-redis:
        condition: service_healthy
    networks:
      - default

  code-interpreter-redis:
    image: redis:7-alpine
    container_name: code-interpreter-redis
    restart: unless-stopped
    command: >
      redis-server --appendonly yes --appendfsync everysec
      --maxmemory 256mb --maxmemory-policy allkeys-lru
    deploy:
      resources:
        limits:
          memory: 256M
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    volumes:
      - code-interpreter-redis-data:/data

  code-interpreter-minio:
    image: minio/minio:latest
    container_name: code-interpreter-minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    deploy:
      resources:
        limits:
          memory: 256M
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 5
    volumes:
      - code-interpreter-minio-data:/data

  code-interpreter-minio-init:
    image: minio/mc:latest
    depends_on:
      code-interpreter-minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set myminio http://code-interpreter-minio:9000 minioadmin minioadmin;
      mc mb --ignore-existing myminio/code-interpreter-files;
      exit 0;
      "

volumes:
  code-interpreter-sandbox-data:
  code-interpreter-redis-data:
  code-interpreter-minio-data:
```

## Étape 5 — Configurer LibreChat pour utiliser le code-interpreter

Dans `librechat.yaml` de LibreChat, s'assurer que le `codeInterpreter` est configuré :

```yaml
# Dans la section endpoints ou interface
codeInterpreter:
  url: http://code-interpreter-api:8000
  apiKey: <MÊME_CLÉ_QUE_DANS_LE_.ENV>
```

> Vérifier que l'URL correspond au nom du container (`code-interpreter-api`) et au port interne (`8000`).

## Étape 6 — Lancer les services

```bash
cd /home/damien/LibreChat
docker compose -f deploy-compose.yml -f deploy-compose.override.yml -p librechat_clean up -d code-interpreter-api
```

Vérifier que le container est healthy :

```bash
docker ps --filter "name=code-interpreter-api" --format "{{.Names}} {{.Status}}"
# Attendu : code-interpreter-api Up X seconds (healthy)
```

Tester le health check :

```bash
curl -s http://127.0.0.1:8010/health
# Attendu : {"status":"healthy","version":"1.2.0","service":"code-interpreter-api"}
```

## Étape 7 — Créer les 6 agents dans LibreChat

Les agents sont stockés dans MongoDB. Exécuter le script suivant dans le container MongoDB :

```bash
docker exec -i chat-mongodb mongosh --quiet LibreChat << 'MONGOEOF'

// === Agent 1 : Word DOCX Complete ===
db.agents.insertOne({
  id: "agent_docx_complete",
  name: "Word DOCX Complete",
  description: "Création, édition et manipulation de documents Word avec tracked changes, comments, et conversion PDF.",
  provider: "anthropic",
  model: "claude-sonnet-4.5",
  category: "documents",
  tools: ["execute_code"],
  model_parameters: {},
  recursion_limit: 25,
  artifacts: "enabled",
  hide_sequential_outputs: false,
  end_after_tools: false,
  conversation_starters: [
    "Crée un document Word professionnel",
    "Modifie ce DOCX avec tracked changes",
    "Convertis ce document en PDF",
    "Ajoute des commentaires à ce DOCX"
  ],
  author: "<USER_ID>",
  authorName: "<VOTRE_NOM>",
  instructions: "",
  projectIds: [],
  versions: [],
  createdAt: new Date(),
  updatedAt: new Date(),
  is_promoted: false,
  mcpServerNames: [],
  support_contact: { name: "", email: "" }
});

// === Agent 2 : PowerPoint PPTX ===
db.agents.insertOne({
  id: "agent_pptx_complete",
  name: "PowerPoint PPTX",
  description: "Création et édition de présentations PowerPoint professionnelles.",
  provider: "anthropic",
  model: "claude-sonnet-4.5",
  category: "documents",
  tools: ["execute_code"],
  model_parameters: {},
  recursion_limit: 25,
  artifacts: "enabled",
  hide_sequential_outputs: false,
  end_after_tools: false,
  conversation_starters: [
    "Crée une présentation PowerPoint",
    "Édite ce PPTX existant",
    "Génère des slides à partir de ce contenu",
    "Analyse ce template PPTX"
  ],
  author: "<USER_ID>",
  authorName: "<VOTRE_NOM>",
  instructions: "",
  projectIds: [],
  versions: [],
  createdAt: new Date(),
  updatedAt: new Date(),
  is_promoted: false,
  mcpServerNames: [],
  support_contact: { name: "", email: "" }
});

// === Agent 3 : Excel XLSX ===
db.agents.insertOne({
  id: "agent_xlsx_complete",
  name: "Excel XLSX",
  description: "Manipulation de fichiers Excel : création, analyse, formules, graphiques.",
  provider: "anthropic",
  model: "claude-sonnet-4.5",
  category: "documents",
  tools: ["execute_code"],
  model_parameters: {},
  recursion_limit: 25,
  artifacts: "enabled",
  hide_sequential_outputs: false,
  end_after_tools: false,
  conversation_starters: [
    "Crée un tableau Excel avec formules",
    "Analyse ce fichier Excel",
    "Ajoute un graphique à ce XLSX",
    "Recalcule les formules de ce fichier"
  ],
  author: "<USER_ID>",
  authorName: "<VOTRE_NOM>",
  instructions: "",
  projectIds: [],
  versions: [],
  createdAt: new Date(),
  updatedAt: new Date(),
  is_promoted: false,
  mcpServerNames: [],
  support_contact: { name: "", email: "" }
});

// === Agent 4 : PDF ===
db.agents.insertOne({
  id: "agent_pdf_complete",
  name: "PDF",
  description: "Manipulation de PDFs : extraction, fusion, OCR, conversion, watermark.",
  provider: "anthropic",
  model: "claude-sonnet-4.5",
  category: "documents",
  tools: ["execute_code"],
  model_parameters: {},
  recursion_limit: 25,
  artifacts: "enabled",
  hide_sequential_outputs: false,
  end_after_tools: false,
  conversation_starters: [
    "Extrais le texte de ce PDF",
    "Fusionne ces fichiers PDF",
    "Extrais les tableaux de ce PDF",
    "Fais un OCR sur ce PDF scanné"
  ],
  author: "<USER_ID>",
  authorName: "<VOTRE_NOM>",
  instructions: "",
  projectIds: [],
  versions: [],
  createdAt: new Date(),
  updatedAt: new Date(),
  is_promoted: false,
  mcpServerNames: [],
  support_contact: { name: "", email: "" }
});

// === Agent 5 : Quick Edits (FFmpeg) ===
db.agents.insertOne({
  id: "agent_quick_edits",
  name: "Quick Edits (FFmpeg)",
  description: "Manipulation de fichiers médias : conversion vidéo, extraction audio, redimensionnement images.",
  provider: "anthropic",
  model: "claude-sonnet-4.5",
  category: "media",
  tools: ["execute_code"],
  model_parameters: {},
  recursion_limit: 25,
  artifacts: "enabled",
  hide_sequential_outputs: false,
  end_after_tools: false,
  conversation_starters: [
    "Convertis cette vidéo en MP4",
    "Extrais l'audio de cette vidéo",
    "Redimensionne cette image",
    "Découpe cette vidéo de 0:30 à 1:45"
  ],
  author: "<USER_ID>",
  authorName: "<VOTRE_NOM>",
  instructions: "",
  projectIds: [],
  versions: [],
  createdAt: new Date(),
  updatedAt: new Date(),
  is_promoted: false,
  mcpServerNames: [],
  support_contact: { name: "", email: "" }
});

// === Agent 6 : Data Analysis & Visualization ===
db.agents.insertOne({
  id: "agent_data_viz",
  name: "Data Analysis & Visualization",
  description: "Analyse de données et visualisation : pandas, matplotlib, seaborn, sklearn.",
  provider: "google",
  model: "gemini-2.5-pro",
  category: "analysis",
  tools: ["execute_code"],
  model_parameters: { temperature: 0.4 },
  recursion_limit: 25,
  artifacts: "enabled",
  hide_sequential_outputs: false,
  end_after_tools: false,
  conversation_starters: [
    "Analyse ce fichier CSV",
    "Crée un graphique à partir de ces données",
    "Fais une analyse statistique de ce dataset",
    "Génère un dashboard de visualisation"
  ],
  author: "<USER_ID>",
  authorName: "<VOTRE_NOM>",
  instructions: "",
  projectIds: [],
  versions: [],
  createdAt: new Date(),
  updatedAt: new Date(),
  is_promoted: false,
  mcpServerNames: [],
  support_contact: { name: "", email: "" }
});

print("6 agents created successfully");
MONGOEOF
```

> **IMPORTANT** : Remplacer `<USER_ID>` par votre ObjectId utilisateur MongoDB (trouvable avec `db.users.findOne({name: "VotreNom"})._id`) et `<VOTRE_NOM>` par votre nom d'affichage.

## Étape 8 — Injecter les instructions des agents

Les instructions sont stockées dans les fichiers `AGENT_INSTRUCTIONS.md` du repo. Les injecter dans MongoDB :

```bash
cd /home/damien/LibreCodeInterpreter

# Script d'injection pour les 6 agents
for agent_dir in "docx:agent_docx_complete" "pptx:agent_pptx_complete" "xlsx:agent_xlsx_complete" "pdf:agent_pdf_complete" "ffmpeg:agent_quick_edits" "dataviz:agent_data_viz"; do
    DIR="${agent_dir%%:*}"
    AGENT_ID="${agent_dir##*:}"
    FILE="skills/$DIR/AGENT_INSTRUCTIONS.md"
    
    if [ -f "$FILE" ]; then
        cat > /tmp/update_agent.js << JSEOF
const fs = require('fs');
const instructions = fs.readFileSync('/home/damien/LibreCodeInterpreter/$FILE', 'utf8');
const escaped = JSON.stringify(instructions);
const cmd = 'db.agents.updateOne({id:"$AGENT_ID"},{\\$set:{instructions:' + escaped + ',"versions.0.instructions":' + escaped + '}})';
fs.writeFileSync('/tmp/update_mongo.js', cmd);
JSEOF
        node /tmp/update_agent.js
        docker exec -i chat-mongodb mongosh --quiet LibreChat < /tmp/update_mongo.js
        echo "✓ $AGENT_ID updated from $FILE"
    else
        echo "✗ $FILE not found"
    fi
done
```

## Étape 9 — Migrer les permissions des agents

Si LibreChat requiert une migration des permissions pour les agents :

```bash
# Vérifier si le script existe dans votre version de LibreChat
ls /home/damien/LibreChat/api/config/migrate-agent-permissions.js 2>/dev/null

# Si oui, l'exécuter dans le container API
docker exec LibreChat-API node /app/api/config/migrate-agent-permissions.js
```

## Étape 10 — Vérification

### Vérifier les containers

```bash
docker ps --filter "name=code-interpreter" --format "table {{.Names}}\t{{.Status}}"
```

Attendu :
```
NAMES                     STATUS
code-interpreter-api      Up X minutes (healthy)
code-interpreter-redis    Up X minutes (healthy)
code-interpreter-minio    Up X minutes (healthy)
```

### Vérifier les agents dans LibreChat

```bash
docker exec chat-mongodb mongosh --quiet --eval "
db.agents.find({id: /^agent_/}, {id:1, name:1, instructions:1}).forEach(a => {
    print(a.id + ' | ' + a.name + ' | instructions: ' + (a.instructions || '').length + ' chars');
})
" LibreChat
```

Attendu : 6 agents avec des instructions de 2000-20000 chars chacun.

### Tester un agent via l'API

```bash
# Créer une API key pour les tests
docker exec LibreChat-API node -e '
const mongoose = require("mongoose");
const { agentApiKeySchema, createMethods } = require("@librechat/data-schemas");
async function main() {
    await mongoose.connect(process.env.MONGO_URI || "mongodb://chat-mongodb:27017/LibreChat");
    if (!mongoose.models.AgentApiKey) mongoose.model("AgentApiKey", agentApiKeySchema);
    const methods = createMethods(mongoose);
    const result = await methods.createAgentApiKey({userId: "<USER_ID>", name: "test"});
    console.log("API Key: " + result.key);
    await mongoose.disconnect();
}
main().catch(console.error);
'

# Tester un appel agent
curl -s -X POST http://127.0.0.1:3080/api/agents/v1/responses \
  -H "Authorization: Bearer <API_KEY_CI_DESSUS>" \
  -H "Content-Type: application/json" \
  -d '{"model": "agent_docx_complete", "input": "Dis bonjour.", "stream": false}' \
  | python3 -m json.tool | head -20
```

### Tester via l'interface web

Ouvrir `https://<votre-domaine>/c/new?agent_id=agent_docx_complete` et demander : "Crée un guide d'installation Docker".

---

## Mise à jour ultérieure

Pour mettre à jour après des modifications sur la branche :

```bash
cd /home/damien/LibreCodeInterpreter
git pull origin feat/agent-skills-runtime

# Rebuild l'image (rapide si seul skills/ a changé)
docker build --target app -t code-interpreter:agent-skills .

# Restart le container
cd /home/damien/LibreChat
docker compose -f deploy-compose.yml -f deploy-compose.override.yml \
    -p librechat_clean up -d code-interpreter-api

# Si les instructions ont changé, ré-injecter dans MongoDB
cd /home/damien/LibreCodeInterpreter
# (relancer le script d'injection de l'étape 8)
```

## Personnalisation des templates

### Remplacer le template OBA par celui d'un client

```bash
# DOCX : copier les templates du client
cp client_template.docx skills/docx/templates/<client>/template-base.docx
cp client_cr.docx skills/docx/templates/<client>/template-compte-rendu.docx
cp client_logo.png skills/docx/templates/<client>/logo.png

# PPTX : copier le template du client
cp client_template.pptx skills/pptx/templates/<client>/template-corporate.pptx

# Adapter les AGENT_INSTRUCTIONS.md pour pointer vers les templates du client
# Puis rebuild + ré-injecter instructions
```

### Structure des templates

```
skills/
├── docx/templates/
│   ├── onbehalfai/          # Templates On Behalf AI (par défaut)
│   │   ├── template-base.docx
│   │   ├── template-compte-rendu.docx
│   │   └── logo-onbehalfai.png
│   └── <client>/            # Templates client (optionnel)
│       ├── template-base.docx
│       └── logo.png
└── pptx/templates/
    ├── onbehalfai/
    │   ├── template-oba-corporate.pptx  # 50 layouts
    │   └── TEMPLATE_REFERENCE.md
    └── <client>/
        └── template-corporate.pptx
```

## Résolution de problèmes

### Le build Docker échoue sur apt-get

```
E: Failed to fetch http://archive.ubuntu.com/... Mirror sync in progress?
```
→ Relancer le build après quelques minutes. Les mirrors Ubuntu ont des fenêtres de sync.

### L'agent ne génère pas de fichier

→ Vérifier que `model_parameters` est `{}` (pas de `temperature`) pour les agents Claude Sonnet 4.5 (thinking mode requiert pas de temperature).

### Le container n'est pas healthy

```bash
docker logs code-interpreter-api --tail 50
```
→ Vérifier que Redis et MinIO sont accessibles, que les ports ne sont pas en conflit.

### Les tracked changes montrent "AI-Agent" au lieu du nom de l'utilisateur

→ Les instructions doivent contenir `{{current_user}}` qui est résolu par LibreChat. Vérifier que les instructions sont à jour dans MongoDB.

### Le PPTX généré a des slides vides

→ `add_slide.py` crée des slides sans contenu. Utiliser `create_from_template.py` qui copie les placeholders du layout ET insère le sldId dans presentation.xml.
