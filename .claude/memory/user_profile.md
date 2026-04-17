---
name: User Profile — RK
description: Developer building RCM Denial Management agentic AI system — preferences, working style, and technical context
type: user
---

# User Profile

- **Email**: kvrkr866@gmail.com
- **Domain**: Healthcare RCM (Revenue Cycle Management) / medical billing (US)
- **Stack**: Python 3.12, LangGraph, LangChain, OpenAI, ChromaDB, Pydantic v2, NiceGUI, Docker
- **Environment**: WSL2 on Windows (Linux path: /mnt/e/rcm_study/rcm_denial_proto)
- **Cloud**: Google Cloud e2-medium VM (Docker deployment)

## Working Style
- Thinks ahead — asks about production readiness before demo is complete
- Values clean separation: UI layer separate from backend, CLI and web both work independently
- Prefers organized file structure (deployment/ folder, docs/ folder)
- Wants comprehensive documentation (README for users, TECHNICAL_ARCHITECTURE for engineers, REQUIREMENTS_AND_ROADMAP for stakeholders)
- Tests on local PC first, then deploys to cloud
- Iterates on UI look-and-feel with specific feedback

## Key Preferences
- Don't start changes immediately on big features — discuss approach first
- Check thoroughly before confirming "all OK" — verify end-to-end, not just compilation
- NiceGUI stats page: operational/business-focused (no LLM cost — that goes to Grafana)
- RAG KB should NOT be cleared with claim history (independent)
- Output: package/ (payer submission) vs internal_audit/ (not submitted) — clear separation
- Ports configurable via .env, not hardcoded
- Date format flexibility (DD-MM-YYYY from Excel exports)
- Sensitive about file naming and folder organization
