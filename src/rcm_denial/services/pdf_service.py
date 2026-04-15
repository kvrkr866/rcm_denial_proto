##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: pdf_service.py
# Purpose: Generates individual PDF documents from workflow
#          outputs (analysis report, correction plan, appeal
#          letter) and merges them into a single submission
#          package PDF using reportlab and pypdf.
#
##########################################################

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Optional

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# PDF generation using reportlab
# ------------------------------------------------------------------ #

def _get_canvas_and_styles():
    """Lazy import reportlab to avoid hard failure if not installed."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )
    return colors, letter, getSampleStyleSheet, ParagraphStyle, inch, SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable


def generate_analysis_report_pdf(state, output_path: Path) -> Path:
    """Generates a PDF report of the denial analysis and enrichment summary."""
    try:
        colors, letter, getSampleStyleSheet, ParagraphStyle, inch, SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable = _get_canvas_and_styles()

        doc = SimpleDocTemplate(str(output_path), pagesize=letter,
                                rightMargin=inch, leftMargin=inch,
                                topMargin=inch, bottomMargin=inch)
        styles = getSampleStyleSheet()
        story = []

        title_style = ParagraphStyle('Title', parent=styles['Title'], fontSize=16, spaceAfter=12)
        h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=13, spaceBefore=14, spaceAfter=6)
        body_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, spaceAfter=6, leading=14)
        label_style = ParagraphStyle('Label', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold')

        claim = state.claim
        analysis = state.denial_analysis

        # Header
        story.append(Paragraph("DENIAL ANALYSIS REPORT", title_style))
        story.append(Paragraph(f"RCM Denial Management System — Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", body_style))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
        story.append(Spacer(1, 0.2 * inch))

        # Claim summary table
        story.append(Paragraph("Claim Summary", h2_style))
        claim_data = [
            ["Claim ID", claim.claim_id],
            ["Patient ID", claim.patient_id],
            ["Payer ID", claim.payer_id],
            ["Date of Service", str(claim.date_of_service)],
            ["Denial Date", str(claim.denial_date)],
            ["Billed Amount", f"${claim.billed_amount:,.2f}"],
            ["CPT Codes", ", ".join(claim.cpt_codes)],
            ["Diagnosis Codes", ", ".join(claim.diagnosis_codes)],
            ["CARC Code", claim.carc_code],
            ["RARC Code", claim.rarc_code or "N/A"],
            ["Denial Reason", claim.denial_reason],
        ]
        tbl = Table(claim_data, colWidths=[2 * inch, 4.5 * inch])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#F7F7F7')]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.2 * inch))

        # Analysis section
        if analysis:
            story.append(Paragraph("Denial Analysis", h2_style))
            story.append(Paragraph(f"<b>Recommended Action:</b> {analysis.recommended_action.upper()}", body_style))
            story.append(Paragraph(f"<b>Denial Category:</b> {analysis.denial_category.replace('_', ' ').title()}", body_style))
            story.append(Paragraph(f"<b>Confidence Score:</b> {analysis.confidence_score:.0%}", body_style))
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph("<b>Root Cause:</b>", label_style))
            story.append(Paragraph(analysis.root_cause, body_style))
            story.append(Paragraph("<b>CARC Interpretation:</b>", label_style))
            story.append(Paragraph(analysis.carc_interpretation, body_style))
            if analysis.rarc_interpretation and analysis.rarc_interpretation != "N/A":
                story.append(Paragraph("<b>RARC Interpretation:</b>", label_style))
                story.append(Paragraph(analysis.rarc_interpretation, body_style))
            story.append(Paragraph("<b>Reasoning:</b>", label_style))
            story.append(Paragraph(analysis.reasoning, body_style))

            if analysis.missing_items:
                story.append(Paragraph("<b>Missing Items:</b>", label_style))
                for item in analysis.missing_items:
                    story.append(Paragraph(f"• {item}", body_style))

            if analysis.incorrect_items:
                story.append(Paragraph("<b>Incorrect Items:</b>", label_style))
                for item in analysis.incorrect_items:
                    story.append(Paragraph(f"• {item}", body_style))

        # Errors
        if state.errors:
            story.append(Paragraph("Processing Notes", h2_style))
            for err in state.errors:
                story.append(Paragraph(f"• {err}", body_style))

        doc.build(story)
        logger.info("Analysis report PDF generated", path=str(output_path))
        return output_path

    except Exception as exc:
        logger.error("Failed to generate analysis report PDF", error=str(exc))
        raise


def generate_correction_plan_pdf(state, output_path: Path) -> Path:
    """Generates a PDF of the correction plan."""
    try:
        colors, letter, getSampleStyleSheet, ParagraphStyle, inch, SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable = _get_canvas_and_styles()

        doc = SimpleDocTemplate(str(output_path), pagesize=letter,
                                rightMargin=inch, leftMargin=inch,
                                topMargin=inch, bottomMargin=inch)
        styles = getSampleStyleSheet()
        story = []
        h1 = ParagraphStyle('H1', parent=styles['Title'], fontSize=16, spaceAfter=10)
        h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=12, spaceBefore=12, spaceAfter=6)
        body = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, leading=14, spaceAfter=5)
        bold = ParagraphStyle('Bold', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold')

        plan = state.correction_plan
        if not plan:
            story.append(Paragraph("CORRECTION PLAN", h1))
            story.append(Paragraph("No correction plan generated.", body))
            doc.build(story)
            return output_path

        story.append(Paragraph("CLAIM CORRECTION PLAN", h1))
        story.append(Paragraph(f"Claim ID: {plan.claim_id}  |  Plan Type: {plan.plan_type.upper()}  |  Generated: {datetime.utcnow().strftime('%Y-%m-%d')}", body))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
        story.append(Spacer(1, 0.15 * inch))

        # Code corrections
        if plan.code_corrections:
            story.append(Paragraph("Code Corrections Required", h2))
            tbl_data = [["Original Code", "Corrected Code", "Type", "Reason"]]
            for cc in plan.code_corrections:
                tbl_data.append([cc.original_code, cc.corrected_code, cc.code_type, cc.reason])
            tbl = Table(tbl_data, colWidths=[1.2*inch, 1.2*inch, 0.9*inch, 3.2*inch])
            tbl.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2C5F8A')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F4F8')]),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 0.1 * inch))

        # Documentation checklist
        if plan.documentation_required:
            story.append(Paragraph("Documentation Checklist", h2))
            for doc_req in plan.documentation_required:
                status = "✓ Available" if doc_req.is_available else "✗ Required"
                mandatory = " [MANDATORY]" if doc_req.is_mandatory else ""
                story.append(Paragraph(
                    f"<b>{doc_req.document_type.replace('_', ' ').title()}{mandatory}</b> — {status}",
                    bold
                ))
                story.append(Paragraph(doc_req.description, body))

        # Resubmission instructions
        if plan.resubmission_instructions:
            story.append(Paragraph("Resubmission Instructions", h2))
            for i, instr in enumerate(plan.resubmission_instructions, 1):
                story.append(Paragraph(f"{i}. {instr}", body))

        # Compliance notes
        if plan.compliance_notes:
            story.append(Paragraph("Compliance Notes", h2))
            for note in plan.compliance_notes:
                story.append(Paragraph(f"• {note}", body))

        # Payer notes
        if plan.payer_specific_notes:
            story.append(Paragraph("Payer-Specific Notes", h2))
            for note in plan.payer_specific_notes:
                story.append(Paragraph(f"• {note}", body))

        doc.build(story)
        logger.info("Correction plan PDF generated", path=str(output_path))
        return output_path

    except Exception as exc:
        logger.error("Failed to generate correction plan PDF", error=str(exc))
        raise


def generate_appeal_letter_pdf(state, output_path: Path) -> Path:
    """Generates a formal appeal letter PDF."""
    try:
        colors, letter_size, getSampleStyleSheet, ParagraphStyle, inch, SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable = _get_canvas_and_styles()

        doc = SimpleDocTemplate(str(output_path), pagesize=letter_size,
                                rightMargin=1.25*inch, leftMargin=1.25*inch,
                                topMargin=inch, bottomMargin=inch)
        styles = getSampleStyleSheet()
        story = []

        h1 = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=13, spaceAfter=8, alignment=1)
        body = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, leading=16, spaceAfter=10)
        bold = ParagraphStyle('Bold', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold', spaceAfter=4)

        pkg = state.appeal_package
        if not pkg or not pkg.appeal_letter:
            story.append(Paragraph("APPEAL LETTER", h1))
            story.append(Paragraph("No appeal letter generated.", body))
            doc.build(story)
            return output_path

        ltr = pkg.appeal_letter

        story.append(Paragraph(ltr.date_of_letter.strftime("%B %d, %Y"), body))
        story.append(Spacer(1, 0.1*inch))
        story.append(Paragraph(f"<b>To:</b> {ltr.recipient_name}", body))
        if ltr.recipient_address:
            story.append(Paragraph(ltr.recipient_address, body))
        story.append(Spacer(1, 0.1*inch))
        story.append(Paragraph(f"<b>From:</b> {ltr.sender_name}", body))
        if ltr.sender_npi:
            story.append(Paragraph(f"NPI: {ltr.sender_npi}", body))
        story.append(Spacer(1, 0.15*inch))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        story.append(Spacer(1, 0.1*inch))
        story.append(Paragraph(f"<b>RE: {ltr.subject_line}</b>", bold))
        story.append(Paragraph(f"Claim ID: {pkg.claim_id}  |  Patient ID: {pkg.patient_id}", body))
        if pkg.appeal_deadline:
            story.append(Paragraph(f"Appeal Deadline: {pkg.appeal_deadline.strftime('%B %d, %Y')}", body))
        story.append(Spacer(1, 0.15*inch))

        for para_text in [
            ltr.opening_paragraph,
            ltr.denial_summary,
            ltr.clinical_justification,
            ltr.regulatory_basis,
            ltr.closing_paragraph,
        ]:
            if para_text:
                story.append(Paragraph(para_text, body))

        story.append(Spacer(1, 0.3*inch))
        story.append(Paragraph(ltr.signature_block, body))

        # Supporting documents list
        if pkg.supporting_documents:
            story.append(Spacer(1, 0.3*inch))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
            story.append(Paragraph("Enclosures / Supporting Documents:", bold))
            for i, sdoc in enumerate(pkg.supporting_documents, 1):
                status = "Attached" if sdoc.is_attached else "To be obtained"
                story.append(Paragraph(f"{i}. {sdoc.document_name} — {status}", body))

        doc.build(story)
        logger.info("Appeal letter PDF generated", path=str(output_path))
        return output_path

    except Exception as exc:
        logger.error("Failed to generate appeal letter PDF", error=str(exc))
        raise


def generate_cover_letter_pdf(state, output_path: Path) -> Path:
    """
    Generates a cover letter PDF for the submission package.

    This is the FIRST page of the submission bundle. It clearly states:
    - Whether this is a RESUBMISSION or APPEAL
    - What the problem was (denial reason, CARC/RARC)
    - How it was addressed (per SOP guidelines)
    - What documents are enclosed
    """
    try:
        colors, letter_size, getSampleStyleSheet, ParagraphStyle, inch, SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable = _get_canvas_and_styles()

        doc = SimpleDocTemplate(str(output_path), pagesize=letter_size,
                                rightMargin=inch, leftMargin=inch,
                                topMargin=0.75*inch, bottomMargin=0.75*inch)
        styles = getSampleStyleSheet()
        story = []

        h1 = ParagraphStyle('H1', parent=styles['Title'], fontSize=16, spaceAfter=6, alignment=1)
        h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=12, spaceBefore=10, spaceAfter=4)
        body = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, leading=14, spaceAfter=5)
        bold = ParagraphStyle('Bold', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold', spaceAfter=3)
        small = ParagraphStyle('Small', parent=styles['Normal'], fontSize=9, textColor=colors.grey, spaceAfter=3)

        claim = state.claim
        analysis = state.denial_analysis
        routing = state.routing_decision or "unknown"
        evidence = state.evidence_check

        # Determine submission type
        if routing in ("resubmit", "both"):
            submission_type = "CORRECTED CLAIM RESUBMISSION"
            type_color = colors.HexColor("#1565C0")
        elif routing == "appeal":
            submission_type = "FORMAL APPEAL"
            type_color = colors.HexColor("#C62828")
        elif routing == "write_off":
            submission_type = "WRITE-OFF DOCUMENTATION"
            type_color = colors.grey
        else:
            submission_type = "DENIAL RESPONSE"
            type_color = colors.HexColor("#2E7D32")

        # Header with submission type
        story.append(Paragraph(submission_type, ParagraphStyle(
            'TypeHeader', parent=h1, textColor=type_color, fontSize=18,
        )))
        story.append(Paragraph("Cover Letter and Submission Summary", small))
        story.append(HRFlowable(width="100%", thickness=2, color=type_color))
        story.append(Spacer(1, 0.15*inch))

        # Claim reference table
        story.append(Paragraph("Claim Reference", h2))
        ref_data = [
            ["Claim ID",        claim.claim_id],
            ["Patient",         f"{claim.patient_name or claim.patient_id} (Member: {claim.member_id or claim.patient_id})"],
            ["Payer",           f"{claim.payer_name or claim.payer_id}"],
            ["Provider",        f"{claim.rendering_provider or claim.provider_id} (NPI: {claim.provider_npi or claim.provider_id})"],
            ["Date of Service", str(claim.date_of_service)],
            ["Denial Date",     str(claim.denial_date)],
            ["Billed Amount",   f"${claim.billed_amount:,.2f}"],
            ["CARC Code",       f"{claim.carc_code}"],
            ["RARC Code",       f"{claim.rarc_code or 'N/A'}"],
        ]
        tbl = Table(ref_data, colWidths=[1.8*inch, 4.7*inch])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#E3F2FD')),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#BBDEFB')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.15*inch))

        # Problem statement
        story.append(Paragraph("Problem Identified", h2))
        if analysis:
            story.append(Paragraph(f"<b>Denial Category:</b> {analysis.denial_category.replace('_', ' ').title()}", body))
            story.append(Paragraph(f"<b>Root Cause:</b> {analysis.root_cause}", body))
            story.append(Paragraph(f"<b>CARC Interpretation:</b> {analysis.carc_interpretation}", body))
            if claim.denial_reason:
                story.append(Paragraph(f"<b>Payer Stated Reason:</b> {claim.denial_reason}", body))
        else:
            story.append(Paragraph(f"Denial reason: {claim.denial_reason or 'Not specified'}", body))

        # Resolution per SOP
        story.append(Paragraph("Resolution Applied (per Payer SOP)", h2))
        story.append(Paragraph(f"<b>Recommended Action:</b> {routing.upper()}", bold))
        if analysis:
            story.append(Paragraph(f"<b>AI Confidence:</b> {analysis.confidence_score:.0%}", body))
            story.append(Paragraph(f"<b>Reasoning:</b> {analysis.reasoning}", body))

        if evidence:
            story.append(Spacer(1, 0.05*inch))
            story.append(Paragraph(f"<b>Evidence Sufficient:</b> {'Yes' if evidence.evidence_sufficient else 'No'}", body))
            if evidence.key_arguments:
                story.append(Paragraph("<b>Key Arguments:</b>", bold))
                for arg in evidence.key_arguments:
                    story.append(Paragraph(f"  - {arg}", body))
            if evidence.evidence_gaps:
                story.append(Paragraph("<b>Evidence Gaps:</b>", bold))
                for gap in evidence.evidence_gaps:
                    story.append(Paragraph(f"  - {gap}", body))

        # Correction details (if resubmission)
        if state.correction_plan and routing in ("resubmit", "both"):
            plan = state.correction_plan
            story.append(Paragraph("Corrections Applied", h2))
            if plan.code_corrections:
                for cc in plan.code_corrections:
                    story.append(Paragraph(
                        f"  {cc.code_type}: {cc.original_code} -> {cc.corrected_code} ({cc.reason})", body
                    ))
            if plan.resubmission_instructions:
                story.append(Paragraph("<b>Resubmission Steps:</b>", bold))
                for i, instr in enumerate(plan.resubmission_instructions, 1):
                    story.append(Paragraph(f"  {i}. {instr}", body))

        # Documents enclosed
        story.append(Paragraph("Documents Enclosed in This Package", h2))
        enc_items = ["This cover letter"]
        if analysis:
            enc_items.append("Denial analysis report")
        if state.correction_plan and routing in ("resubmit", "both"):
            enc_items.append("Correction plan with code changes")
        if state.appeal_package and routing in ("appeal", "both"):
            enc_items.append("Formal appeal letter with clinical justification")
        if state.appeal_package and state.appeal_package.supporting_documents:
            for sdoc in state.appeal_package.supporting_documents:
                enc_items.append(f"{sdoc.document_name} ({sdoc.document_type})")
        enc_items.append("Supporting medical records (as referenced)")

        for i, item in enumerate(enc_items, 1):
            story.append(Paragraph(f"  {i}. {item}", body))

        # Footer
        story.append(Spacer(1, 0.3*inch))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
        story.append(Paragraph(
            f"Generated by RCM Denial Management System on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}. "
            f"Batch: {state.batch_id or 'N/A'}  |  Run: {state.run_id}",
            small,
        ))

        doc.build(story)
        logger.info("Cover letter PDF generated", path=str(output_path))
        return output_path

    except Exception as exc:
        logger.error("Failed to generate cover letter PDF", error=str(exc))
        raise


def merge_pdfs(pdf_paths: list[Path], output_path: Path) -> Path:
    """Merges multiple PDFs into a single submission package."""
    try:
        from pypdf import PdfWriter

        writer = PdfWriter()
        for pdf_path in pdf_paths:
            if pdf_path.exists():
                writer.append(str(pdf_path))
            else:
                logger.warning("PDF not found for merge — skipping", path=str(pdf_path))

        with open(output_path, "wb") as f:
            writer.write(f)

        logger.info("PDFs merged", output=str(output_path), source_count=len(pdf_paths))
        return output_path

    except Exception as exc:
        logger.error("PDF merge failed", error=str(exc))
        raise
