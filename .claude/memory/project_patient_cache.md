---
name: Patient Data Caching Across Claims
description: When multiple claims belong to the same patient in a batch, cache and reuse patient/EHR/payer data instead of re-fetching from external systems
type: project
---

# Patient Data Caching — Planned Feature

**Why:** In a real batch, multiple denied claims often belong to the same patient (e.g., patient has 3 denied claims from the same hospital visit). Currently every claim fetches patient data, EHR records, and payer policy from scratch — wasted API calls, latency, and potential cost.

**User requirement:** If new claim belongs to same patient as previous claim, retain patient details in memory. If different patient, clear cache and fetch fresh.

**Current state:** No caching on patient/EHR/payer data. SOP RAG has per-payer collection caching but that's a different layer.

**Design:**
- Cache key: `(patient_id, provider_id)` for EHR, `patient_id` for patient data, `payer_id` for payer policy
- Cache scope: within one batch run (not persisted across batches)
- Cache invalidation: when patient_id changes, clear patient + EHR cache; when payer_id changes, clear payer cache
- Implementation: module-level dict in enrichment_agent or a shared BatchCache service

**How to apply:** Add a `BatchCache` class that the enrichment agent checks before calling external tools. Batch processor creates the cache at start and passes it through the pipeline state or as a module-level singleton.

**Status:** Noted for future implementation. Not yet built.
