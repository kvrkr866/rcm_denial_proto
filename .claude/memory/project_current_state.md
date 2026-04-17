---
name: Project Current State — April 2026
description: Comprehensive snapshot of RCM Denial Management project status, what's built, deployed, and pending
type: project
---

# RCM Denial Management — Current State (April 2026)

## What It Is
Multi-agent AI system (LangGraph) for medical claim denial management. Analyzes denied claims using CARC/RARC codes, generates appeal letters/correction plans, routes through human review, submits to payer portals.

## Status: Demo-Ready
- All 6 phases implemented (core pipeline, evidence check, SOP RAG, review queue, submission, observability)
- NiceGUI web UI with 5 pages (dashboard, process claims, review queue, stats, evals)
- Grafana dashboard with 21 panels + 3 Loki log panels
- Docker deployment working on Google Cloud (e2-medium VM)
- 110+ automated tests passing

## Key Architecture Decisions
- LangGraph StateGraph with 10 agents
- Per-payer ChromaDB RAG collections (summithealth, nationalcare, crestviewhealth, global)
- 3 LLM call sites: evidence_check_agent, response_agent, analysis_agent (all with rule-based fallback)
- Structured output via Pydantic v2 models
- Human-in-the-loop: 4 actions (approve, re-route, human override, write-off with guard)
- Output: package/ (submitted to payer) + internal_audit/ (not submitted)
- Claim disposition table + EHR sync after submission
- Cover letter PDF as first page of submission package

## Data
- 3 real payer SOPs: SummitHealth, NationalCare, CrestviewHealth (PDF files)
- 4 real EOB PDFs matching the payer SOPs
- demo_denials.csv with 12 claims across 3 payers
- Payer IDs in CSV normalize to folder names: SummitHealth→summithealth, NationalCare→nationalcare, Crestview→crestviewhealth
- Date formats: flexible parser handles DD-MM-YYYY, YYYY-MM-DD, etc.

## Cloud Deployment
- Google Cloud e2-medium VM (2 vCPU, 4GB RAM, $300 free credits)
- docker compose up -d (app + Prometheus + Grafana + Loki + Pushgateway)
- WEB_PORT=8080, GRAFANA_PORT=3000
- Pushgateway receives all-time cumulative metrics after each batch

## Known Issues Fixed
- NiceGUI version difference: local=2.4.0 (e.content), Docker=3.10.0 (e.file with async read)
- Prometheus config needed Docker service names (pushgateway:9091, loki:3100) not localhost
- push_to_gateway had parameter order bug (gateway= must be first)
- get_current_metrics() had broken payer_id query on claim_intake_log
- SOP PDF loading needed PyMuPDF (was using poppler-dependent pdf2image)
- Manifest.json needs writable mount (removed :ro from docker-compose)

## Planned Features (Not Built)
- Multi-payer COB (check secondary payer when primary denies)
- Policy discovery service (policy_source_registry table)
- Patient data caching across claims in batch
- Real EHR/PMS/payer integrations (adapter scaffolds exist)
- Voice integration (speech-to-text for claim intake and review actions)
- REST API wrapper (FastAPI)
- HIPAA compliance, RBAC
- Parallel batch processing

## Key Files
- Web UI: src/rcm_denial/web/ (app.py, layout.py, auth.py, pages/)
- Pipeline: src/rcm_denial/workflows/denial_graph.py
- Agents: src/rcm_denial/agents/ (10 agents)
- Services: src/rcm_denial/services/ (14 service modules)
- Docs: docs/REQUIREMENTS_AND_ROADMAP.md, deployment/TECHNICAL_ARCHITECTURE.md
- Config: .env, src/rcm_denial/config/settings.py
