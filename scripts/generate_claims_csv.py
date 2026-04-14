##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: generate_claims_csv.py
# Purpose: One-time utility to convert claim_db.json into
#          data/claims.csv. In production, the CSV will arrive
#          directly from an external system. This script exists
#          only to bootstrap the development dataset.
#
#          Usage: python scripts/generate_claims_csv.py
#
##########################################################

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

# ------------------------------------------------------------------ #
# Column order in output CSV
# Grouped logically: claim → patient → service → denial →
#                    financial → provider → payer → workflow/priority
# ------------------------------------------------------------------ #

CSV_COLUMNS = [
    # Claim identification
    "rec_id",
    "invoice_number",
    "status",

    # Patient
    "patient_name",
    "member_id",
    "patient_dob",

    # Service
    "service_date",
    "cpt_code",
    "cpt_description",
    "diagnosis_code",
    "specialty",
    "facility_type",

    # Denial
    "denial_date",
    "denial_code",
    "eob_pdf_path",

    # Financial
    "billed_amount",
    "contracted_rate",
    "paid_amount",

    # Provider
    "provider_npi",
    "rendering_provider",

    # Payer
    "payer",
    "payer_id",
    "payer_phone",
    "payer_response_time_days",
    "ivr_style",
    "primary_channel",
    "payer_filing_deadline_days",
    "payer_portal_url",

    # Workflow / priority
    "requires_auth",
    "days_in_ar",
    "prior_appeal_attempts",
    "appealable",
    "rebillable",
    "appeal_deadline",
    "days_to_deadline",
    "appeal_win_probability",
    "priority_score",
    "priority_label",
]


def generate_claims_csv(
    json_path: Path,
    csv_path: Path,
) -> int:
    """
    Reads claim_db.json and writes data/claims.csv.

    Args:
        json_path: Path to claim_db.json
        csv_path:  Output path for claims.csv

    Returns:
        Number of claims written.
    """
    if not json_path.exists():
        print(f"ERROR: {json_path} not found.")
        return 0

    with open(json_path, encoding="utf-8") as f:
        claim_db: dict = json.load(f)

    claims = list(claim_db.values())

    # Validate that all expected columns exist in at least one record
    all_keys: set[str] = set()
    for claim in claims:
        all_keys.update(claim.keys())

    missing_cols = [c for c in CSV_COLUMNS if c not in all_keys]
    if missing_cols:
        print(f"WARNING: These columns are in CSV_COLUMNS but not found in JSON: {missing_cols}")

    extra_keys = all_keys - set(CSV_COLUMNS)
    if extra_keys:
        print(f"INFO: These JSON fields are not included in CSV output: {sorted(extra_keys)}")

    # Write CSV
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CSV_COLUMNS,
            extrasaction="ignore",   # silently drop fields not in CSV_COLUMNS
        )
        writer.writeheader()
        for claim in claims:
            writer.writerow(claim)

    return len(claims)


if __name__ == "__main__":
    project_root = Path(__file__).parent.parent
    json_path = project_root / "claim_db.json"
    csv_path  = project_root / "data" / "claims.csv"

    print(f"Reading:  {json_path}")
    print(f"Writing:  {csv_path}")
    print()

    count = generate_claims_csv(json_path, csv_path)

    if count > 0:
        print(f"\n✓ Generated {count} claims → {csv_path}")
        print(f"  Columns : {len(CSV_COLUMNS)}")
        print()
        print("Sample rows preview:")
        with open(csv_path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i > 3:
                    break
                print(f"  {line.rstrip()}")
    else:
        print("✗ CSV generation failed.")
        sys.exit(1)
