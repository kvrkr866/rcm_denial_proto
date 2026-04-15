# Deploy to Oracle Cloud — Always Free Tier

## Why Oracle Cloud Always Free

- **Free forever** — not a trial, no credit card charge
- **24GB RAM** on ARM instance — more than enough for full stack
- **Full docker-compose** — app + Grafana + Prometheus + Loki all run
- **Never sleeps** — always available for demos
- **200GB storage** — plenty for SOP documents, PDFs, ChromaDB, SQLite

## Step 1: Create Oracle Cloud Account

1. Go to https://www.oracle.com/cloud/free/
2. Sign up (credit card required for verification but never charged for Always Free)
3. Select your home region (choose one close to your demo audience)

## Step 2: Create Always Free VM

1. Go to **Compute** > **Instances** > **Create Instance**
2. Configure:
   - **Name:** `rcm-denial-demo`
   - **Image:** Ubuntu 22.04 (or 24.04)
   - **Shape:** Click "Change Shape" > **Ampere** > **VM.Standard.A1.Flex**
     - OCPUs: **4** (max free)
     - Memory: **24 GB** (max free)
   - **Networking:** Create new VCN or use default
   - **SSH Key:** Upload your public key or generate one
   - **Boot Volume:** 50GB (default, within free tier)
3. Click **Create**

## Step 3: Open Firewall Ports

### Oracle Cloud Security List (VCN level)

1. Go to **Networking** > **Virtual Cloud Networks** > your VCN
2. Click **Security Lists** > **Default Security List**
3. Add **Ingress Rules:**

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 22 | TCP | 0.0.0.0/0 | SSH |
| 8080 | TCP | 0.0.0.0/0 | Web UI (NiceGUI) |
| 3000 | TCP | 0.0.0.0/0 | Grafana |
| 9090 | TCP | 0.0.0.0/0 | Prometheus (optional) |

### VM-level firewall (iptables)

```bash
# SSH into your VM first, then:
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 3000 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 9090 -j ACCEPT
sudo netfilter-persistent save
```

## Step 4: Install Docker

```bash
# SSH into your VM
ssh -i your-key ubuntu@<public-ip>

# Install Docker
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker ubuntu
newgrp docker

# Verify
docker --version
docker compose version
```

## Step 5: Clone and Configure

```bash
# Clone your repo
git clone <your-repo-url>
cd rcm_denial_proto

# Create .env
cp .env.example .env
nano .env
```

Set these in `.env`:
```dotenv
# Required
OPENAI_API_KEY=sk-your-key-here

# Security (important for cloud!)
WEB_AUTH_ENABLED=true
WEB_AUTH_USERS=admin:your-strong-password
WEB_AUTH_SECRET=change-this-to-a-random-64-char-string

# Ports (configurable)
WEB_PORT=8080
GRAFANA_PORT=3000

# Production settings
ENV=production
LOG_LEVEL=INFO
```

## Step 6: Build and Launch

```bash
# Build Docker image (takes ~3-5 min on ARM)
docker compose build

# Launch full stack
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

## Step 7: Access

| Service | URL | Credentials |
|---------|-----|-------------|
| **Web UI** | `http://<public-ip>:8080` | From WEB_AUTH_USERS in .env |
| **Grafana** | `http://<public-ip>:3000` | admin / admin (change on first login) |
| **Prometheus** | `http://<public-ip>:9090` | No auth |

## Step 8: Initialize and Test

Open the Web UI in your browser, then:

1. **Dashboard** > **Clear History** (reset any stale data)
2. **Process Claims** > **Init SOPs** (builds RAG collections)
3. Upload `demo_denials.csv` > **Process All**
4. **Review Queue** > approve/re-route claims
5. **Stats** > verify metrics appear
6. Open Grafana > verify dashboard panels

## Running CLI Commands on the VM

```bash
# SSH into VM, then:
docker compose exec app rcm-denial stats
docker compose exec app rcm-denial review list --status pending
docker compose exec app rcm-denial evals run
```

## Updating After Code Changes

```bash
# On your VM:
cd rcm_denial_proto
git pull
docker compose build
docker compose up -d
```

## Monitoring

```bash
# View app logs
docker compose logs -f app

# View all service logs
docker compose logs -f

# Check resource usage
docker stats
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Port not accessible | Check Oracle VCN security list + VM iptables |
| Container keeps restarting | `docker compose logs app` to check error |
| Out of memory | Reduce to 2 OCPUs / 12GB RAM (still free) |
| Slow first build | ARM builds take longer; subsequent builds use cache |
| Grafana shows no data | Run `rcm-denial stats --export-metrics` first |

## Cost: $0

All resources used are within Oracle Cloud Always Free tier:
- VM.Standard.A1.Flex: 4 OCPUs + 24GB RAM (free)
- Boot volume: 50GB (free up to 200GB)
- Network: 10TB outbound (free)
- No time limit, no trial expiry
