---
name: Feedback and Lessons Learned
description: User corrections and preferences gathered during development — apply in future sessions
type: feedback
---

# Feedback & Lessons Learned

## Verify Thoroughly Before Confirming
- User caught Grafana issues after I said "all OK" — must test end-to-end chains, not just syntax
- Grafana pipeline had 11 issues: wrong metric names, missing push, wrong Docker hostnames, missing Loki datasource
- **How to apply:** When user asks "is it OK?", actually trace the full data flow, don't just check compilation

## Don't Mix Business and Technical Metrics
- NiceGUI Stats page should show operational metrics (claims, CARC, reviews, write-offs)
- LLM cost, tool performance, fallback rates go to Grafana (technical audience)
- **Why:** Demo audience is billing managers, not engineers

## Separation of Concerns
- Web UI is a thin layer — no business logic in NiceGUI pages
- CLI and Web UI must both work independently
- Output: package/ (payer submission) vs internal_audit/ (not submitted) — never mix
- RAG KB is independent of claim history — Clear History must NOT delete manifest.json or ChromaDB

## File Organization
- Deployment docs go in deployment/ folder
- Technical docs go in docs/ folder
- railway.json and render.yaml must stay in project ROOT (platforms read from root)
- README is for "what and how to run", TECHNICAL_ARCHITECTURE is for "how it works"

## Docker/Cloud Gotchas
- NiceGUI version differs between local (2.4.0) and Docker (3.10.0) — handle both upload APIs
- Docker volumes need to be writable for manifest.json (remove :ro)
- Prometheus/Promtail must use Docker service names (pushgateway:9091, loki:3100) not localhost
- prometheus_client package must be in requirements.txt for Pushgateway push
- push_to_gateway parameter order: gateway= first, job= second

## User Preferences
- Discuss approach before starting significant features
- Ports should be configurable via .env
- Batch ID auto-generated with date+time+sequence
- CSV state should persist across page navigation
- Date format flexibility (DD-MM-YYYY from Excel exports)
- Don't rebuild RAG KB if already fresh (skip-if-fresh)
