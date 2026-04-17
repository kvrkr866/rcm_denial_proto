# Architecture Diagram — PNG Specification for draw.io

Use this spec to create a polished PNG in draw.io (https://app.diagrams.net/).

## Color Coding

| Color | Hex | Meaning |
|-------|-----|---------|
| **Blue** (#1565C0 fill, #E3F2FD bg) | Demo-ready, implemented |
| **Orange dashed** (#FF9800 border, #FFF3E0 bg) | Production roadmap, not yet built |
| **Green** (#2E7D32) | Success/output |
| **Red** (#C62828) | Errors/write-offs |
| **Purple** (#6A1B9A) | LLM/AI components |
| **Grey** (#616161) | Internal/supporting |

## Layout (Left to Right, Top to Bottom)

### Row 1: Input Sources (top-left)
```
┌─────────────────────────────────┐
│         INPUT SOURCES           │
│  [CSV Upload] [Web UI]          │
│  [REST API] ← dashed/orange     │
└─────────────────────────────────┘
```

### Row 2: AI Pipeline (center, largest section)
```
┌──────────────────────────────────────────────────────────────────────────┐
│  AI PIPELINE (LangGraph StateGraph)                                      │
│                                                                          │
│  ┌──────────┐   ┌──────────────────────────────┐   ┌─────────────────┐  │
│  │ 1.Intake  │──►│ 2.Enrichment (5 tools ∥)     │──►│ 3.Analysis      │  │
│  │ Validate  │   │ Patient│Payer│EHR│OCR│RAG    │   │ LLM: GPT-4o    │  │
│  └──────────┘   └──────────────────────────────┘   │ Prompt→Pydantic │  │
│                                                      │ Fallback: CARC  │  │
│                        ┌─────────────────────────────┴─────────────────┘  │
│                        ▼                                                  │
│                 [Supervisor Router]                                       │
│            resubmit│appeal│both│write_off                                │
│                        │                                                  │
│  ┌────────────────────┐│┌───────────────────────┐ ┌───────────────────┐  │
│  │4.Evidence Check    │││5.Targeted EHR (if gaps)│ │6.Response Agent   │  │
│  │ LLM Call 1         │││ Labs, Imaging,         │ │ LLM Call 2        │  │
│  │ Prompt: evidence   │││ Pathology              │ │ Prompt: generate  │  │
│  │ → EvidenceCheck    ││└───────────────────────┘ │ → CorrectionPlan  │  │
│  │ 💰 Cost tracked    ││            │              │ → AppealLetter    │  │
│  └────────────────────┘│            ▼              │ 💰 Cost tracked   │  │
│                        └────────────►──────────────┤                   │  │
│                                                     └────────┬──────────┘  │
│  ┌─────────────────────────┐  ┌──────────────────────────────┘             │
│  │7.Document Packaging     │◄─┘                                            │
│  │ Cover Letter + Analysis │    ┌──────────────────┐                       │
│  │ + Correction + Appeal   │───►│8.Review Gate     │                       │
│  │ → SUBMISSION_PACKAGE.pdf│    │ AI Summary       │                       │
│  └─────────────────────────┘    │ Enqueue for HITL │                       │
│                                  └──────────────────┘                       │
└──────────────────────────────────────────────────────────────────────────┘
```

### Left Side: External Systems & Connection Registry
```
┌──────────────────────────────────────────┐
│  EXTERNAL SYSTEMS                        │
│                                          │
│  ┌─── EHR/EMR ───────────────────────┐   │
│  │ [Mock ✓] [Epic FHIR ┈] [Cerner ┈]│   │
│  │ [Athena ┈] [RPA Portal ┈]        │   │  ┈ = dashed/orange
│  └───────────────────────────────────┘   │
│                                          │
│  ┌─── PMS ───────────────────────────┐   │
│  │ [Mock ✓] [Kareo ┈] [AdvancedMD ┈]│   │
│  └───────────────────────────────────┘   │
│                                          │
│  ┌─── Payer Portals ─────────────────┐   │
│  │ [Mock ✓] [Availity ┈] [CHC ┈]    │   │
│  │ [EDI 270/271 ┈]                   │   │
│  └───────────────────────────────────┘   │
│                                          │
│  ┌─── Connection Registry ───────────┐   │
│  │ Per-provider: access_method,      │   │
│  │   endpoint_url, credentials_ref   │   │
│  │ Per-payer: submission_method,     │   │
│  │   portal_url, api_endpoint        │   │
│  │ [Policy Source Registry ┈]        │   │
│  └───────────────────────────────────┘   │
└──────────────────────────────────────────┘
```

### Right Side: Per-Payer RAG + LLM Config
```
┌──────────────────────────────────────────┐
│  PER-PAYER RAG KNOWLEDGE BASE            │
│                                          │
│  ChromaDB Vector Store                   │
│  OpenAI text-embedding-3-small           │
│                                          │
│  ┌──────────────┐ ┌──────────────┐       │
│  │sop_summit    │ │sop_national  │       │
│  │Summit.pdf    │ │NationalCare  │       │
│  └──────────────┘ └──────────────┘       │
│  ┌──────────────┐ ┌──────────────┐       │
│  │sop_crestview │ │sop_global    │       │
│  │Crestview.pdf │ │4 generic SOPs│       │
│  └──────────────┘ └──────────────┘       │
│                                          │
│  manifest.json (collection health)       │
│  Freshness check, Pipeline mode          │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│  LLM CONFIGURATION (purple)              │
│                                          │
│  Model: GPT-4o / GPT-4o-mini            │
│  Structured Output → Pydantic models     │
│  Temperature: 0.1                        │
│  Rate Limiter: 30 RPM / 5 burst          │
│  Cost Tracker: per-call USD logging      │
│  Fallback: Rule-based CARC mapping       │
└──────────────────────────────────────────┘
```

### Row 3: Human Review + Submission
```
┌──────────────────────────────────────────────────────────────────────────┐
│  HUMAN-IN-THE-LOOP REVIEW QUEUE                                          │
│                                                                          │
│  AI Summary: root cause, confidence, key arguments, flag reasons         │
│                                                                          │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐   │
│  │ APPROVE  │  │  RE-ROUTE    │  │   HUMAN      │  │   WRITE-OFF    │   │
│  │ → submit │  │  → re-enter  │  │   OVERRIDE   │  │   (guarded)    │   │
│  │ to payer │  │  pipeline +  │  │   → replace  │  │   must try     │   │
│  │          │  │  reviewer    │  │   AI output   │  │   re-route     │   │
│  │          │  │  notes in    │  │   with own    │  │   first        │   │
│  │          │  │  LLM prompt  │  │   text        │  │                │   │
│  └────┬─────┘  └──────┬───────┘  └──────┬───────┘  └───────┬────────┘   │
└───────┼────────────────┼────────────────┼───────────────────┼────────────┘
        │                │                │                   │
        ▼                ▼                ▼                   ▼
┌──────────────┐  ┌────────────┐  ┌────────────┐    ┌──────────────────┐
│ PAYER        │  │ Re-enter   │  │ Re-package │    │ Revenue Impact   │
│ SUBMISSION   │  │ Pipeline   │  │ with human │    │ Log (write-off   │
│              │  │ (back to   │  │ text (back │    │ tracking)        │
│ [Mock ✓]     │  │ review     │  │ to review  │    └──────────────────┘
│ [Availity ┈] │  │ queue)     │  │ queue)     │
│ [RPA ┈]      │  └────────────┘  └────────────┘
│ [EDI 837 ┈]  │
│              │
│ Retry: exp.  │
│ backoff      │
│ Log: every   │
│ attempt      │
└──────┬───────┘
       ▼
┌──────────────────────────────────────────┐
│  POST-SUBMISSION                         │
│                                          │
│  claim_disposition table                 │
│  → EHR Sync (update patient record)      │
│                                          │
│  [Multi-Payer COB ┈]                     │
│  [Policy Discovery ┈]                    │
└──────────────────────────────────────────┘
```

### Row 4: Memory/State + Output + Observability
```
┌─────────────────┐  ┌───────────────────────┐  ┌──────────────────────────┐
│ MEMORY & STATE  │  │ OUTPUT (per claim)     │  │ OBSERVABILITY            │
│                 │  │                         │  │                          │
│ Workflow State  │  │ package/                │  │ NiceGUI (Operational)    │
│ (Pydantic v2)  │  │  00_cover_letter.pdf    │  │  Stats: claims, CARC,    │
│                 │  │  01_analysis.pdf        │  │  reviews, write-offs,    │
│ Checkpointing  │  │  02_correction.pdf      │  │  EHR sync, recovery      │
│ (per-node)     │  │  03_appeal.pdf          │  │  Evals: golden dataset,  │
│                 │  │  SUBMISSION_PACKAGE.pdf │  │  quality signals         │
│ Audit Log      │  │                         │  │                          │
│ (per-node)     │  │ internal_audit/         │  │ Grafana (Technical)      │
│                 │  │  audit_log.json         │  │  Prometheus + Pushgateway│
│ [Patient       │  │  submission_metadata    │  │  21 panels: LLM cost,    │
│  Cache ┈]      │  │                         │  │  tool perf, routing,     │
│                 │  │                         │  │  confidence calibration  │
│ SQLite (dev)   │  │                         │  │  Loki: error logs,       │
│ PostgreSQL     │  │                         │  │  audit trail             │
│ (production)   │  │                         │  │                          │
└─────────────────┘  └───────────────────────┘  └──────────────────────────┘
```

## draw.io Tips

1. Use **Container** shapes for the major sections
2. Use **solid border + blue fill** for demo-ready components
3. Use **dashed border + orange fill** for production roadmap
4. Use **purple fill** for LLM/AI-specific components
5. Use **arrows** with labels for data flow direction
6. Group the connection registry as a **horizontal layer** between External Systems and the Pipeline
7. Export as PNG at 2x resolution for crisp display
