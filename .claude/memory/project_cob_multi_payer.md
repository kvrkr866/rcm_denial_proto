---
name: Multi-Payer COB Feature — Requirements
description: Coordination of Benefits / multi-policy denial handling — patient may have multiple insurance policies; when primary denies, check if secondary covers the service category
type: project
---

# Multi-Payer COB Feature — Planned

**Why:** A patient may have 2+ active insurance policies. When primary payer denies a claim (eligibility, service exclusion, out-of-network, etc.), the system should check if the patient has another policy that covers this category of service — NOT just split the balance.

**Key insight from user:** It's NOT always about remaining balance. The primary scenarios are:
1. Primary denies for eligibility — check if secondary payer's plan covers this service category
2. Primary denies for service exclusion — check if other policy covers the procedure type
3. Primary denies for wrong payer order — identify correct primary and resubmit
4. Primary denies for out-of-network — secondary may have different network
5. Primary pays partially — remaining may be coverable by secondary (this IS the balance case, but only one of many)

**How to apply:** This feature should:
- Look up ALL active policies for the patient (PatientData.insurance_coverage already supports a list)
- For each denial, check if the denial reason makes the claim eligible for secondary payer routing
- Check the secondary payer's coverage criteria to see if this category of service is covered
- If yes, generate a new claim for the secondary payer with appropriate documentation (primary denial EOB attached)
- Route through the same pipeline (analyze → response → package → review → submit) but targeted at the secondary payer

**Current state:** Data models support multiple policies (list[InsuranceCoverage]), COB denial category is recognized (CARC 22/23/24), but no actual multi-payer routing logic exists. Pipeline processes one claim → one payer only.

## Policy Discovery Service — Retrieve All Active Policies

When a claim is denied, the system needs to discover ALL active insurance policies for the patient. This requires connecting to multiple external sources.

**Sources to query:**
1. Healthcare provider's PMS/EHR (patient's insurance cards on file)
2. Payer eligibility APIs (Availity 270/271, Change Healthcare real-time eligibility)
3. State HIE / CMS databases (Medicare/Medicaid cross-coverage)
4. Clearinghouse batch eligibility (bulk verification)

**Implementation approach:** New `policy_source_registry` table (same pattern as `medical_record_source_registry` and `payer_submission_registry`):

```
policy_source_registry
├── source_id          (e.g. "availity_eligibility", "epic_coverage", "cms_mbi_lookup")
├── source_type        (ehr | payer_api | clearinghouse | state_hie)
├── access_method      (rest_api | fhir_r4 | edi_270 | rpa_portal | manual)
├── endpoint_url       (API base URL)
├── credentials_ref    (key in secrets manager)
├── supported_payers   (JSON list of payer IDs this source can verify)
├── last_verified_at
├── is_active
```

**Flow:**
1. Claim denied → COB detection triggers
2. Query policy_source_registry for all active sources
3. For each source: call eligibility API with patient demographics
4. Aggregate all active policies found
5. Filter: which of these policies covers THIS category of service?
6. If match found → generate secondary claim with primary denial EOB attached
7. Route through pipeline targeted at the secondary payer

**Status:** Noted for future implementation. Not yet built.
