from copy import deepcopy

import pytest

from iaea_guidance_parser.provenance import fingerprint
from iaea_guidance_parser.reviews import (
    apply_dispositions,
    content_fingerprint,
    equivalent_evidence,
    finding_id,
    matching_review,
    metadata_fingerprint,
    migrate_review,
    validate_result,
)


@pytest.fixture
def records():
    return [
        {
            "record_id": "sequence-1",
            "text": "≤ 10",
            "element_type": "paragraph",
            "section_path": ["1. INTRODUCTION"],
            "source_region": "Body",
            "text_status": "Normative",
            "page_start_pdf": 4,
            "page_end_pdf": 4,
            "parent_element_id": "2.1",
            "extra": {"cell": [1, 2]},
        }
    ]


def test_sequence_change_preserves_review_but_semantic_changes_invalidate_it(records):
    changed = deepcopy(records)
    changed[0]["record_id"] = "sequence-999"
    assert content_fingerprint(changed) == content_fingerprint(records)
    for key, value in {
        "text": "< 10",
        "element_type": "heading",
        "section_path": ["2. SCOPE"],
        "source_region": "Annex",
        "text_status": "Informative",
        "page_end_pdf": 5,
        "parent_element_id": "2.2",
        "extra": {"cell": [2, 1]},
    }.items():
        changed = deepcopy(records)
        changed[0][key] = value
        assert content_fingerprint(changed) != content_fingerprint(records), key
    assert content_fingerprint(records * 2) != content_fingerprint(records)


def test_metadata_changes_are_bound_separately():
    metadata = {
        "title": "Source",
        "publication_year": 2022,
        "source_file": "a.pdf",
        "config_sha256": "a",
    }
    assert metadata_fingerprint(metadata) == metadata_fingerprint(
        dict(metadata, source_file="b.pdf", config_sha256="b")
    )
    assert metadata_fingerprint(metadata) != metadata_fingerprint(
        dict(metadata, publication_year=2023)
    )


def test_only_matching_legacy_reviews_migrate_without_upgrading_claims(records):
    old = {
        "source_sha256": "source",
        "output_sha256": fingerprint(records),
        "visual_review": "reviewed",
        "notes": "Compared source; raw table remains unresolved.",
    }
    review = migrate_review(
        old, source_hash="source", records=records, metadata={"title": "Source"}
    )
    assert review["verification_status"] == "inspected"
    assert review["metadata_verified"] is False
    assert review["output_sha256"] == content_fingerprint(records)
    assert matching_review(
        review, source_hash="source", records=records, metadata={"title": "Source"}
    )
    assert (
        matching_review(
            review, source_hash="source", records=records, metadata={"title": "Changed"}
        )
        is None
    )
    with pytest.raises(ValueError, match="Legacy"):
        migrate_review(old, source_hash="changed", records=records, metadata={})
    changed = deepcopy(records)
    changed[0]["record_id"] = "later-sequence"
    with pytest.raises(ValueError, match="Legacy"):
        migrate_review(old, source_hash="source", records=changed, metadata={})


def test_document_wide_dispositions_bind_to_individual_evidence():
    finding = {
        "document_id": "D",
        "pdf_page": 0,
        "check": "output_token_difference",
        "evidence": {"extra_tokens": {"preand": 1}},
        "disposition": "unresolved",
    }
    review = {
        "dispositions": {
            finding_id(finding): {
                "status": "source_confirmed_false_positive",
                "reason": "Rendered source confirms the exact compound.",
            }
        }
    }
    apply_dispositions([finding], review)
    assert finding["disposition"] == "source_confirmed_false_positive"
    changed = dict(finding, evidence={"extra_tokens": {"preand": 2}}, disposition="unresolved")
    apply_dispositions([changed], review)
    assert changed["disposition"] == "unresolved"


def test_similar_evidence_never_authorizes_equivalent_review():
    evidence = {
        k: k
        for k in (
            "image_sha256",
            "text_sha256",
            "structure_sha256",
            "metadata_sha256",
            "checklist_sha256",
        )
    }
    assert equivalent_evidence(evidence, dict(evidence))
    for key in evidence:
        assert not equivalent_evidence(evidence, dict(evidence, **{key: "different"}))
        assert not equivalent_evidence(evidence, {k: v for k, v in evidence.items() if k != key})
    assert not equivalent_evidence({}, {})


def test_worker_result_must_cover_owned_pages_once_and_escalate_uncertainty():
    assignment = {
        "assignment_id": "A",
        "items": [{"id": "p1", "owned": True}, {"id": "p2", "owned": False}],
    }
    result = {"assignment_id": "A", "passes": ["p1"], "exceptions": [], "checklist_completed": True}
    assert validate_result(assignment, result)
    for changed in (
        dict(result, passes=[]),
        dict(result, passes=["p1", "p1"]),
        dict(result, passes=["p1", "p2"]),
        dict(result, checklist_completed=False),
    ):
        with pytest.raises(ValueError):
            validate_result(assignment, changed)
    with pytest.raises(ValueError, match="Exceptions"):
        validate_result(
            assignment,
            dict(result, passes=[], exceptions=[{"id": "p1", "status": "defect or uncertain"}]),
        )


def test_automated_proof_requires_source_role_geometry_and_hierarchy():
    import fitz

    from iaea_guidance_parser.outline import structural_proof

    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "2. EXAMPLE")
        page.insert_text((72, 110), "2.1. Source paragraph.")
        doc.set_toc([[1, "2. EXAMPLE", 1]])
        box = list(page.search_for("2. EXAMPLE")[0])
        records = [
            {
                "element_type": "heading",
                "page_start_pdf": 1,
                "page_end_pdf": 1,
                "text": "2. EXAMPLE",
                "section_path": ["2. EXAMPLE"],
                "source_region": "Body",
                "extra": {"source_spans": [{"pdf_page": 1, "bbox": box}]},
            },
            {"element_type": "paragraph", "section_path": ["2. EXAMPLE"], "source_region": "Body"},
        ]
        text = "2. EXAMPLE\n2.1. Source paragraph."
        assert structural_proof(doc, 1, records, text)
        changed = deepcopy(records)
        changed[0]["section_path"] = ["1. WRONG", "2. EXAMPLE"]
        assert structural_proof(doc, 1, changed, text) is None
        changed = deepcopy(records)
        changed[0]["extra"]["source_spans"][0]["bbox"] = [0, 0, 10, 10]
        assert structural_proof(doc, 1, changed, text) is None
        assert structural_proof(doc, 1, records, "Same words elsewhere") is None
        doc.set_toc([])
        assert structural_proof(doc, 1, records, text) is None


def test_equivalence_requires_a_current_visual_anchor_and_unchanged_artifacts(tmp_path):
    from iaea_guidance_parser.provenance import sha256_file
    from iaea_guidance_parser.reviews import validated_equivalent_review

    image, text = tmp_path / "source.png", tmp_path / "source.txt"
    image.write_bytes(b"identical blank-page rendering")
    text.write_text("")
    evidence = {
        "source_sha256": "source",
        "pdf_page": 1,
        "image_path": str(image),
        "image_sha256": sha256_file(image),
        "text_path": str(text),
        "text_sha256": sha256_file(text),
    }
    reference = {
        "method": "visual",
        "verification_status": "verified",
        "source_sha256": "source",
        "pdf_page": 1,
        "checklist_sha256": "fixed",
        "evidence": [evidence],
    }
    candidate = dict(
        reference,
        method="equivalent_evidence",
        pdf_page=2,
        reference_review_sha256=fingerprint(reference),
        evidence=[dict(evidence, pdf_page=2)],
    )
    args = dict(target_records=[], reference_records=[], metadata={})
    assert validated_equivalent_review(candidate, reference, **args)
    assert not validated_equivalent_review(
        candidate, dict(reference, verification_status="blocked"), **args
    )
    image.write_bytes(b"changed")
    assert not validated_equivalent_review(candidate, reference, **args)
