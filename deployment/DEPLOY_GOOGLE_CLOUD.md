# Deploy to Google Cloud — $300 Free Credits (90 days)

## Why Google Cloud

- **$300 free credits** for 90 days — enough for ~12 months on e2-medium
- **No region restrictions** — pick any region globally
- **Full docker-compose** — app + Grafana + Prometheus + Loki all run
- **4GB RAM** on e2-medium — comfortable for full stack
- **Easy SSH** — browser-based terminal, no local SSH setup needed

## Step 1: Create Google Cloud Account

1. Go to https://cloud.google.com/free
2. Sign in with your Google account
3. You get **$300 free credits for 90 days**
4. Credit card required for verification (not charged unless you upgrade)

## Step 2: Create VM Instance

1. Go to **Google Cloud Console** > **Compute Engine** > **VM Instances**
2. If prompted, enable the Compute Engine API (takes ~1 min)
3. Click **Create Instance**
4. Configure:

| Setting | Value |
|---------|-------|
| **Name** | `rcm-denial-demo` |
| **Region** | `asia-south1` (Mumbai) or `us-central1` (Iowa) |
| **Zone** | Any available |
| **Machine type** | `e2-medium` (2 vCPU, 4GB RAM) — ~$25/mo from free credits |
| **Boot disk** | Click "Change" → Ubuntu 22.04 LTS, 30 GB, Standard |
| **Firewall** | Check both: "Allow HTTP traffic" and "Allow HTTPS traffic" |

5. Click **Create** (takes ~1 minute)

**Budget note:** e2-medium at $25/mo means $300 credits last ~12 months. For even longer, use e2-small (2 vCPU, 2GB RAM, ~$13/mo).

## Step 3: Open Firewall Ports

The default firewall only allows HTTP (80) and HTTPS (443). We need ports 8080 (Web UI), 3000 (Grafana), and 9090 (Prometheus).

1. Go to **VPC Network** > **Firewall** > **Create Firewall Rule**
2. Configure:

| Setting | Value |
|---------|-------|
| **Name** | `allow-rcm-demo` |
| **Direction** | Ingress |
| **Targets** | All instances in the network |
| **Source IP ranges** | `0.0.0.0/0` |
| **Protocols and ports** | TCP: `8080,3000,9090` |

3. Click **Create**

## Step 4: SSH into the VM

**Option A: Browser SSH (easiest)**
- Go to VM Instances page
- Click the **SSH** button next to your instance
- A browser terminal opens — no local setup needed

**Option B: gcloud CLI**
```bash
# Install gcloud CLI: https://cloud.google.com/sdk/docs/install
gcloud compute ssh rcm-denial-demo --zone=<your-zone>
```

## Step 5: Install Docker

Run these commands in the SSH terminal:

```bash
# Update and install Docker
sudo apt update
sudo apt install -y docker.io docker-compose-plugin git

# Add your user to docker group (avoids needing sudo)
sudo usermod -aG docker $USER
newgrp docker

# Verify installation
docker --version
docker compose version
```

## Step 6: Clone Repository and Configure

```bash
# Clone your repo
git clone <your-repo-url>
cd rcm_denial_proto

# Create .env from template
cp .env.example .env
nano .env
```

Set these values in `.env`:

```dotenv
# Required — LLM features
OPENAI_API_KEY=sk-your-key-here

# Security — important for cloud deployment
WEB_AUTH_ENABLED=true
WEB_AUTH_USERS=admin:your-strong-password
WEB_AUTH_SECRET=change-this-to-a-random-string

# Ports
WEB_PORT=8080
GRAFANA_PORT=3000

# Production settings
ENV=production
LOG_LEVEL=INFO
METRICS_EXPORT_AFTER_BATCH=true
PROMETHEUS_PUSHGATEWAY_URL=http://pushgateway:9091
```

Save: `Ctrl+O` → Enter → `Ctrl+X`

## Step 7: Build and Launch

```bash
# Build Docker images (takes ~5 min first time)
docker compose build

# Launch full stack (app + Prometheus + Grafana + Loki)
docker compose up -d

# Verify all services are running
docker compose ps
```

Expected output:
```
NAME              STATUS        PORTS
rcm_app           Up            0.0.0.0:8080->8080/tcp
rcm_grafana       Up            0.0.0.0:3000->3000/tcp
rcm_prometheus    Up            0.0.0.0:9090->9090/tcp
rcm_loki          Up            0.0.0.0:3100->3100/tcp
rcm_pushgateway   Up            0.0.0.0:9091->9091/tcp
rcm_promtail      Up
```

## Step 8: Get Your External IP

1. Go back to **VM Instances** page in Google Cloud Console
2. Copy the **External IP** column value (e.g., `34.93.xx.xx`)

## Step 9: Access Your Demo

| Service | URL | Credentials |
|---------|-----|-------------|
| **Web UI** | `http://<external-ip>:8080` | From WEB_AUTH_USERS in .env |
| **Grafana** | `http://<external-ip>:3000` | admin / admin (change on first login) |
| **Prometheus** | `http://<external-ip>:9090` | No auth |

## Step 10: Initialize and Run Demo

Open the Web UI in your browser, then:

1. **Login** with your credentials
2. **Dashboard** > Click **Clear History** (reset any stale data)
3. **Process Claims** > Click **Init SOPs** (builds RAG collections)
4. **Upload** `demo_denials.csv` > Select claims > **Process**
5. **Review Queue** > **Pending Review** tab > Approve/re-route claims
6. **Review Queue** > **Ready to Submit** tab > Submit to payer
7. **Stats** > View operational metrics, CARC breakdown, EHR sync status
8. **Evals** > Run accuracy check against golden dataset
9. Open **Grafana** > View technical dashboard (LLM cost, tool performance)

## Share the Demo URL

Share this with your audience:
```
Web UI:  http://<external-ip>:8080
Grafana: http://<external-ip>:3000
```

## Useful Commands

```bash
# View app logs
docker compose logs -f app

# View all service logs
docker compose logs -f

# Restart after code changes
git pull
docker compose build
docker compose up -d

# Stop everything
docker compose down

# Check resource usage (CPU, RAM per container)
docker stats

# Run CLI commands inside the container
docker compose exec app rcm-denial stats
docker compose exec app rcm-denial review list
docker compose exec app rcm-denial evals run
```

## Updating After Code Changes

```bash
# On the VM:
cd rcm_denial_proto
git pull
docker compose build
docker compose up -d
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| **Port not accessible** | Check firewall rule `allow-rcm-demo` exists in VPC Network > Firewall |
| **Container keeps restarting** | `docker compose logs app` — check for errors |
| **Grafana shows no data** | Process some claims first, then `docker compose exec app rcm-denial stats --export-metrics` |
| **Build fails** | Check disk space: `df -h`. 30GB should be enough |
| **Slow performance** | `docker stats` to check RAM. Upgrade to e2-standard-2 (8GB) if needed |
| **SSH disconnects** | Use `tmux` or `screen` before running long commands |

## Cost Management

| Machine Type | Monthly Cost | Credits Last |
|-------------|-------------|-------------|
| e2-medium (2 vCPU, 4GB) | ~$25/mo | ~12 months |
| e2-small (2 vCPU, 2GB) | ~$13/mo | ~23 months |

- Set a **budget alert** at $50 to avoid surprises: Billing > Budgets & Alerts
- **Stop the VM** when not demoing: VM Instances > Stop (charges stop when VM is stopped)
- **Delete the VM** when done with all demos to stop all charges

## Stopping vs Deleting

```bash
# Stop VM (keeps data, stops charges for compute, small disk charge remains)
gcloud compute instances stop rcm-denial-demo --zone=<zone>

# Start VM again
gcloud compute instances start rcm-denial-demo --zone=<zone>

# Delete VM completely (all data lost)
gcloud compute instances delete rcm-denial-demo --zone=<zone>
```
