from __future__ import annotations

import json
from pathlib import Path

import pytest

from iaea_guidance_parser.security_audit import (
    DISALLOWED_CONTROL_RE,
    NOTE_THEN_MARKING_PROSE_RE,
    NEXT_SECTION_IN_TABLE_RE,
    TABLE_CONTAMINATION_RE,
    AuditConfig,
    label_from_rule,
    run_audit,
)


@pytest.fixture(scope="module")
def security_audit(tmp_path_factory):
    output_dir = tmp_path_factory.mktemp("security_audit")
    result = run_audit(AuditConfig(output_dir=output_dir))
    return result, output_dir


def test_security_audit_reports_matching_record_counts(security_audit):
    result, _ = security_audit
    summary = result.summary

    assert summary["raw_record_count"] == 16338
    assert summary["clean_record_count"] == summary["raw_record_count"]
    assert summary["qa_expected_record_count"] == summary["raw_record_count"]
    assert summary["manifest_record_count"] == summary["raw_record_count"]
    assert summary["qa_record_count_matches"] is True
    assert summary["manifest_record_count_matches"] is True


def test_clean_records_have_no_disallowed_control_characters(security_audit):
    result, _ = security_audit
    checked_fields = ["doc", "record", "status", "region", "pdf", "section", "text"]

    for record in result.records_clean:
        for field in checked_fields:
            assert not DISALLOWED_CONTROL_RE.search(getattr(record, field)), (
                record.stable_internal_id,
                field,
            )


def test_nss3_title_is_repaired_when_source_title_is_verified(security_audit):
    result, output_dir = security_audit
    title_corrections = [
        correction
        for correction in result.corrections
        if correction.doc == "NSS-3-M" and correction.rule_name == "metadata_title_from_source_pdf"
    ]

    assert title_corrections
    assert title_corrections[0].before_text == "Jotusvnfout Dpotfotvt"
    assert title_corrections[0].after_text == (
        "Monitoring For Radioactive Material In International Mail Transported By Public Postal Operators"
    )
    clean_part = (output_dir / "part_002_of_003.clean.md").read_text(encoding="utf-8")
    assert "Jotusvnfout Dpotfotvt" not in clean_part
    assert title_corrections[0].after_text in clean_part


def test_table_contamination_is_marked_layout_ambiguous(security_audit):
    result, _ = security_audit

    for record in result.records_clean:
        if record.record_type != "table":
            continue
        obvious_contamination = (
            TABLE_CONTAMINATION_RE.search(record.text)
            or NEXT_SECTION_IN_TABLE_RE.search(record.text)
            or NOTE_THEN_MARKING_PROSE_RE.search(record.text)
            or record.text.count("(cont.)") > 1
        )
        if obvious_contamination:
            assert record.layout_ambiguous, record.stable_internal_id

    nss6_table = next(
        record
        for record in result.records_clean
        if record.doc == "NSS-6-C" and record.record == "table TABLE 1"
    )
    assert nss6_table.layout_ambiguous is True
    assert "likely_ocr_symbol_error" in nss6_table.manual_review_labels
    assert "table_flattened_or_contaminated" in nss6_table.manual_review_labels


def test_heading_fragmentation_candidates_are_explicitly_justified(security_audit):
    result, _ = security_audit
    heading_candidates = [
        candidate for candidate in result.candidates if candidate.error_label == "heading_fragmentation"
    ]

    assert heading_candidates
    for candidate in heading_candidates:
        assert candidate.auto_fixed is False
        assert "not auto-merged" in candidate.suggested_action


def test_all_clean_records_have_unique_stable_internal_ids(security_audit):
    result, _ = security_audit
    stable_ids = [record.stable_internal_id for record in result.records_clean]

    assert all(stable_ids)
    assert len(stable_ids) == len(set(stable_ids))


def test_no_unreviewed_critical_status_findings(security_audit):
    result, _ = security_audit
    unreviewed_critical = [
        candidate
        for candidate in result.manual_review
        if candidate.severity == "critical" or candidate.error_label == "section_status_mismatch"
    ]

    assert unreviewed_critical == []


def test_every_automatic_candidate_has_a_correction_log(security_audit):
    result, output_dir = security_audit
    correction_keys = {
        (correction.doc, correction.record, correction.original_record_index, label_from_rule(correction.rule_name))
        for correction in result.corrections
    }
    auto_candidate_keys = {
        (candidate.doc, candidate.record, candidate.original_record_index, candidate.error_label)
        for candidate in result.candidates
        if candidate.auto_fixed
    }

    assert auto_candidate_keys <= correction_keys

    logged = [
        json.loads(line)
        for line in (output_dir / "corrections_applied.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(logged) == len(result.corrections)
    assert all({"before_text", "after_text", "rule_name", "provenance"} <= set(row) for row in logged)

