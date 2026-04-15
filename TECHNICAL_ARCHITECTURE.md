# RCM Denial Management — Technical Architecture Document

**Project:** RCM Denial Management — Agentic AI System
**Author:** RK (kvrkr866@gmail.com)
**Version:** 1.0.0
**Last Updated:** April 2026

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Agentic AI Features Used](#2-agentic-ai-features-used)
3. [Design Rationale — Why We Built It This Way](#3-design-rationale--why-we-built-it-this-way)
4. [Multi-Agent Pipeline Architecture](#4-multi-agent-pipeline-architecture)
5. [LLM Integration Details](#5-llm-integration-details)
6. [RAG (Retrieval-Augmented Generation) System](#6-rag-retrieval-augmented-generation-system)
7. [Tool Usage — Agent-Tool Interaction Pattern](#7-tool-usage--agent-tool-interaction-pattern)
8. [Memory and State Management](#8-memory-and-state-management)
9. [Human-in-the-Loop (HITL) Design](#9-human-in-the-loop-hitl-design)
10. [Evaluation System](#10-evaluation-system)
11. [Observability and Cost Tracking](#11-observability-and-cost-tracking)
12. [Data Models and Schema](#12-data-models-and-schema)
13. [Production Hardening](#13-production-hardening)
14. [Technology Stack](#14-technology-stack)
15. [Future Improvements](#15-future-improvements)

---

## System Architecture — End-to-End View

```
 EXTERNAL DATA SOURCES                    AI PIPELINE (LangGraph)                         OUTPUTS & ACTIONS
 =====================                    =======================                         =================

 ┌─────────────────┐     ┌──────────────────────────────────────────────────────────┐
 │  Denied Claims   │     │                                                          │
 │  (CSV / Web UI)  │────►│  1. INTAKE AGENT                                         │
 │                  │     │     Validate claim fields, normalize CARC codes           │
 └─────────────────┘     │                        │                                  │
                          │                        ▼                                  │
 ┌─────────────────┐     │  2. ENRICHMENT AGENT  (parallel fan-out)                  │
 │ Patient Data     │◄───►│     ┌──────┬──────┬──────┬──────┬──────┐                 │
 │ (FHIR/PMS)      │     │     │Patient│Payer │ EHR  │ EOB  │ SOP  │                 │
 └─────────────────┘     │     │ Data  │Policy│ Docs │ OCR  │ RAG  │                 │
                          │     └──┬───┴──┬───┴──┬───┴──┬───┴──┬───┘                 │
 ┌─────────────────┐     │        │      │      │      │      │                      │
 │ Payer Policy     │◄───►│        ▼      ▼      ▼      ▼      ▼                      │
 │ (Availity/CHC)   │     │  3. ANALYSIS AGENT  (LLM + rule-based fallback)          │
 └─────────────────┘     │     Root cause analysis, CARC/RARC interpretation         │
                          │     Denial category + recommended action + confidence      │
 ┌─────────────────┐     │                        │                                  │
 │ Provider EHR     │◄───►│                 [Supervisor Router]                       │
 │ (Epic/Cerner/    │     │          ┌────────┬───┴────┬──────────┐                  │
 │  Athena FHIR)    │     │          ▼        ▼        ▼          ▼                  │
 └─────────────────┘     │     resubmit   appeal    both     write_off              │
                          │          │        │        │          │                   │
 ┌─────────────────┐     │          ▼        ▼        ▼          │                   │
 │ EOB PDFs         │◄───►│  4. EVIDENCE CHECK AGENT  (LLM call 1)                  │
 │ (OCR: PyMuPDF +  │     │     Assess evidence sufficiency, identify gaps            │
 │  Tesseract)      │     │                        │                                  │
 └─────────────────┘     │                ┌───────┴────────┐                         │
                          │                ▼                ▼                         │
 ┌─────────────────┐     │     Evidence OK?          Gaps found?                     │
 │ SOP Knowledge    │◄───►│         │                     │                          │
 │ Base (ChromaDB)  │     │         │              5. TARGETED EHR                   │
 │ Per-payer RAG    │     │         │                 AGENT                           │
 │ ┌─────────────┐ │     │         │                 Fetch labs,                     │
 │ │ SummitHealth │ │     │         │                 imaging,                        │
 │ │ NationalCare│ │     │         │                 pathology                       │
 │ │ Crestview   │ │     │         │                     │                           │
 │ │ Global SOPs │ │     │         ▼                     ▼                           │
 │ └─────────────┘ │     │  6. RESPONSE AGENT  (LLM call 2)                         │
 └─────────────────┘     │     ┌─────────────────┬────────────────┐                  │
                          │     │ CorrectionPlan  │  AppealLetter  │                  │
                          │     │ (code fixes,    │  (clinical     │                  │
                          │     │  documentation) │   justification,│                 │
                          │     │                 │   regulatory    │                  │
                          │     └────────┬────────┴───────┬────────┘                  │
                          │              ▼                ▼                            │
                          │  7. DOCUMENT PACKAGING AGENT                              │     ┌──────────────────┐
                          │     PDF generation + merge + metadata                     │────►│ Output Package    │
                          │                        │                                  │     │ ├─ Analysis.pdf   │
                          │                        ▼                                  │     │ ├─ Correction.pdf │
                          │  8. REVIEW GATE AGENT                                     │     │ ├─ Appeal.pdf     │
                          │     Build AI summary, enqueue for human review             │     │ ├─ MERGED.pdf     │
                          │                                                           │     │ ├─ metadata.json  │
                          └───────────────────────────┬───────────────────────────────┘     │ └─ audit_log.json │
                                                      │                                     └──────────────────┘
                                                      ▼
                          ┌───────────────────────────────────────────────────────────┐
                          │                    HUMAN REVIEW QUEUE                      │
                          │                                                           │
                          │   AI Summary: root cause, evidence confidence,             │
                          │   key arguments, flag reasons, recommended action          │
                          │                                                           │
                          │   Reviewer Actions:                                        │
                          │   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
                          │   │ APPROVE  │ │ RE-ROUTE │ │ HUMAN    │ │  WRITE-OFF  │ │
                          │   │          │ │          │ │ OVERRIDE │ │             │ │
                          │   │ Proceed  │ │ Re-run   │ │ Replace  │ │ Last resort │ │
                          │   │ to payer │ │ from any │ │ AI output│ │ (guarded:   │ │
                          │   │ submit   │ │ pipeline │ │ with own │ │  must try   │ │
                          │   │          │ │ stage +  │ │ letter/  │ │  re-route   │ │
                          │   │          │ │ reviewer │ │ plan     │ │  first)     │ │
                          │   │          │ │ notes    │ │          │ │             │ │
                          │   └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬──────┘ │
                          └────────┼────────────┼────────────┼──────────────┼────────┘
                                   │            │            │              │
                                   │            │            │              ▼
                                   │            │            │     ┌──────────────┐
                                   │            ▼            │     │ Revenue      │
                                   │     ┌──────────────┐    │     │ Impact Log   │
                                   │     │ Re-enter     │    │     │ (write-off   │
                                   │     │ Pipeline at: │    │     │  tracking)   │
                                   │     │ - intake     │    │     └──────────────┘
                                   │     │ - ehr_fetch  │    │
                                   │     │ - response   │    │
                                   │     └──────┬───────┘    │
                                   │            │            │
                                   │            ▼            ▼
                                   │     (re-processes, lands    (re-packages with
                                   │      back in review queue)   human text, lands
                                   │                              back in review queue)
                                   ▼
                          ┌───────────────────────────────────────────────────────────┐
                          │                  PAYER SUBMISSION                          │
                          │                                                           │
                          │   Adapter per payer (from payer_submission_registry):      │
                          │   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
                          │   │   Mock   │ │ Availity │ │   RPA    │ │ EDI 837  │   │
                          │   │  (test)  │ │ REST API │ │ (portal  │ │(clearinghouse)│
                          │   │          │ │          │ │ browser) │ │  SFTP)   │   │
                          │   └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
                          │                                                           │
                          │   Retry: exponential backoff (tenacity)                   │
                          │   Logging: every attempt to submission_log table           │
                          │   Status: poll payer for adjudication result               │
                          └───────────────────────────┬───────────────────────────────┘
                                                      │
                                                      ▼
                          ┌───────────────────────────────────────────────────────────┐
                          │                  OBSERVABILITY                             │
                          │                                                           │
                          │   ┌────────────┐  ┌────────────┐  ┌────────────┐         │
                          │   │ Prometheus │  │  Grafana   │  │   Loki    │         │
                          │   │ (metrics)  │  │ (dashboard)│  │  (logs)   │         │
                          │   └────────────┘  └────────────┘  └────────────┘         │
                          │                                                           │
                          │   9 metric families: claims processed, duration p50/95/99,│
                          │   LLM cost by model, submissions, review queue depth,     │
                          │   write-off revenue impact, eval quality signals           │
                          └───────────────────────────────────────────────────────────┘
```

---

## 1. Executive Summary

This system automates the analysis and resolution of denied medical insurance claims using a **multi-agent AI pipeline**. It processes denied claims from hospitals, determines the root cause of each denial using CARC/RARC codes, retrieves supporting clinical evidence, generates corrective actions (resubmission corrections or formal appeal letters), packages them into submission-ready PDFs, and routes them through human review before submitting to payer portals.

The system is designed as a **near-production** denial management platform that can process real denied claims with real payer SOPs, generate real appeal letters, and track quality metrics — while keeping all external integrations (EHR, payer APIs) swappable via an adapter pattern.

**Key numbers:**
- 10 AI agents in the pipeline
- 6 external data source tools
- 3 LLM call sites with cost tracking
- Per-payer RAG knowledge base (ChromaDB)
- 4 reviewer actions in human-in-the-loop queue
- 4 payer submission adapters
- 22 deterministic eval criteria checks
- 14 golden dataset regression cases
- 9 Prometheus metric families
- 110+ automated tests

---

## 2. Agentic AI Features Used

### 2.1 Multi-Agent Orchestration (LangGraph)

| Feature | Implementation | Purpose |
|---------|---------------|---------|
| **StateGraph** | `denial_graph.py` — LangGraph `StateGraph` with conditional edges | Orchestrates 10 agents in a directed acyclic graph with branching logic |
| **Shared State** | `DenialWorkflowState` (Pydantic v2 model) | Single state object passed through all agents — each reads inputs and writes outputs |
| **Conditional Routing** | `supervisor_router.py` — `_supervisor_route()` | After analysis, routes to resubmit/appeal/both/write_off paths based on LLM recommendation |
| **Node Wrapping** | `_wrap_node()` | Converts between Pydantic models and dicts at each node boundary (LangGraph requires dict state) |

**Why LangGraph over LangChain Agents or CrewAI:**
- LangGraph gives us explicit control over the execution graph — we define exactly which agent runs when and under what conditions
- Medical billing requires deterministic routing (CARC-based), not free-form agent conversation
- State is a structured Pydantic model, not a chat history — every field is typed and validated
- Supports conditional edges (supervisor routing) and re-entry (human review loop) natively

### 2.2 LLM (Large Language Model) Integration

| Feature | Implementation | Purpose |
|---------|---------------|---------|
| **Structured Output** | `ChatOpenAI.with_structured_output(PydanticModel)` | Forces LLM to output valid Pydantic models (DenialAnalysis, EvidenceCheckResult, CorrectionPlan, AppealLetter) — no JSON parsing errors |
| **Multi-call Pipeline** | 3 separate LLM calls per claim | Call 1: evidence assessment, Call 2: response generation (correction + appeal), Call 3 (optional): appeal letter |
| **Rule-based Fallback** | Every LLM agent has a CARC-map fallback | System works fully offline without an OpenAI key — deterministic routing by CARC code |
| **Temperature Control** | `OPENAI_TEMPERATURE=0.1` | Low temperature for consistent, structured medical output — not creative writing |
| **Token Tracking** | `get_openai_callback()` wrapper | Every LLM call records input/output tokens and USD cost to `llm_cost_log` table |
| **Rate Limiting** | Token-bucket rate limiter (`rate_limiter.py`) | Prevents 429 errors during large batch runs |

**Models supported:** gpt-4o (recommended), gpt-4o-mini (lower cost), gpt-4-turbo, gpt-3.5-turbo. Configurable via `OPENAI_MODEL` env var.

### 2.3 RAG (Retrieval-Augmented Generation)

| Feature | Implementation | Purpose |
|---------|---------------|---------|
| **Per-payer Vector Store** | ChromaDB with `sop_{payer_id}` collections | Each payer has its own SOP knowledge base — appeal letters cite payer-specific policies |
| **Embedding Model** | OpenAI `text-embedding-3-small` | Converts SOP text into vectors for semantic search |
| **Semantic Search** | `similarity_search(query, k=5)` | Retrieves the most relevant SOP passages for the denial scenario |
| **Keyword Fallback** | `_MOCK_SOPS` dictionary | When ChromaDB is unavailable, uses keyword matching against built-in SOP snippets |
| **Freshness Check** | Compares SOP file timestamps vs index timestamp | Re-indexes automatically when SOP documents are updated |
| **Pipeline Mode** | `set_pipeline_mode(True)` | Blocks indexing during batch runs — only serves queries, never modifies collections |
| **Manifest Tracking** | `manifest.json` | Records collection health: document count, index timestamp, verification status per payer |

**RAG flow per claim:**
```
Claim (CARC 252, payer=NationalCare)
    → normalize payer: "nationalcare"
    → look up collection: sop_nationalcare
    → query: "CO-252 M127 missing medical record surgical procedure"
    → retrieve top 5 SOP passages
    → inject into LLM prompt as context
    → LLM generates appeal letter citing NationalCare-specific procedures
```

### 2.4 Tools (Agent-Tool Interaction)

| Tool | Agent That Uses It | Data Source | Real Integration Path |
|------|-------------------|-------------|----------------------|
| `patient_data_tool` | Enrichment Agent | Patient demographics + insurance | FHIR R4 `/Patient` + `/Coverage` |
| `payer_policy_tool` | Enrichment Agent | Payer billing guidelines + auth rules | Availity / Change Healthcare API |
| `ehr_tool` | Enrichment Agent | Clinical documentation (encounter notes, procedures) | Epic/Cerner/Athena FHIR |
| `eob_ocr_tool` | Enrichment Agent | EOB PDF text extraction | PyMuPDF (digital) + Tesseract (scanned) + AWS Textract (future) |
| `sop_rag_tool` | Enrichment Agent | Payer-specific SOP procedures | ChromaDB vector search |
| `clinical_ocr_tool` | Enrichment Agent | Clinical PDF text extraction | PyMuPDF / Tesseract |

**Tool architecture:** All tools are plain Python functions (not LangChain `Tool` objects) called by the enrichment agent via `asyncio.gather()` for parallel execution. Each tool has a mock implementation that returns realistic test data and a documented integration path for real systems.

### 2.5 Memory and State

| Type | Implementation | Scope | Purpose |
|------|---------------|-------|---------|
| **Pipeline State** | `DenialWorkflowState` | Per-claim | Full state passed through all agents — claim data, analysis, evidence, corrections, appeal, audit log |
| **Checkpointing** | `claim_checkpoint` table | Per-node per-claim | Crash recovery — resume from last completed node after batch failure |
| **SOP Collection Cache** | Module-level `_collection_cache` dict | Per-batch (process lifetime) | Avoids re-loading ChromaDB collections for every claim |
| **Review Queue State** | `state_snapshot` JSON column | Per-claim | Full `DenialWorkflowState` serialized in queue — enables re-entry at any pipeline stage |
| **Audit Trail** | `audit_log` list in state + `claim_audit_log` DB table | Per-claim | Every node records start/complete/fail with duration and token usage |

**Note:** Patient/EHR/payer data is NOT cached across claims within a batch today. This is a planned optimization — when the next claim belongs to the same patient, reuse already-fetched data instead of re-calling external APIs.

### 2.6 Human-in-the-Loop (HITL)

| Feature | Implementation | Purpose |
|---------|---------------|---------|
| **Non-blocking Review** | Pipeline completes fully, then enqueues for review | AI work is never blocked waiting for humans |
| **AI Summary** | `_build_ai_summary()` generates decision-ready text | Reviewer sees root cause, evidence confidence, key arguments, flag reasons |
| **4 Actions** | approve / re_route / human_override / write_off | Covers all possible reviewer decisions |
| **Pipeline Re-entry** | `pipeline_reentry.py` — re-runs from chosen stage | Reviewer notes are injected into LLM prompts on re-run |
| **Write-off Guard** | Blocked unless re-route attempted first | Prevents premature revenue loss — forces human to try alternatives first |
| **Bulk Approve** | `bulk_approve()` with confidence/amount thresholds | Auto-approves low-risk claims — reduces reviewer workload |

### 2.7 Evaluation System

| Feature | Implementation | Purpose |
|---------|---------------|---------|
| **Continuous Eval** | Reviewer actions as ground truth | First-pass approval rate, override rate, confidence calibration — derived from reviewer behavior |
| **Criteria Checks** | 22 deterministic structural assertions | Validates LLM output structure without calling LLM — free, fast, always deterministic |
| **Golden Dataset** | 14 labeled cases covering all 7 denial categories | Regression testing — ensures model changes don't break known-good outputs |
| **LLM-as-Judge** | `evaluator.py` — 5-metric composite scoring | Appeal letter quality, classification accuracy, document completeness, CARC interpretation, latency |

---

## 3. Design Rationale — Why We Built It This Way

### 3.1 Why Multi-Agent (not Single-Agent or Chain)

**Problem:** Denial management involves multiple distinct cognitive tasks — data validation, evidence retrieval, root cause analysis, correction planning, appeal writing, document packaging. A single LLM call cannot do all of these well.

**Decision:** Separate agents with single responsibilities, orchestrated by LangGraph.

**Rationale:**
- Each agent has a clear input/output contract (Pydantic models)
- Agents can be individually tested, evaluated, and improved
- Some agents need LLM (analysis, evidence, response), others don't (intake, enrichment, packaging)
- Conditional routing (supervisor) is explicit, not emergent
- Failure in one agent doesn't crash the whole pipeline — errors are captured in state

### 3.2 Why LangGraph (not LangChain Agents, CrewAI, AutoGen)

| Framework | Why Not |
|-----------|---------|
| **LangChain AgentExecutor** | Free-form tool-calling loop — too unpredictable for structured medical workflows |
| **CrewAI** | Designed for role-based collaboration — our agents don't "talk" to each other, they process sequentially |
| **AutoGen** | Multi-agent conversation — overkill for a directed pipeline; hard to control output format |
| **Custom (no framework)** | Would work but lose graph visualization, state management, and conditional routing primitives |

**LangGraph fits because:** We need a deterministic directed graph with conditional edges, shared typed state, and human-in-the-loop interrupt points — exactly what LangGraph provides.

### 3.3 Why Structured Output (not Free-Text LLM)

**Problem:** LLM responses are unpredictable. A free-text appeal letter might miss required sections. A free-text analysis might not include a valid action recommendation.

**Decision:** All LLM outputs are forced into Pydantic models via `with_structured_output()`.

**Benefits:**
- `DenialAnalysis` has typed fields: `recommended_action: Literal["resubmit", "appeal", "both", "write_off"]` — the LLM cannot return "maybe resubmit" or "I think appeal"
- `confidence_score: float = Field(ge=0.0, le=1.0)` — always a valid number in range
- `AppealLetter` has separate sections (subject_line, clinical_justification, regulatory_basis) — each can be independently evaluated
- Downstream agents (packaging, submission) can rely on the structure without defensive parsing

### 3.4 Why Rule-Based Fallback (not LLM-Only)

**Problem:** LLM API may be unavailable (network, rate limit, cost), and medical billing has well-known deterministic rules (CARC code mappings).

**Decision:** Every LLM agent has a rule-based fallback path that activates when the LLM call fails or when no API key is configured.

**CARC fallback map example:**
```python
"97":  {"action": "resubmit", "category": "coding_error", "correction_possible": True}
"252": {"action": "appeal",   "category": "other",        "correction_possible": False}
"16":  {"action": "resubmit", "category": "coding_error", "correction_possible": True}
```

**Benefits:**
- System works fully offline — useful for testing, demos, and CI/CD
- LLM enhances quality but isn't a single point of failure
- Deterministic routing can be validated against known-good outcomes

### 3.5 Why Per-Payer RAG (not Single Knowledge Base)

**Problem:** Different payers have different procedures, filing deadlines, portal URLs, and documentation requirements. A single global SOP would give generic advice.

**Decision:** One ChromaDB collection per payer (`sop_summithealth`, `sop_nationalcare`, `sop_crestviewhealth`) plus a `global` collection for universal SOPs.

**Benefits:**
- Appeal letters cite payer-specific procedures (e.g., "submit via provider.summithealth.example within 180 days")
- Different payers have different filing deadlines (NationalCare: 120 days, others: 180 days)
- Global SOPs provide fallback for payers without specific collections
- New payer onboarding = drop a folder of documents + `rcm-denial init`

### 3.6 Why Non-Blocking Human Review (not Inline Approval)

**Problem:** If the pipeline pauses mid-execution waiting for human approval, batch processing would be extremely slow and the reviewer would need to be online simultaneously.

**Decision:** Pipeline completes fully (AI generates the best output it can), then enqueues for review. Human reviews asynchronously.

**Benefits:**
- A batch of 100 claims processes in minutes, not days
- Reviewer works on their own schedule — could be hours later
- AI summary gives reviewer all context needed to decide in 30 seconds
- Re-route option means the reviewer can always fix mistakes

### 3.7 Why 4 Review Actions (not Just Approve/Reject)

| Action | Why It Exists |
|--------|---------------|
| **Approve** | Happy path — AI got it right, submit to payer |
| **Re-route** | AI was partially wrong — re-run from a specific pipeline stage with reviewer guidance (notes injected into LLM prompt) |
| **Human Override** | AI was completely wrong — reviewer writes their own appeal letter or correction plan, replacing AI output entirely |
| **Write-off** | Claim is genuinely unrecoverable — but guarded: must attempt re-route first to prevent premature revenue loss |

A simple approve/reject binary would lose the nuance of "AI was 80% right but needs more clinical evidence" (re-route) vs "AI was completely wrong, I'll write this myself" (override).

### 3.8 Why Write-Off Guard

**Problem:** In real hospital billing, writing off a $48,000 claim should never be a one-click decision. The easiest path for an overwhelmed reviewer is to write off difficult claims — but each write-off is lost revenue.

**Decision:** Write-off is blocked unless `review_count >= 1` (re-route was attempted) OR reason is `timely_filing_expired` (genuinely no path forward). Manager can force with `force=True`.

**Result:** Every claim gets at least two chances — the initial AI attempt and one re-route with human guidance — before it can be written off.

---

## 4. Multi-Agent Pipeline Architecture

### Pipeline Execution Order

```
1. intake_agent           Validate claim fields, normalize CARC codes
        │
2. enrichment_agent       Parallel fan-out to 5 data tools (asyncio.gather)
        │
3. analysis_agent         LLM structured output → DenialAnalysis
        │                   (root cause, category, action, confidence)
        │
   [supervisor_route]     Conditional routing based on recommended_action
        │
        ├── write_off ──────────────────────────────► 7. document_packaging_agent
        │
4. evidence_check_agent   LLM call 1: evidence sufficiency assessment
        │
        ├── needs EHR ──► 5. targeted_ehr_agent (fetch labs/imaging/pathology)
        │
6. response_agent         LLM call 2: generate CorrectionPlan and/or AppealLetter
        │
7. document_packaging_agent  PDF generation + merge + metadata JSON
        │
8. review_gate_agent      Build AI summary, enqueue for human review
```

### Node Wrapper Pattern

Every agent function receives and returns a `DenialWorkflowState` Pydantic model. LangGraph requires dict state, so `_wrap_node()` handles conversion:

```python
def _wrap_node(agent_fn):
    def wrapped(state_dict: dict) -> dict:
        state = DenialWorkflowState(**state_dict)  # dict → Pydantic
        # Check checkpoint (crash recovery)
        # Run agent
        updated_state = agent_fn(state)
        # Save checkpoint
        return updated_state.model_dump()           # Pydantic → dict
    return wrapped
```

This pattern also handles checkpointing — on restart, if a node was already completed, the wrapper returns the cached state and skips execution.

---

## 5. LLM Integration Details

### Three LLM Call Sites

| Call | Agent | Input | Output Model | Fallback |
|------|-------|-------|-------------|----------|
| **LLM Call 1** | `evidence_check_agent` | Claim + enriched data + analysis | `EvidenceCheckResult` | Skip evidence check, assume sufficient |
| **LLM Call 2** | `response_agent` | Claim + evidence + analysis + SOP context | `CorrectionPlan` and/or `AppealLetter` | Rule-based correction plan + template letter |
| **LLM Call 3** (optional) | `appeal_prep_agent` | Claim + correction plan + evidence | `AppealLetter` | Template-based letter |

### Prompt Engineering Strategy

Each LLM call uses a structured prompt with these sections:
1. **System instruction** — role, output format requirements
2. **Claim context** — all claim fields, CARC/RARC codes, billed amount
3. **Evidence context** — patient data, EHR records, payer policy
4. **SOP context** — retrieved RAG passages from per-payer collection
5. **Output schema** — Pydantic model fields with descriptions

The LLM is instructed to output a JSON object matching the Pydantic schema. `with_structured_output()` enforces this at the API level.

### Cost Tracking

Every LLM call is wrapped with token tracking:

```python
from langchain_community.callbacks import get_openai_callback
with get_openai_callback() as cb:
    result = structured_llm.invoke(prompt)
input_tokens = cb.prompt_tokens
output_tokens = cb.completion_tokens

record_llm_call(
    run_id=state.run_id, batch_id=state.batch_id,
    agent_name="evidence_check_agent", model="gpt-4o",
    input_tokens=input_tokens, output_tokens=output_tokens,
)
```

Pricing table covers gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-3.5-turbo, and embedding models. Unknown models use a conservative default rate.

---

## 6. RAG (Retrieval-Augmented Generation) System

### Architecture

```
data/sop_documents/
├── global/              → sop_global collection (applies to all payers)
├── summithealth/        → sop_summithealth collection
├── nationalcare/        → sop_nationalcare collection
└── crestviewhealth/     → sop_crestviewhealth collection
                                    │
                                    ▼
                            ChromaDB (persisted)
                            data/chroma_db/
                                    │
                                    ▼
                        Enrichment Agent queries:
                        "CO-252 M127 missing medical record
                         surgical procedure knee replacement"
                                    │
                                    ▼
                        Top 5 passages returned
                        (filtered by min_relevance_score=0.3)
                                    │
                                    ▼
                        Injected into LLM prompt as SOP context
```

### Collection Management

| Operation | Command | What It Does |
|-----------|---------|-------------|
| Build all | `rcm-denial init --verify` | Discovers all payer folders, indexes documents, runs test queries |
| Build one | `rcm-denial init --payer NationalCare` | Indexes one payer's folder only |
| Check coverage | `rcm-denial init --check-only` | Reports which payers have collections without indexing |
| Refresh stale | Automatic (if `SOP_RAG_FRESHNESS_CHECK=true`) | Re-indexes when SOP files are newer than the collection |
| Pipeline mode | `set_pipeline_mode(True)` during batch | Never index during a batch run — only serve queries |

### Document Formats Supported

SOP documents can be `.txt`, `.pdf`, or `.md` files. PDF text is extracted via PyMuPDF. Each document is chunked and embedded using OpenAI `text-embedding-3-small`.

---

## 7. Tool Usage — Agent-Tool Interaction Pattern

### Enrichment Fan-Out (Parallel)

The enrichment agent calls all 5 data tools simultaneously using `asyncio.gather()`:

```python
results = await asyncio.gather(
    _fetch_patient(patient_id, claim),    # → PatientData
    _fetch_payer(payer_id, cpt_codes),    # → PayerPolicy
    _fetch_ehr(provider_id, patient_id),  # → EhrData
    _fetch_eob(eob_pdf_path),            # → EobExtractedData
    _fetch_sop(payer_id, carc_code),     # → list[SopResult]
)
```

Each fetch is wrapped in try/except — partial failure is tolerated. If EHR fetch fails but everything else succeeds, the pipeline continues with what it has.

### Adapter Pattern for External Systems

All external integrations use a pluggable adapter pattern:

```python
# settings.py
emr_adapter: str = "mock"    # mock | epic | cerner | athena

# data_source_adapters.py
def get_emr_adapter(adapter_name: str) -> BaseEMRAdapter:
    if adapter_name == "mock":    return MockEMRAdapter()
    if adapter_name == "epic":    return EpicFHIRAdapter()
    if adapter_name == "cerner":  return CernerFHIRAdapter()
```

Switching from mock to real is a config change, not a code change.

### OCR Dual-Strategy

```
PDF Input
    │
    ├── Try PyMuPDF first (fitz)
    │   Extract embedded text layer
    │   If >= 50 chars → use it (confidence: 0.95)
    │
    └── Fallback to Tesseract OCR
        Convert PDF → images → OCR
        Returns text + word-level confidence
```

PyMuPDF is ~10x faster and more accurate for digital PDFs (most EOBs are generated digitally, not scanned).

---

## 8. Memory and State Management

### Per-Claim State (`DenialWorkflowState`)

The central state object carries everything about a claim through the pipeline:

```python
class DenialWorkflowState(BaseModel):
    # Immutable inputs
    claim: ClaimRecord
    run_id: str
    batch_id: str

    # Agent outputs (populated as pipeline progresses)
    enriched_data: Optional[EnrichedData]
    denial_analysis: Optional[DenialAnalysis]
    evidence_check: Optional[EvidenceCheckResult]
    correction_plan: Optional[CorrectionPlan]
    appeal_package: Optional[AppealPackage]
    output_package: Optional[SubmissionPackage]

    # Human-in-the-loop
    human_notes: str            # reviewer guidance for re-runs
    review_count: int           # how many HITL cycles
    is_human_override: bool     # response was written by human, not AI

    # Control flow
    routing_decision: str       # resubmit | appeal | both | write_off
    current_node: str
    errors: list[str]
    audit_log: list[AuditEntry]
```

### Checkpoint Recovery

After each node completes, the full state is serialized to `claim_checkpoint` table:

```
claim_checkpoint
├── run_id
├── claim_id
├── last_completed_node = "analysis_agent"
├── node_index = 2
├── state_snapshot = <full JSON>
├── status = "in_progress"
```

On batch restart, `_wrap_node()` checks if the node was already completed. If so, it loads the saved state and skips execution — zero wasted LLM cost.

### Review Queue State Preservation

When a claim enters the review queue, the full `DenialWorkflowState` is serialized into `state_snapshot`. This enables:
- **Re-route:** Load state, inject reviewer notes, re-run from chosen node
- **Human override:** Load state, replace AI output, re-package
- **State inspection:** Full claim history available in the claim detail page

---

## 9. Human-in-the-Loop (HITL) Design

### Why Every Claim Goes Through Review

In medical billing, even a well-crafted AI appeal letter should be verified by a human before submission to a payer. The cost of submitting an incorrect appeal (wasted time, payer relationship damage, compliance risk) outweighs the cost of human review.

### AI Summary Generation

The review gate agent generates a structured summary for each claim:

```
CLAIM: CLM-33001  |  Payer: Summit Health  |  CARC: CO-252  |  Amount: $4,250.00
Category: other  |  Action: APPEAL  |  Package: APPEAL

Root cause: Missing patient medical record (M127) — operative report needed
Evidence confidence: 85%  |  Sufficient: Yes
Key arguments:
  - Surgical procedure 27447 requires operative report per payer SOP
  - Service date documentation available in provider EHR

Flagged for review because:
  - High-value claim (> $1,000)
  - Missing documentation denial — verify document retrieval
```

### Review Cycle Flow

```
Initial pipeline run → review_count = 0, status = "pending"
        │
Reviewer: re-route to response_agent with notes
        │
Re-run pipeline from response_agent → review_count = 1, status = "re_processed"
        │
Reviewer: approve
        │
Submit to payer → status = "submitted"
```

---

## 10. Evaluation System

### Three-Layer Evaluation Strategy

| Layer | Type | Cost | When | What It Measures |
|-------|------|------|------|-----------------|
| **Continuous** | Reviewer actions as ground truth | Free | Every claim | First-pass approval rate, override rate, confidence calibration |
| **Structural** | Deterministic criteria checks | Free | On demand / CI | Are LLM outputs structurally valid and internally consistent? |
| **Regression** | Golden dataset | Free (or LLM cost for full eval) | Before model changes | Does the system still handle all 7 denial categories correctly? |

### Continuous Eval Metrics

Derived from the review queue — no additional data collection needed:

| Metric | Formula | Target | What It Tells You |
|--------|---------|--------|-------------------|
| **First-pass approval rate** | approved(review_count=0) / total | > 70% | How often AI gets it right without human intervention |
| **Override rate** | human_override / total | < 5% | How often AI output is completely replaced — strong failure signal |
| **Re-route rate by stage** | re_routed(stage=X) / total_rerouted | — | Which pipeline stage fails most — where to invest improvement |
| **Confidence calibration** | avg_confidence(approved) - avg_confidence(rerouted) | > 0 | Is the LLM's self-assessed confidence meaningful? Positive gap = well-calibrated |
| **Multi-cycle claims** | Claims with review_count > 1 | Minimize | Natural eval dataset — these are the hardest cases |

### Deterministic Criteria Checks (22 total)

**DenialAnalysis checks (7):**
- Valid action enum (resubmit/appeal/both/write_off)
- Valid category enum (7 denial categories)
- Confidence score in [0.0, 1.0]
- Root cause non-empty (>= 10 chars)
- Action-category consistency (e.g., write_off never for timely_filing)
- Correction flag consistency (write_off implies correction_possible=False)
- Reasoning is substantive (>= 30 chars)

**AppealLetter checks (6):**
- Subject line present
- Clinical justification contains medical keywords
- Regulatory basis cites policies/guidelines
- Professional closing present (sincerely/respectfully)
- Minimum length (200 chars)
- No unfilled placeholders ([INSERT], [TBD], etc.)

**EvidenceCheckResult checks (5):**
- evidence_sufficient is boolean
- At least one key argument
- Action confirmed is valid enum
- Gaps/fetch flag advisory consistency
- Confidence in range

**CorrectionPlan checks (4):**
- Valid plan type
- Valid code correction types (CPT/ICD10/HCPCS/modifier)
- Non-empty corrected codes
- Resubmission has content

### Golden Dataset

14 labeled cases covering all 7 denial categories and all 4 recommended actions:

| Category | Cases | Actions Covered |
|----------|-------|----------------|
| Timely filing | 2 | resubmit, appeal (expired window edge case) |
| Medical necessity | 2 | appeal, appeal (high-value inpatient) |
| Prior auth | 2 | appeal, appeal (retroactive auth) |
| Duplicate claim | 1 | resubmit |
| Coding error | 3 | resubmit (CPT unbundling, ICD invalid, missing modifier) |
| Eligibility | 2 | resubmit, resubmit (retroactive reinstatement) |
| Coordination of benefits | 1 | both |
| Write-off (small balance) | 1 | write_off |

---

## 11. Observability and Cost Tracking

### Prometheus Metrics (9 families)

| Metric | Type | Labels | What It Tracks |
|--------|------|--------|---------------|
| `rcm_denial_claims_processed` | counter | status, package_type | Total claims by outcome |
| `rcm_denial_duration_ms` | histogram | | Processing time percentiles (p50/p95/p99) |
| `rcm_denial_llm_cost_usd` | gauge | model | LLM spend by model |
| `rcm_denial_llm_calls_total` | counter | agent_name | Call count per agent |
| `rcm_denial_submissions` | counter | method, status | Submission outcomes |
| `rcm_denial_review_queue_depth` | gauge | status | Queue size by status |
| `rcm_denial_write_off_amount_usd` | gauge | reason | Revenue impact |
| `rcm_denial_write_off_count` | counter | reason | Write-off count by reason |
| `rcm_denial_review_first_pass_rate` | gauge | | Eval quality signal |

### Grafana Dashboard

Pre-built dashboard with 6 panels: Pipeline Overview, Duration Percentiles, LLM Cost by Model, Review Queue Depth, Submission Success Rate, Write-Off Revenue Impact.

### Log Aggregation

Structured JSON logging via `structlog` shipped to Loki via Promtail. Every log entry includes `claim_id`, `batch_id`, `node_name` as labels for filtering.

---

## 12. Data Models and Schema

### Pydantic Models (6 model files)

| Model | File | Purpose |
|-------|------|---------|
| `ClaimRecord` | `claim.py` | Input claim with 40+ fields (required + optional) |
| `EnrichedData` | `claim.py` | Aggregated output from 5 enrichment tools |
| `DenialAnalysis` | `analysis.py` | LLM-structured root cause analysis |
| `EvidenceCheckResult` | `analysis.py` | Evidence sufficiency assessment |
| `CorrectionPlan` | `analysis.py` | Code corrections + documentation checklist |
| `AppealLetter` | `appeal.py` | Formal appeal with sections (subject, clinical, regulatory, closing) |
| `DenialWorkflowState` | `output.py` | Full pipeline state (passed through all agents) |
| `SubmissionPackage` | `output.py` | Final output artifact per claim |
| `SubmissionResult` | `submission.py` | Payer submission response |

### Database Tables (8 tables)

| Table | Purpose |
|-------|---------|
| `claim_intake_log` | CSV row validation results |
| `claim_audit_log` | Per-node processing audit trail |
| `claim_pipeline_result` | Final pipeline outcomes |
| `claim_checkpoint` | Per-node state for crash recovery |
| `human_review_queue` | Review queue with full state snapshots |
| `payer_submission_registry` | Per-payer submission method config |
| `submission_log` | Every submission attempt (success/failure) |
| `llm_cost_log` | Per-call LLM token usage and cost |

---

## 13. Production Hardening

| Feature | Status | Detail |
|---------|--------|--------|
| Rate limiting | Built | Token-bucket on LLM calls (configurable RPM + burst) |
| OCR upgrade | Built | PyMuPDF primary + Tesseract fallback |
| Crash recovery | Built | Per-node checkpointing with resume |
| Web auth | Built | Username/password login (NiceGUI session-based) |
| CI/CD | Built | GitHub Actions: lint + test + Docker build |
| Docker | Built | Dockerfile + docker-compose.yml (app + monitoring stack) |
| PostgreSQL | Built | Production DB option with one-command migration from SQLite |
| Idempotency | Built | Batch processor skips already-completed claims |
| Structured logging | Built | structlog JSON + Loki aggregation |

---

## 14. Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| **AI Orchestration** | LangGraph | >= 0.2.0 | Multi-agent pipeline with conditional routing |
| **LLM** | OpenAI GPT-4o | via langchain-openai | Structured analysis, evidence check, response generation |
| **Embeddings** | OpenAI text-embedding-3-small | via langchain-openai | SOP document vectorization |
| **Vector Store** | ChromaDB | >= 0.5.0 | Per-payer SOP RAG collections |
| **Data Models** | Pydantic v2 | >= 2.7.0 | Typed models for all pipeline state |
| **Configuration** | pydantic-settings | >= 2.3.0 | Environment-driven config with validation |
| **PDF Generation** | reportlab + pypdf | >= 4.2.0 | Analysis reports, appeal letters, merged packages |
| **OCR** | PyMuPDF + pytesseract | >= 1.24.0 | Digital + scanned PDF text extraction |
| **CLI** | Click + Rich | >= 8.1.7 | 30+ commands with formatted output |
| **Web UI** | NiceGUI | >= 2.0.0 | Browser-based operator console |
| **Database** | SQLite (default) / PostgreSQL | | Claim data, review queue, metrics |
| **Retry** | tenacity | >= 8.3.0 | Exponential backoff for submissions |
| **Logging** | structlog | >= 24.1.0 | Structured JSON logging |
| **Metrics** | Prometheus + Grafana | | Metrics collection and dashboards |
| **Logs** | Loki + Promtail | | Log aggregation and search |
| **Testing** | pytest | >= 8.2.0 | 110+ automated tests |
| **Linting** | ruff | >= 0.4.0 | Code quality |
| **Container** | Docker + Docker Compose | | Deployment |
| **CI/CD** | GitHub Actions | | Automated lint + test + build |

---

## 15. Future Improvements

### High Priority (planned)

| Feature | Description | Effort |
|---------|-------------|--------|
| **Multi-payer COB** | When primary denies, check if secondary payer covers the service category; auto-generate secondary claim | High |
| **Policy discovery service** | `policy_source_registry` table — look up all active policies from EHR, payer APIs, clearinghouses | High |
| **Patient data caching** | Cache patient/EHR/payer data within a batch; reuse when same patient appears in multiple claims | Medium |
| **Real EHR integration** | Implement Epic FHIR adapter (most common US hospital EHR) | High |
| **Real payer integration** | Implement Availity REST API adapter (largest US multi-payer clearinghouse) | High |

### Medium Priority

| Feature | Description | Effort |
|---------|-------------|--------|
| **AWS Textract OCR** | Third OCR strategy for high-accuracy scanned document extraction | Medium |
| **FastAPI REST wrapper** | API layer over `process_claim_api()` for microservice deployment | Medium |
| **Parallel batch processing** | `asyncio.gather` with semaphore for concurrent claim processing | Medium |
| **RBAC** | Role-based access control (admin, reviewer, viewer) for web UI | Medium |
| **Payer portal RPA** | Playwright browser automation for portal submission | High |

### Lower Priority

| Feature | Description | Effort |
|---------|-------------|--------|
| **A/B testing** | Compare model versions via golden dataset regression scores | Low |
| **Alerting** | Prometheus alertmanager for write-off spikes, LLM cost anomalies | Low |
| **HIPAA compliance** | PHI access audit logging, encryption at rest, BAA with LLM providers | High (compliance) |
| **Multi-tenancy** | Per-hospital isolation with separate databases and SOP collections | High |
| **Real-time WebSocket** | Replace polling with WebSocket push for pipeline progress | Medium |

---

## 16. Project Structure

```
rcm_denial_proto/
├── pyproject.toml                          # Dependencies, entry point: rcm_denial.main:cli
├── Dockerfile                              # Production container (Python 3.11 + Tesseract + OCR)
├── docker-compose.yml                      # Full stack: app + Prometheus + Grafana + Loki
├── .dockerignore                           # Excludes .venv, __pycache__, .env, output, logs
├── .env.example                            # Full environment variable template (40+ vars)
├── railway.json                            # Railway.app deployment config
├── render.yaml                             # Render.com blueprint
│
├── src/rcm_denial/
│   ├── main.py                             # CLI (click) -- 30+ commands + programmatic API
│   ├── config/settings.py                  # Pydantic-settings singleton (all env vars)
│   ├── models/                             # Pydantic v2 data models
│   │   ├── claim.py                        # ClaimRecord, EnrichedData, PatientData, PayerPolicy,
│   │   │                                   #   EhrData, DiagnosticReport, EobExtractedData, SopResult
│   │   ├── analysis.py                     # DenialAnalysis, EvidenceCheckResult, CorrectionPlan
│   │   ├── appeal.py                       # AppealPackage, AppealLetter, SupportingDocument
│   │   ├── output.py                       # DenialWorkflowState, SubmissionPackage, AuditEntry
│   │   └── submission.py                   # SubmissionResult, SubmissionStatus
│   ├── agents/                             # 10 LangGraph agent nodes
│   │   ├── intake_agent.py                 # Validate ClaimRecord; non-blocking
│   │   ├── enrichment_agent.py             # Async fan-out to 5 tools via asyncio.gather
│   │   ├── analysis_agent.py               # LLM structured output; rule-based CARC-map fallback
│   │   ├── evidence_check_agent.py         # LLM call 1: evidence sufficiency + cost tracking
│   │   ├── targeted_ehr_agent.py           # Stage 2 EHR fetch for diagnostics
│   │   ├── response_agent.py              # LLM call 2: CorrectionPlan + AppealLetter + cost tracking
│   │   ├── correction_plan_agent.py        # Code corrections + documentation checklist
│   │   ├── appeal_prep_agent.py            # Formal appeal letter
│   │   ├── document_packaging_agent.py     # PDF generation + merge
│   │   └── review_gate_agent.py            # Enqueue for human review
│   ├── tools/                              # 6 data source tools (mock -> real integration)
│   │   ├── patient_data_tool.py            # Mock -> FHIR R4 patient/coverage
│   │   ├── payer_policy_tool.py            # Mock -> Availity/Change Healthcare
│   │   ├── ehr_tool.py                     # Mock -> Epic/Cerner FHIR
│   │   ├── clinical_ocr_tool.py            # PDF text extraction
│   │   ├── eob_ocr_tool.py                # PyMuPDF (digital) + Tesseract (scanned)
│   │   └── sop_rag_tool.py                # Per-payer ChromaDB RAG + keyword fallback
│   ├── workflows/                          # Pipeline orchestration
│   │   ├── denial_graph.py                 # LangGraph StateGraph + _wrap_node + checkpointing
│   │   ├── supervisor_router.py            # Conditional edge routing logic
│   │   └── batch_processor.py              # Batch engine with SOP pre-flight + metrics
│   ├── services/                           # 14 service modules
│   │   ├── audit_service.py                # structlog configuration
│   │   ├── pdf_service.py                  # reportlab PDF generation + pypdf merge
│   │   ├── claim_intake.py                 # CSV parsing, DB table init, pipeline result logging
│   │   ├── data_source_adapters.py         # EMR/PMS/Payer adapter factory
│   │   ├── sop_ingestion.py                # Per-payer SOP indexing, manifest, coverage check
│   │   ├── review_queue.py                 # Human review queue: enqueue, approve, re_route, write_off
│   │   ├── review_queue_helpers.py         # Flag reasons for review
│   │   ├── pipeline_reentry.py             # Re-entry paths after reviewer actions
│   │   ├── submission_adapters.py          # Mock/Availity/RPA/EDI adapters + payer registry
│   │   ├── submission_service.py           # Submit with retry (tenacity), submission log
│   │   ├── cost_tracker.py                 # LLM cost tracking per call/claim/batch
│   │   ├── metrics_service.py              # Prometheus .prom export + Pushgateway push
│   │   ├── db_backend.py                   # SQLite/PostgreSQL connection factory + migration
│   │   ├── checkpoint_service.py           # Per-node crash recovery checkpointing
│   │   └── rate_limiter.py                 # Token-bucket LLM rate limiter
│   ├── evaluation/evaluator.py             # 5-metric LLM-as-judge evaluation
│   ├── evals/criteria_checks.py            # 22 deterministic structural assertions
│   └── web/                                # NiceGUI web UI (optional, fully separate)
│       ├── app.py                          # Entry point, layout shell, navigation, static files
│       ├── auth.py                         # Username/password login
│       └── pages/
│           ├── dashboard.py                # Landing page with metric cards + live stats
│           ├── process.py                  # Three-panel operator console + pipeline stepper
│           ├── review.py                   # Queue table + action buttons with dialogs
│           ├── claim_detail.py             # Full claim view + audit trail + PDF download
│           ├── stats.py                    # Stats dashboard mirroring `rcm-denial stats`
│           └── evals.py                    # Golden dataset runner, quality signals
│
├── data/
│   ├── sample_denials.csv                  # 5 sample denied claims
│   ├── demo_denials.csv                    # 4 claims matching real payer SOPs + EOBs
│   ├── carc_rarc_reference.json            # CARC/RARC code reference
│   ├── sop_documents/                      # Per-payer SOP folders + manifest.json
│   │   ├── global/                         # SOPs applying to all payers
│   │   ├── crestviewhealth/                # Crestview Health SOPs (CO-16, CO-252)
│   │   ├── nationalcare/                   # NationalCare SOPs (CO-16, CO-252)
│   │   └── summithealth/                   # Summit Health SOPs (CO-16, CO-252)
│   ├── eob_pdfs/                           # EOB PDF files referenced from CSV
│   ├── evals/golden_cases.json             # 14 labeled golden cases
│   └── observability/                      # Docker Compose + Grafana + Prometheus + Loki configs
│
├── tests/                                  # 9 test files, 110+ tests
│   ├── conftest.py                         # Shared fixtures (DB isolation, SOP isolation)
│   ├── test_agents.py                      # Phase 1 agent unit tests
│   ├── test_tools.py                       # Phase 1 mock data tool tests
│   ├── test_batch_processor.py             # Phase 1 batch integration tests
│   ├── test_review_queue.py                # 15 tests -- queue actions, write-off guard, eval stats
│   ├── test_submission.py                  # 14 tests -- adapters, registry, retry logic
│   ├── test_cost_tracker.py                # 17 tests -- cost calculation, recording, summaries
│   ├── test_sop_pipeline_mode.py           # 17 tests -- pipeline mode, manifest, coverage
│   ├── test_criteria_checks.py             # 24 tests -- structural assertions, golden dataset
│   └── test_rate_limiter.py                # 6 tests -- token bucket, throttle, burst
│
├── .github/workflows/ci.yml               # GitHub Actions: lint + test + Docker build
├── output/                                 # Generated submission packages (per claim)
└── logs/                                   # Structured JSON logs (structlog)
```

---

## 17. Complete CLI Reference

### Core Pipeline

```
rcm-denial process-batch <csv>
    --batch-id TEXT          Optional batch identifier (auto-generated if empty)
    --no-skip                Re-process already completed claims
    --source-label TEXT      Human-readable label for this CSV source
    --output-dir TEXT        Override output directory (default: ./output)

rcm-denial process-claim
    --claim-id TEXT          [required]
    --patient-id TEXT        [required]
    --payer-id TEXT          [required]
    --provider-id TEXT       [required]
    --dos TEXT               Date of service YYYY-MM-DD [required]
    --cpt TEXT               CPT codes, comma-separated [required]
    --dx TEXT                Diagnosis codes, comma-separated [required]
    --denial-reason TEXT     [required]
    --carc TEXT              CARC code [required]
    --rarc TEXT              RARC code (optional)
    --denial-date TEXT       YYYY-MM-DD [required]
    --amount FLOAT           Billed amount USD [required]
    --eob-path TEXT          Path to EOB PDF (optional)
```

### SOP Management

```
rcm-denial init
    --payer TEXT              Build only this payer (omit for all)
    --check-only             Report coverage without indexing
    --strict                 Fail on missing collections (CI/CD)
    --verify                 Run test queries after indexing

rcm-denial ingest-sop
    --payer TEXT              [required]
    --dir TEXT                [required]
    --verify                 Run verification after ingestion

rcm-denial sop-status        Show collection stats and manifest health
rcm-denial seed-kb           Seed global ChromaDB collection (legacy)
```

### Human Review Queue

```
rcm-denial review list
    --batch-id TEXT           Filter by batch
    --status TEXT             pending|approved|re_routed|re_processed|human_override|written_off|submitted
    --limit INTEGER           Max rows (default: 50)

rcm-denial review detail     --run-id TEXT [required]
rcm-denial review approve    --run-id TEXT [required] --reviewer TEXT
rcm-denial review bulk-approve
    --batch-id TEXT --confidence-above FLOAT --amount-below FLOAT --reviewer TEXT

rcm-denial review re-route
    --run-id TEXT             [required]
    --stage TEXT              intake_agent | targeted_ehr_agent | response_agent [required]
    --notes TEXT              Reviewer guidance injected into LLM prompt
    --reviewer TEXT           --execute (run re-entry immediately)

rcm-denial review override
    --run-id TEXT [required]  --text TEXT [required] --reviewer TEXT --execute

rcm-denial review execute-reentry  --run-id TEXT [required]

rcm-denial review write-off
    --run-id TEXT [required]
    --reason TEXT             timely_filing_expired|cost_exceeds_recovery|payer_non_negotiable|
                              duplicate_confirmed_paid|patient_responsibility|other [required]
    --notes TEXT --reviewer TEXT --force (bypass guard)

rcm-denial review stats      --batch-id TEXT
```

### Payer Submission

```
rcm-denial submit            --run-id TEXT [required] --dry-run
rcm-denial submit-batch      --batch-id TEXT [required] --dry-run
rcm-denial submission-status --run-id TEXT [required]
rcm-denial submission-log    --run-id TEXT [required]
rcm-denial submission-registry list
rcm-denial submission-registry register
    --payer-id TEXT [required] --method TEXT [required]
    --api-endpoint TEXT --portal-url TEXT --clearinghouse TEXT --notes TEXT
rcm-denial submission-stats  --batch-id TEXT
```

### Observability & Database

```
rcm-denial stats
    --batch-id TEXT --export-metrics --push-gateway TEXT

rcm-denial db info
rcm-denial db export-schema  --output TEXT
rcm-denial db migrate-to-postgres
```

### Evaluation

```
rcm-denial evals run
    --golden-cases TEXT      Path to golden_cases.json (default: data/evals/golden_cases.json)
    --output-dir TEXT        Check actual pipeline output against golden cases
    --json-out TEXT          Write report as JSON

rcm-denial evals check-output <CLAIM_ID>  --output-dir TEXT
rcm-denial evals quality-signals
```

### Web UI & Tests

```
rcm-denial web               --host TEXT --port INTEGER --reload
rcm-denial run-tests         -v/--verbose
```

---

## 18. Complete Configuration Reference

### OpenAI / LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | -- | Required for LLM features |
| `OPENAI_MODEL` | `gpt-4o` | LLM model |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model for SOP RAG |
| `OPENAI_MAX_TOKENS` | `4096` | Max output tokens per LLM call |
| `OPENAI_TEMPERATURE` | `0.1` | LLM temperature (0.0 = deterministic) |

### LangSmith Tracing

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGCHAIN_TRACING_V2` | `false` | Enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | -- | LangSmith API key |
| `LANGCHAIN_PROJECT` | `rcm-denial-management` | LangSmith project name |

### ChromaDB / SOP RAG

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROMA_PERSIST_DIR` | `./data/chroma_db` | ChromaDB storage path |
| `CHROMA_COLLECTION_NAME` | `sop_global` | Fallback collection name |
| `SOP_RAG_FRESHNESS_CHECK` | `true` | Re-index when SOP files change |
| `SOP_MIN_RELEVANCE_SCORE` | `0.3` | Minimum RAG similarity score |
| `SOP_PIPELINE_STRICT_MODE` | `false` | Warn on missing SOP collections |

### External Adapters

| Variable | Default | Description |
|----------|---------|-------------|
| `EMR_ADAPTER` | `mock` | mock / epic / cerner / athena / rpa_portal |
| `PMS_ADAPTER` | `mock` | mock / kareo / advancedmd |
| `PAYER_ADAPTER` | `mock` | mock / availity / change_healthcare |
| `SUBMISSION_ADAPTER` | `mock` | mock / availity_api / rpa_portal / edi_837 |

### Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_REQUESTS_PER_MINUTE` | `30` | Token bucket refill rate |
| `LLM_BURST_SIZE` | `5` | Max rapid-fire calls before throttle |

### Submission Retry

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBMISSION_MAX_RETRIES` | `3` | Retry attempts for transient failures |
| `SUBMISSION_RETRY_DELAY_SECONDS` | `5.0` | Initial backoff (exponential x2) |

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_TYPE` | `sqlite` | sqlite / postgresql |
| `DATABASE_URL` | -- | PostgreSQL URL |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `METRICS_EXPORT_AFTER_BATCH` | `true` | Auto-write .prom file after batch |
| `PROMETHEUS_PUSHGATEWAY_URL` | -- | Pushgateway URL (empty = disabled) |

### Web UI Auth

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_AUTH_ENABLED` | `false` | Enable login |
| `WEB_AUTH_SECRET` | `change-me-in-production` | Session encryption key |
| `WEB_AUTH_USERS` | `admin:admin` | Comma-separated user:password pairs |

### Checkpointing

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_CHECKPOINTING` | `true` | Save per-node state for crash recovery |

### OCR

| Variable | Default | Description |
|----------|---------|-------------|
| `TESSERACT_CMD` | `/usr/bin/tesseract` | Tesseract binary path |
| `OCR_DPI` | `300` | OCR resolution |
| `OCR_PYMUPDF_MIN_CHARS` | `50` | Min chars from PyMuPDF before Tesseract fallback |

### Batch Processing

| Variable | Default | Description |
|----------|---------|-------------|
| `SKIP_COMPLETED_CLAIMS` | `true` | Idempotency |
| `MAX_CONCURRENT_CLAIMS` | `1` | Concurrency (future) |
| `BATCH_MAX_RETRIES` | `3` | Pipeline retry attempts |
| `BATCH_RETRY_DELAY_SECONDS` | `2.0` | Pipeline retry delay |

### Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `OUTPUT_DIR` | `./output` | Submission package directory |
| `LOG_DIR` | `./logs` | Log files directory |
| `DATA_DIR` | `./data` | Data directory |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |

---

## 19. Test Suite

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=rcm_denial --cov-report=term-missing

# Run specific phase
pytest tests/test_review_queue.py tests/test_submission.py -v
```

| Test File | Count | What It Tests |
|-----------|-------|---------------|
| `test_agents.py` | -- | Phase 1 agent unit tests (rule-based fallback paths) |
| `test_tools.py` | -- | Phase 1 mock data tool tests |
| `test_batch_processor.py` | -- | Phase 1 batch integration tests |
| `test_review_queue.py` | 15 | Enqueue, approve, re_route, override, write-off guard, review stats |
| `test_submission.py` | 14 | Models, mock adapter, registry CRUD, retry logic |
| `test_cost_tracker.py` | 17 | Cost calculation, recording, per-claim/batch summaries |
| `test_sop_pipeline_mode.py` | 17 | Pipeline mode toggle, manifest CRUD, coverage checks |
| `test_criteria_checks.py` | 24 | Structural assertions, golden dataset regression |
| `test_rate_limiter.py` | 6 | Token bucket burst, throttle, sustained throughput |
