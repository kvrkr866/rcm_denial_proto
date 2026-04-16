# Deployment Guide — RCM Denial Management

## Prerequisites — Install Checklist

### System Packages

| Package | Required? | Install (Linux) | Install (macOS) | Purpose |
|---------|-----------|-----------------|-----------------|---------|
| **Python 3.11+** | Required | `sudo apt install python3.11 python3.11-venv python3-pip` | `brew install python@3.11` | Runtime |
| **tesseract-ocr** | Optional | `sudo apt install tesseract-ocr` | `brew install tesseract` | Scanned PDF OCR fallback (PyMuPDF handles digital PDFs without this) |
| **poppler-utils** | Optional | `sudo apt install poppler-utils` | `brew install poppler` | PDF-to-image conversion (only needed if tesseract is used) |
| **Docker** | Optional | [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) | `brew install docker` | Container deployment |

**Note:** `tesseract-ocr` and `poppler-utils` are only needed if you have **scanned** (image-only) EOB PDFs. Most EOBs are digital PDFs — PyMuPDF handles those without any system packages.

### Python Packages

All Python dependencies are listed in `requirements.txt` and `pyproject.toml`. Install with ONE of:

```bash
# Option A: requirements.txt (explicit)
pip install -r requirements.txt

# Option B: pyproject.toml editable install (recommended for development)
pip install -e "."            # core + web UI
pip install -e ".[dev]"       # + pytest, ruff, mypy
pip install -e ".[all]"       # everything
```

Key packages installed:

| Category | Packages |
|----------|----------|
| **AI Pipeline** | langgraph, langchain, langchain-openai, langchain-community, langchain-chroma, chromadb, openai |
| **Data Models** | pydantic, pydantic-settings, python-dotenv |
| **PDF & OCR** | PyMuPDF (primary OCR), pytesseract (fallback), pdf2image, pypdf, reportlab, fpdf2, Pillow |
| **Web UI** | nicegui |
| **CLI** | click, rich, structlog |
| **Networking** | httpx, tenacity |
| **Data** | pandas |
| **Dev/Test** | pytest, pytest-asyncio, pytest-cov, ruff, mypy |

### Verify Installation

```bash
# Check all critical imports
python -c "
import langgraph; print(f'langgraph {langgraph.__version__}')
import langchain; print(f'langchain OK')
import chromadb; print(f'chromadb {chromadb.__version__}')
import pydantic; print(f'pydantic {pydantic.__version__}')
import fitz; print(f'PyMuPDF {fitz.__doc__}')
import nicegui; print(f'nicegui {nicegui.__version__}')
import click; print(f'click {click.__version__}')
print('All OK')
"

# Check CLI is registered
rcm-denial --help
```

---

## Option 1: Local + ngrok (quickest, 5 min)

Share your local machine with a public URL. Best for quick demos to 1-5 people.

```bash
# Terminal 1: Start the app (port from WEB_PORT in .env, default 8080)
rcm-denial web

# Terminal 2: Expose via ngrok
# Install: https://ngrok.com/download (free account)
ngrok http 8080    # match the WEB_PORT from your .env
```

ngrok gives you a URL like `https://abc123.ngrok-free.app` — share this with your audience.

**Important:**
- Ports are configurable in `.env`: `WEB_PORT=8080`, `GRAFANA_PORT=3000`
- Enable auth: set `WEB_AUTH_ENABLED=true` and `WEB_AUTH_USERS=demo:yourpassword` in `.env`
- Free ngrok: URL changes each restart, 40 connections/min limit
- Paid ngrok ($8/mo): custom subdomain, no rate limit

---

## Option 2: Railway.app (recommended for demo, 10 min)

One-command deploy from Git. Free tier includes 500 hours/month.

### Setup

```bash
# 1. Install Railway CLI
npm install -g @railway/cli

# 2. Login
railway login

# 3. Initialize project
railway init

# 4. Set environment variables
railway variables set OPENAI_API_KEY=sk-your-key-here
railway variables set WEB_AUTH_ENABLED=true
railway variables set WEB_AUTH_SECRET=$(openssl rand -hex 32)
railway variables set WEB_AUTH_USERS=admin:demo-password-here

# 5. Deploy
railway up

# Railway gives you a URL like: https://rcm-denial.up.railway.app
```

### Update after code changes

```bash
railway up    # redeploys from current directory
```

---

## Option 3: Render.com (easy, 10 min)

Docker-based deploy with persistent disk for SQLite data.

### Setup

1. Push your code to GitHub
2. Go to https://dashboard.render.com
3. Click **New** > **Blueprint** > connect your repo
4. Render reads `render.yaml` and creates the service automatically
5. Set environment variables in the dashboard:
   - `OPENAI_API_KEY` = your key
   - `WEB_AUTH_USERS` = `admin:yourpassword`

Your app will be at: `https://rcm-denial-management.onrender.com`

### Manual deploy (without Blueprint)

1. **New** > **Web Service** > **Docker**
2. Connect your GitHub repo
3. Set Docker command: `rcm-denial web --host 0.0.0.0 --port 10000`
4. Add environment variables
5. Deploy

---

## Option 4: AWS EC2 (production-grade, 15 min)

Full control, best for longer-running demos or staging environments.

### Setup

```bash
# 1. Launch an EC2 instance
#    - AMI: Ubuntu 22.04
#    - Instance type: t3.medium (2 vCPU, 4GB RAM)
#    - Security group: open ports 22, 8080
#    - Storage: 20GB

# 2. SSH in
ssh -i your-key.pem ubuntu@<public-ip>

# 3. Install Docker
sudo apt update && sudo apt install -y docker.io docker-compose
sudo usermod -aG docker ubuntu
newgrp docker

# 4. Clone your repo
git clone <your-repo-url>
cd rcm_denial_proto

# 5. Create .env
cp .env.example .env
nano .env
# Set: OPENAI_API_KEY, WEB_AUTH_ENABLED=true, WEB_AUTH_USERS=admin:password

# 6. Launch with Docker Compose (app + monitoring)
docker compose up -d

# App:       http://<public-ip>:8080
# Grafana:   http://<public-ip>:3000
# Prometheus:http://<public-ip>:9090
```

### With HTTPS (recommended for sharing)

```bash
# Install Caddy as reverse proxy (auto-HTTPS with Let's Encrypt)
sudo apt install -y caddy

# Configure /etc/caddy/Caddyfile:
#   your-domain.com {
#       reverse_proxy localhost:8080
#   }

sudo systemctl restart caddy
# Now accessible at: https://your-domain.com
```

### Without a domain (use AWS public IP + ngrok)

```bash
# On the EC2 instance:
ngrok http 8080
# Share the ngrok URL
```

---

## Option 5: Google Cloud Run (serverless, auto-scales)

Pay-per-request, auto-scales to zero when no traffic.

```bash
# 1. Build and push Docker image
gcloud builds submit --tag gcr.io/YOUR_PROJECT/rcm-denial

# 2. Deploy
gcloud run deploy rcm-denial \
  --image gcr.io/YOUR_PROJECT/rcm-denial \
  --port 8080 \
  --allow-unauthenticated \
  --set-env-vars="OPENAI_API_KEY=sk-...,WEB_AUTH_ENABLED=true,WEB_AUTH_USERS=admin:pass"

# Gives you: https://rcm-denial-xxxxx.run.app
```

**Note:** Cloud Run is stateless — SQLite data is lost on restart. For a short demo this is fine. For persistent data, use Cloud SQL (PostgreSQL) with `DATABASE_TYPE=postgresql`.

---

## Option 6: Oracle Cloud Always Free (recommended for full stack, $0)

Free forever VM with 4 ARM CPUs + 24GB RAM. Runs full docker-compose including Grafana.

See **[DEPLOY_ORACLE_CLOUD.md](DEPLOY_ORACLE_CLOUD.md)** for the complete step-by-step guide.

```bash
# Summary:
# 1. Create Always Free ARM VM (4 CPU, 24GB RAM)
# 2. Open ports 8080, 3000, 9090
# 3. Install Docker
# 4. git clone + cp .env.example .env + set OPENAI_API_KEY
# 5. docker compose up -d
# 6. Web UI: http://<ip>:8080  |  Grafana: http://<ip>:3000
```

---

## Option 7: Google Cloud — $300 Free Credits (recommended)

See **[DEPLOY_GOOGLE_CLOUD.md](DEPLOY_GOOGLE_CLOUD.md)** for the complete step-by-step guide.

---

## Pre-Demo Checklist (all options)

| # | Item | Command |
|---|------|---------|
| 1 | Ports configured | Set `WEB_PORT=8080` and `GRAFANA_PORT=3000` in .env (or use defaults) |
| 2 | Auth enabled | Set `WEB_AUTH_ENABLED=true` in .env |
| 3 | Strong password | Set `WEB_AUTH_USERS=admin:strong-password` |
| 4 | Session secret | Set `WEB_AUTH_SECRET=<random-64-char-string>` |
| 5 | OpenAI key | Set `OPENAI_API_KEY=sk-...` |
| 6 | SOP collections built | `rcm-denial init --verify` (or click "Init SOPs" in web UI) |
| 7 | Demo data loaded | Upload `data/demo_denials.csv` via web UI |
| 8 | Test the URL | Open in incognito browser, login, process one claim |

---

## Demo Script (suggested flow)

1. **Login** — show the auth screen, login as admin
2. **Dashboard** — click **Clear History** to reset any previous demo data
3. **Init SOPs** — click "Init SOPs" button; show 3 payer + global collections (skips if already fresh)
4. **Upload CSV** — upload `demo_denials.csv`, show 4 claims appear in Pending panel
5. **Select & Process** — select specific claims or click "Process All", watch pipeline stages light up live
6. **Review** — go to Review Queue > Pending Review tab, show claims with AI summaries
7. **Approve** — approve one claim, show it move to "approved"
8. **Re-route** — re-route one claim with reviewer notes, show it re-enter pipeline
9. **Submit** — go to Ready to Submit tab, click "Submit to Payer" on approved claims
10. **View detail** — click a claim ID, show:
    - Submission Package (cover letter + analysis + correction/appeal PDFs)
    - Internal Audit Data (audit_log.json, submission_metadata.json)
    - Appeal letter preview, evidence assessment, audit trail timeline
11. **Stats** — show operational metrics: claims per CARC code, processing time, review outcomes, write-off impact, recovery rate
12. **Evals** — Accuracy Check tab: run golden dataset, show 14/14 pass; Quality Signals tab: first-pass approval rate
