# RCM Denial Management — Requirements & Roadmap

**Project:** RCM Denial Management — Agentic AI System
**Author:** RK (kvrkr866@gmail.com)
**Version:** 1.0.0 (Demo)
**Date:** April 2026

---

## Table of Contents

1. [Section A: Implemented Features (Demo-Ready)](#section-a-implemented-features-demo-ready)
2. [Section B: Production Roadmap (Hooks, Provisions & Future Features)](#section-b-production-roadmap-hooks-provisions--future-features)

---

# Section A: Implemented Features (Demo-Ready)

Everything below is built, tested, and working in the current codebase.

## A1. Core AI Pipeline

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A1.1 | **Multi-agent LangGraph pipeline** | 10 agents orchestrated in a directed acyclic graph with conditional routing | Done |
| A1.2 | **Shared typed state** | `DenialWorkflowState` (Pydantic v2) passed through all agents — every field typed and validated | Done |
| A1.3 | **Supervisor routing** | After analysis, automatically routes to resubmit / appeal / both / write_off based on LLM recommendation | Done |
| A1.4 | **Rule-based fallback** | Every LLM agent has a deterministic CARC-code fallback — system works fully offline without OpenAI key | Done |
| A1.5 | **Batch processing** | CSV-driven with idempotency — skips already-completed claims on re-run | Done |
| A1.6 | **Single claim processing** | Interactive single-claim processing via CLI or Web UI | Done |

## A2. AI / LLM Integration

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A2.1 | **Structured LLM output** | `ChatOpenAI.with_structured_output(PydanticModel)` — forces valid typed output (DenialAnalysis, EvidenceCheckResult, CorrectionPlan, AppealLetter) | Done |
| A2.2 | **Evidence check (LLM call 1)** | Assesses whether available evidence is sufficient before generating response; identifies evidence gaps and key arguments | Done |
| A2.3 | **Response generation (LLM call 2)** | Generates CorrectionPlan and/or AppealLetter informed by evidence assessment and SOP context | Done |
| A2.4 | **Targeted EHR fetch** | Stage 2 EHR retrieval for labs, imaging, pathology when evidence is insufficient | Done |
| A2.5 | **LLM cost tracking** | Per-call recording of model, input/output tokens, USD cost to `llm_cost_log` table | Done |
| A2.6 | **Rate limiting** | Token-bucket rate limiter (configurable RPM + burst) prevents OpenAI 429 errors during large batches | Done |
| A2.7 | **Temperature control** | Configurable via `OPENAI_TEMPERATURE` (default 0.1 for consistent medical output) | Done |
| A2.8 | **Model selection** | Configurable via `OPENAI_MODEL` — supports gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-3.5-turbo | Done |

## A3. RAG (Retrieval-Augmented Generation)

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A3.1 | **Per-payer ChromaDB collections** | Separate vector store per payer (sop_summithealth, sop_nationalcare, etc.) + global collection | Done |
| A3.2 | **Payer isolation** | Payer X's claims only search payer X's collection — no cross-payer contamination | Done |
| A3.3 | **OpenAI embeddings** | text-embedding-3-small for SOP document vectorization | Done |
| A3.4 | **PDF SOP ingestion** | PyMuPDF extracts text from PDF SOP documents (digital); Tesseract fallback for scanned | Done |
| A3.5 | **Manifest tracking** | `manifest.json` records collection health: document count, index timestamp, verification status per payer | Done |
| A3.6 | **Freshness check** | Re-indexes automatically when SOP files are newer than the collection | Done |
| A3.7 | **Pipeline mode** | Blocks indexing during batch runs — only serves queries, never modifies collections mid-batch | Done |
| A3.8 | **Skip-if-fresh** | Init SOPs skips already up-to-date collections — avoids unnecessary embedding API calls | Done |
| A3.9 | **Keyword fallback** | When ChromaDB is unavailable, uses keyword matching against built-in SOP snippets | Done |
| A3.10 | **Relevance scoring** | Configurable minimum relevance score threshold (`SOP_MIN_RELEVANCE_SCORE`) | Done |

## A4. Data Enrichment Tools

| # | Tool | Mock Data | What It Provides | Status |
|---|------|-----------|-----------------|--------|
| A4.1 | **Patient Data Tool** | PAT001-PAT003 | Demographics, insurance coverage, eligibility | Done (mock) |
| A4.2 | **Payer Policy Tool** | BCBS, AETNA, MEDICARE, CIGNA + real payers | Coverage rules, filing deadlines, appeal instructions | Done (mock) |
| A4.3 | **EHR Tool** | By patient ID | Encounter notes, procedure details, prior auth records | Done (mock) |
| A4.4 | **EOB OCR Tool** | Mock fallback | PDF text extraction — PyMuPDF (digital) + Tesseract (scanned) | Done |
| A4.5 | **SOP RAG Tool** | Keyword fallback | Per-payer SOP retrieval from ChromaDB | Done |
| A4.6 | **Clinical OCR Tool** | — | PDF text extraction for clinical documents | Done |

## A5. Human-in-the-Loop Review

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A5.1 | **Non-blocking review** | Pipeline completes fully, then enqueues — humans review asynchronously | Done |
| A5.2 | **AI-generated summary** | Decision-ready summary with root cause, evidence confidence, key arguments, flag reasons | Done |
| A5.3 | **4 reviewer actions** | Approve, Re-route (to any pipeline stage), Human Override, Write-off | Done |
| A5.4 | **Write-off guard** | Blocked unless re-route was attempted first OR reason is timely_filing_expired; force option for manager override | Done |
| A5.5 | **Pipeline re-entry** | Re-routed claims re-run from chosen stage (intake, targeted_ehr, response) with reviewer notes injected into LLM prompts | Done |
| A5.6 | **Bulk approve** | Auto-approve low-risk claims by confidence and amount thresholds | Done |
| A5.7 | **Review count tracking** | Tracks how many HITL cycles each claim has gone through | Done |

## A6. Document Output

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A6.1 | **Cover letter PDF** | First page of submission package — clearly states RESUBMISSION or APPEAL, problem, resolution per SOP, documents enclosed | Done |
| A6.2 | **Denial analysis PDF** | Root cause analysis report with claim summary, CARC/RARC interpretation, reasoning | Done |
| A6.3 | **Correction plan PDF** | Code corrections table, documentation checklist, resubmission instructions | Done |
| A6.4 | **Appeal letter PDF** | Formal appeal with clinical justification, regulatory basis, closing, signature block | Done |
| A6.5 | **Merged submission package** | All PDFs merged into single SUBMISSION_PACKAGE_{id}.pdf | Done |
| A6.6 | **Separated output structure** | `package/` (submitted to payer) and `internal_audit/` (audit_log.json, metadata — NOT submitted) | Done |

## A7. Payer Submission

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A7.1 | **4 submission adapters** | MockSubmissionAdapter (active), AvailitySubmissionAdapter (scaffold), RPASubmissionAdapter (scaffold), EDI837SubmissionAdapter (scaffold) | Done |
| A7.2 | **Per-payer registry** | `payer_submission_registry` table maps each payer to its submission method | Done |
| A7.3 | **Retry with backoff** | tenacity-based exponential backoff for transient network errors | Done |
| A7.4 | **Submission logging** | Every attempt (success or failure) logged to `submission_log` table | Done |
| A7.5 | **Status checking** | Poll payer for adjudication status after submission | Done |

## A8. Post-Submission

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A8.1 | **Claim disposition** | `claim_disposition` table records final status: disposition, confirmation number, payer response | Done |
| A8.2 | **EHR sync** | After submission, marks claim as synced to EHR (mock: local DB; real: EMR adapter push) | Done |
| A8.3 | **EHR sync tracking** | `ehr_synced` flag (0=pending, 1=synced, -1=failed) with timestamp and error details | Done |

## A9. Observability & Monitoring

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A9.1 | **NiceGUI Stats page** | Operational dashboard: claims loaded/processed/approved/pending, CARC breakdown, processing time, review outcomes, write-off impact, recovery rate, EHR sync status | Done |
| A9.2 | **NiceGUI Evals page** | Accuracy Check (14 golden cases) + Quality Signals (first-pass approval rate, override rate, confidence calibration) | Done |
| A9.3 | **Grafana dashboard** | 21 panels across 8 sections: pipeline overview, processing performance, LLM cost/usage, review queue, submissions, write-offs, tool performance, logs | Done |
| A9.4 | **Prometheus metrics** | 11 metric families pushed to Pushgateway after each batch | Done |
| A9.5 | **Loki log aggregation** | Structured JSON logs shipped via Promtail with claim_id/batch_id labels; error, audit trail, and all-logs panels in Grafana | Done |
| A9.6 | **LLM cost tracking** | Per-call, per-claim, per-batch cost summaries from `llm_cost_log` table | Done |
| A9.7 | **Structured logging** | structlog JSON format with claim context binding | Done |

## A10. Web UI (NiceGUI)

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A10.1 | **Dashboard** | Overview cards + live stats from review queue + Clear History button | Done |
| A10.2 | **Process Claims** | Three-panel operator console: Pending / Processing (live pipeline stepper) / Completed | Done |
| A10.3 | **CSV upload with state preservation** | Upload CSV, state survives page navigation, batch ID auto-incremented per upload | Done |
| A10.4 | **Selective processing** | Checkboxes on pending claims; toggle between "All Claims" and "Selected Only" | Done |
| A10.5 | **Cancel processing** | Stop button — cancels after current claim finishes | Done |
| A10.6 | **Init SOPs from UI** | Build RAG collections from web UI; skips if already fresh | Done |
| A10.7 | **Review Queue** | 3 tabs: Pending Review / Ready to Submit / Submitted | Done |
| A10.8 | **Claim detail page** | Full view: analysis, evidence, appeal letter preview, submission package, internal audit data, audit trail | Done |
| A10.9 | **Authentication** | Username/password login with session storage (configurable via .env) | Done |
| A10.10 | **Stats & Evals** | Operational metrics + accuracy checks — see A9.1 and A9.2 | Done |

## A11. Evaluation System

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A11.1 | **Continuous eval** | First-pass approval rate, override rate, confidence calibration, re-route hotspots — all derived from reviewer actions | Done |
| A11.2 | **22 deterministic criteria checks** | Structural assertions on DenialAnalysis (7), AppealLetter (6), EvidenceCheckResult (5), CorrectionPlan (4) — no LLM cost | Done |
| A11.3 | **14-case golden dataset** | Covers all 7 denial categories, all 4 actions (resubmit, appeal, both, write_off), edge cases | Done |
| A11.4 | **LLM-as-judge evaluation** | 5-metric composite scoring: classification accuracy, CARC interpretation, document completeness, appeal quality, latency | Done |

## A12. Production Hardening

| # | Feature | Detail | Status |
|---|---------|--------|--------|
| A12.1 | **Crash recovery checkpointing** | Per-node state saved to `claim_checkpoint` table; batch restart resumes from last completed node | Done |
| A12.2 | **OCR dual strategy** | PyMuPDF (digital, 95% confidence) + Tesseract (scanned fallback) | Done |
| A12.3 | **Flexible date parsing** | Accepts YYYY-MM-DD, DD-MM-YYYY, MM-DD-YYYY, DD/MM/YYYY and more | Done |
| A12.4 | **Docker deployment** | Dockerfile + docker-compose.yml (app + Prometheus + Grafana + Loki + Pushgateway) | Done |
| A12.5 | **PostgreSQL option** | Production DB with one-command migration from SQLite | Done |
| A12.6 | **CI/CD** | GitHub Actions: lint + test + Docker build | Done |
| A12.7 | **Configurable ports** | WEB_PORT, GRAFANA_PORT configurable via .env | Done |
| A12.8 | **Clear History** | Wipe processed claims, audit logs, output files — preserves RAG collections | Done |

## A13. CLI (30+ commands)

| # | Command Group | Commands | Status |
|---|--------------|----------|--------|
| A13.1 | Pipeline | `process-batch`, `process-claim` | Done |
| A13.2 | SOP Management | `init`, `ingest-sop`, `seed-kb`, `sop-status` | Done |
| A13.3 | Review Queue | `review list/detail/approve/re-route/override/write-off/bulk-approve/execute-reentry/stats` | Done |
| A13.4 | Submission | `submit`, `submit-batch`, `submission-status`, `submission-log`, `submission-registry list/register`, `submission-stats` | Done |
| A13.5 | Observability | `stats`, `db info/export-schema/migrate-to-postgres` | Done |
| A13.6 | Evals | `evals run/check-output/quality-signals` | Done |
| A13.7 | Web & Tests | `web`, `run-tests` | Done |

## A14. Testing

| # | Test File | Tests | What It Covers | Status |
|---|-----------|-------|---------------|--------|
| A14.1 | test_agents.py | — | Phase 1 agent unit tests | Done |
| A14.2 | test_tools.py | — | Phase 1 mock data tools | Done |
| A14.3 | test_batch_processor.py | — | Batch integration | Done |
| A14.4 | test_review_queue.py | 15 | Queue actions, write-off guard, eval stats | Done |
| A14.5 | test_submission.py | 14 | Adapters, registry, retry logic | Done |
| A14.6 | test_cost_tracker.py | 17 | Cost calculation, recording, summaries | Done |
| A14.7 | test_sop_pipeline_mode.py | 17 | Pipeline mode, manifest, coverage | Done |
| A14.8 | test_criteria_checks.py | 24 | Structural assertions, golden dataset | Done |
| A14.9 | test_rate_limiter.py | 6 | Token bucket, throttle, burst | Done |

---

# Section B: Production Roadmap (Hooks, Provisions & Future Features)

Everything below has either a **hook/provision already in the code** (adapter interface, registry table, config flag) or is a **planned feature** with a clear implementation path.

## B1. Real External System Integrations

### B1.1 EHR / EMR Integration

| Item | Hook/Provision | What Exists Today | What's Needed for Production |
|------|---------------|-------------------|----------------------------|
| **Epic FHIR R4** | `BaseEMRAdapter` abstract class + `EpicEMRAdapter` scaffold | Mock adapter active; adapter factory in `data_source_adapters.py` | Implement FHIR R4 REST calls: GET /Patient, /Encounter, /DocumentReference, /Procedure |
| **Cerner** | `CernerEMRAdapter` scaffold | Same adapter factory | Implement HealtheIntent API calls |
| **Athena Health** | `AthenaEMRAdapter` scaffold | Same adapter factory | Implement Athena REST API |
| **RPA Portal** | `RPAPortalAdapter` scaffold | Same adapter factory | Implement Playwright browser automation for EHR portals without API |
| **Config switch** | `EMR_ADAPTER=epic` in .env | `EMR_ADAPTER=mock` (default) | Zero code change — flip config |
| **Connection registry** | `medical_record_source_registry` table | Per-provider: access_method, endpoint_url, credentials_ref, last_verified_at | Populate with real provider endpoints |

### B1.2 Practice Management System (PMS) Integration

| Item | Hook/Provision | What Exists Today | What's Needed |
|------|---------------|-------------------|---------------|
| **Kareo / Tebra** | `BasePMSAdapter` + `KareoPMSAdapter` scaffold | Mock adapter active | Implement Kareo REST API for claim history, eligibility |
| **AdvancedMD** | `AdvancedMDAdapter` scaffold | Same | Implement AdvancedMD API |
| **Config switch** | `PMS_ADAPTER=kareo` in .env | `PMS_ADAPTER=mock` (default) | Zero code change |

### B1.3 Payer Portal / Clearinghouse Integration

| Item | Hook/Provision | What Exists Today | What's Needed |
|------|---------------|-------------------|---------------|
| **Availity** | `BasePayerAdapter` + `AvailityPayerAdapter` scaffold | Mock adapter active | Implement Availity REST API for coverage rules, appeal instructions |
| **Change Healthcare** | `ChangeHealthcareAdapter` scaffold | Same | Implement Optum/CHC API |
| **EDI 270/271** | Scaffold in payer adapter | Same | Implement EDI eligibility transaction via clearinghouse |
| **Config switch** | `PAYER_ADAPTER=availity` in .env | `PAYER_ADAPTER=mock` (default) | Zero code change |

### B1.4 Payer Submission Integration

| Item | Hook/Provision | What Exists Today | What's Needed |
|------|---------------|-------------------|---------------|
| **Availity REST API** | `AvailitySubmissionAdapter` scaffold | Mock active; per-payer `payer_submission_registry` table | Implement POST /claims endpoint with 837P payload |
| **RPA Portal** | `RPASubmissionAdapter` scaffold | Same | Implement Playwright: login, navigate, upload PDF, submit form |
| **EDI 837P/I** | `EDI837SubmissionAdapter` scaffold | Same | Build 837 transaction, transmit via SFTP to clearinghouse |
| **Per-payer config** | `payer_submission_registry` table | Per-payer override: payer_id → method, portal_url, api_endpoint, credentials_ref | Populate with real payer endpoints |
| **Config switch** | `SUBMISSION_ADAPTER=availity_api` in .env | `SUBMISSION_ADAPTER=mock` (default) | Zero code change for global default |

## B2. Multi-Payer Coordination of Benefits (COB)

| Item | Hook/Provision | What's Needed |
|------|---------------|---------------|
| **COB detection** | `denial_category="coordination_of_benefits"` recognized; CARC 22/23/24 mapped | Service to look up ALL active policies for a patient when primary denies |
| **Policy discovery** | `PatientData.insurance_coverage` is a list (supports multiple policies) | New `policy_source_registry` table: source_type (ehr/payer_api/clearinghouse/state_hie), access_method, endpoint_url |
| **Coverage eligibility check** | — | For each secondary policy: check if THIS category of service is covered (not just balance math) |
| **Secondary claim generation** | — | Create new claim for secondary payer with primary denial EOB attached |
| **Pipeline integration** | Same pipeline (analyze → response → package → review → submit) | Route secondary claim through pipeline targeted at the secondary payer's SOP |

**Key insight:** This is NOT about splitting the balance. When primary denies for eligibility, service exclusion, or out-of-network — check if another policy covers the service category entirely.

## B3. Patient Data Caching

| Item | Hook/Provision | What's Needed |
|------|---------------|---------------|
| **Same-patient optimization** | Enrichment agent calls tools per claim | `BatchCache` class: cache key = (patient_id, provider_id) for EHR, patient_id for patient data, payer_id for payer policy |
| **Cache scope** | — | Within one batch run (not persisted across batches) |
| **Cache invalidation** | — | When patient_id changes, clear patient + EHR cache; when payer_id changes, clear payer cache |
| **Impact** | — | Reduces API calls, latency, and cost when a patient has multiple denied claims in the same batch |

## B4. EHR Write-Back (Enhanced)

| Item | Hook/Provision | What Exists Today | What's Needed |
|------|---------------|-------------------|---------------|
| **Disposition tracking** | `claim_disposition` table with ehr_synced flag | Records disposition locally; marks as synced (mock) | Real adapter: `adapter.update_claim_status(disposition)` via Epic/Cerner FHIR API |
| **Payer response update** | `update_payer_response()` function | Updates local DB with payer adjudication result | Automated polling of payer status APIs |
| **Audit trail in EHR** | `sync_to_ehr()` function routes to EMR adapter | Mock marks as synced | Write processing history, appeal letter, confirmation number to patient chart |

## B5. Advanced OCR

| Item | Hook/Provision | What Exists Today | What's Needed |
|------|---------------|-------------------|---------------|
| **AWS Textract** | `extract_text_from_pdf()` has documented swap point | PyMuPDF (primary) + Tesseract (fallback) | Add third strategy: boto3 Textract call for highest-accuracy extraction |
| **Structured extraction** | EOB OCR extracts CARC/RARC/amounts via regex | Regex patterns in `eob_ocr_tool.py` | Textract AnalyzeDocument for table/form extraction from complex EOBs |

## B6. Parallel Batch Processing

| Item | Hook/Provision | What Exists Today | What's Needed |
|------|---------------|-------------------|---------------|
| **Concurrent claims** | `MAX_CONCURRENT_CLAIMS=1` setting exists | Sequential processing | Replace loop with `asyncio.gather` + `Semaphore(max_concurrent)` |
| **Thread safety** | Rate limiter is thread-safe; checkpoint service uses atomic DB writes | — | Ensure all shared state (DB writes, file I/O) is safe under concurrency |

## B7. REST API Layer

| Item | What's Needed |
|------|---------------|
| **FastAPI wrapper** | Expose `process_claim_api()`, `get_queue()`, `approve()`, `get_review_stats()` as REST endpoints |
| **Authentication** | JWT/OAuth2 for API access |
| **Webhook callbacks** | Notify external systems when claim status changes (processed, approved, submitted) |
| **Swagger/OpenAPI** | Auto-generated API documentation |

## B8. Security & Compliance

| Item | What's Needed |
|------|---------------|
| **HIPAA compliance** | PHI access audit logging, encryption at rest (DB + files), BAA with LLM providers |
| **RBAC** | Role-based access control: admin (full), reviewer (approve/re-route/override), viewer (read-only) |
| **Secrets management** | Replace .env with AWS Secrets Manager / HashiCorp Vault for production credentials |
| **Data retention** | Configurable retention policies for audit logs, processed claims, LLM call logs |
| **Encryption** | TLS for all API calls; encrypted SQLite or PostgreSQL with SSL |

## B9. Voice Integration

| Item | Description | Implementation Path |
|------|-------------|-------------------|
| **Voice-to-claim intake** | Billing staff dictates claim details instead of typing or uploading CSV | Speech-to-text (Whisper API) → structured claim extraction (LLM) → `process_claim_api()` |
| **Voice review actions** | Reviewer speaks "approve claim 33001" or "re-route to response agent with notes: add physical therapy records" | Speech-to-text → intent classification (LLM) → map to `approve()` / `re_route()` / `write_off()` API calls |
| **Voice status queries** | "How many claims are pending review?" or "What's the write-off amount for batch B-260416?" | Speech-to-text → query intent → `get_review_stats()` / `get_current_metrics()` → text-to-speech response |
| **IVR integration for payer calls** | Automated IVR navigation when calling payer to check claim status or file verbal appeal | Twilio / Amazon Connect → scripted IVR flows → log call outcome to `claim_disposition` |
| **Provider dictation capture** | Capture provider's clinical justification dictation and inject into appeal letter | Whisper → clinical text → insert into `AppealLetter.clinical_justification` |
| **Suggested integration stack** | Whisper API (speech-to-text), GPT-4o (intent + extraction), ElevenLabs / Azure TTS (text-to-speech), Twilio (telephony) | NiceGUI: add microphone button → WebSocket audio stream → backend processing |

## B10. Analytics & Intelligence

| Item | Description |
|------|-------------|
| **Denial prediction** | ML model trained on historical denials to predict which claims will be denied BEFORE submission — prevent denials proactively |
| **Payer behavior analytics** | Track per-payer approval rates, average response times, most common denial reasons — inform negotiation strategy |
| **Provider scorecards** | Per-provider denial rates, top coding errors, training needs identification |
| **Revenue recovery forecasting** | Predict expected recovery amounts based on denial category, payer, and historical success rates |
| **A/B testing** | Compare LLM model versions via golden dataset regression scores — pick the best model |
| **Trend alerting** | Prometheus alertmanager rules for write-off spikes, LLM cost anomalies, submission failure rate increases |

## B11. Multi-Tenancy

| Item | Description |
|------|-------------|
| **Per-hospital isolation** | Separate databases, SOP collections, and output directories per hospital/organization |
| **Tenant configuration** | Per-tenant settings: payer registries, EHR endpoints, submission methods |
| **Shared infrastructure** | Single deployment serving multiple hospitals with data isolation |
| **Billing/metering** | Track LLM usage per tenant for cost allocation |

## B12. Workflow Enhancements

| Item | Description |
|------|-------------|
| **Escalation paths** | Auto-escalate to Level 2 appeal or peer-to-peer review when Level 1 appeal is denied again |
| **Deadline tracking** | Proactive alerts when appeal filing deadline is approaching (30/15/7 days) |
| **Follow-up automation** | Auto-generate follow-up tasks 30 days after submission if no payer response |
| **Batch prioritization** | Process high-value and urgent claims first within a batch |
| **Template management** | UI for managing appeal letter templates per payer per denial category |

---

## Implementation Priority Matrix

| Priority | Feature | Effort | Business Impact |
|----------|---------|--------|----------------|
| **P0 — Critical** | Real EHR integration (Epic FHIR) | High | Cannot process real claims without patient data |
| **P0 — Critical** | Real payer submission (Availity) | High | Cannot submit to payers without real adapter |
| **P1 — High** | Multi-payer COB | High | Significant revenue recovery opportunity |
| **P1 — High** | HIPAA compliance | High | Required for any production healthcare deployment |
| **P1 — High** | RBAC for web UI | Medium | Required for multi-user production use |
| **P2 — Medium** | Patient data caching | Medium | Performance optimization for large batches |
| **P2 — Medium** | REST API layer | Medium | Enables integration with hospital workflow systems |
| **P2 — Medium** | Parallel batch processing | Medium | Throughput for high-volume hospitals |
| **P2 — Medium** | AWS Textract OCR | Medium | Accuracy improvement for complex scanned EOBs |
| **P3 — Nice to have** | Voice integration | Medium | Workflow efficiency for billing staff |
| **P3 — Nice to have** | Denial prediction | High | Proactive denial prevention |
| **P3 — Nice to have** | Multi-tenancy | High | SaaS deployment model |
| **P3 — Nice to have** | Analytics & intelligence | Medium | Strategic decision support |
