"""Evidence-bound review decisions, separate from parser output and audit screening."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .provenance import fingerprint, sha256_file

METHODS = {"visual", "automated_structural", "equivalent_evidence"}
DISPOSITIONS = {
    "corrected",
    "source_confirmed_false_positive",
    "intentional_exclusion",
    "unresolved",
}


def content_fingerprint(records: list[dict[str, Any]]) -> str:
    """Ignore only sequence IDs; preserve order, content, locations and associations."""
    return fingerprint([{k: v for k, v in r.items() if k != "record_id"} for r in records])


def metadata_fingerprint(metadata: dict[str, Any]) -> str:
    """Bind publication claims independently of filenames and configuration provenance."""
    return fingerprint(
        {
            k: v
            for k, v in metadata.items()
            if k not in {"source_file", "source_sha256", "config_sha256", "metadata_source"}
        }
    )


def finding_id(finding: dict[str, Any]) -> str:
    return fingerprint({k: finding[k] for k in ("document_id", "pdf_page", "check", "evidence")})


def migrate_review(review, *, source_hash, records, metadata):
    """Migrate a matching legacy comparison without upgrading its coverage claims."""
    if (
        review.get("source_sha256") != source_hash
        or review.get("output_sha256") != fingerprint(records)
        or review.get("visual_review") != "reviewed"
        or not review.get("notes")
    ):
        raise ValueError("Legacy review does not match current source and records")
    result = deepcopy(review)
    result.update(
        schema_version=2,
        output_sha256=content_fingerprint(records),
        metadata_sha256=metadata_fingerprint(metadata),
        method="visual",
        verification_status="inspected",
        metadata_verified=False,
        evidence=[{"kind": "legacy_visual_comparison", "notes": review["notes"]}],
        migrated_from_sha256=fingerprint(review),
    )
    return result


def matching_review(review, *, source_hash, records, metadata):
    """Return a current review, or None. Matching hashes do not establish review quality."""
    if not review:
        return None
    if review.get("schema_version") != 2:
        try:
            return migrate_review(
                review, source_hash=source_hash, records=records, metadata=metadata
            )
        except ValueError:
            return None
    if (
        review.get("source_sha256") != source_hash
        or review.get("output_sha256") != content_fingerprint(records)
        or review.get("metadata_sha256") != metadata_fingerprint(metadata)
        or review.get("method") not in METHODS
        or review.get("verification_status") not in {"verified", "blocked", "inspected"}
        or not review.get("notes")
        or not review.get("evidence")
    ):
        return None
    return review


def apply_dispositions(findings, review):
    """Apply decisions to individual evidence-bound findings, including page zero."""
    if not review:
        return
    for finding in findings:
        decision = review.get("dispositions", {}).get(finding_id(finding), {})
        # Legacy decisions cover a whole check, not an individual token.
        if not decision and review.get("migrated_from_sha256"):
            decision = review.get("dispositions", {}).get(finding["check"], {})
        if decision.get("status") in DISPOSITIONS and decision.get("reason"):
            finding.update(disposition=decision["status"], review_reason=decision["reason"])


def validate_result(assignment, result):
    """Reject incomplete, oversized, contradictory or out-of-scope worker submissions."""
    if result.get("assignment_id") != assignment["assignment_id"]:
        raise ValueError("Wrong assignment ID")
    if len(result.get("summary", "").split()) > 150:
        raise ValueError("Coordinator summary exceeds 150 words")
    expected = {item["id"] for item in assignment["items"] if item["owned"]}
    passed = result.get("passes", [])
    exceptions = result.get("exceptions", [])
    ids = passed + [item["id"] for item in exceptions]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError("Result must decide each owned item exactly once")
    for item in exceptions:
        if item.get("status") not in {"defect", "uncertain"}:
            raise ValueError("Exceptions must report a defect or uncertainty")
        if not item.get("evidence") or len(item["evidence"].split()) > 120:
            raise ValueError("Exception needs evidence of at most 120 words")
        if not item.get("references"):
            raise ValueError("Exception needs exact page/record references")
    if passed and not result.get("checklist_completed"):
        raise ValueError("Passes require completion of the fixed checklist")
    return result


def equivalent_evidence(reference, candidate):
    """Require exact source rendering, independent text and parsed structure evidence.

    No fuzzy matching, font-name similarity, or token equality grants reuse.
    The caller must also validate the reference review against its current source.
    """
    keys = (
        "image_sha256",
        "text_sha256",
        "structure_sha256",
        "metadata_sha256",
        "checklist_sha256",
    )
    return all(reference.get(k) and reference[k] == candidate.get(k) for k in keys)


def validated_equivalent_review(review, reference, *, target_records, reference_records, metadata):
    """Accept an exact-evidence reuse only from a current, directly inspected pass.

    The caller first binds both reviews to their source and output. Cached image
    and text bytes, complete parsed structure and metadata must then agree. This
    intentionally rejects partial crops and merely similar pages.
    """
    if (
        not reference
        or reference.get("method") != "visual"
        or reference.get("verification_status") != "verified"
        or review.get("reference_review_sha256") != fingerprint(reference)
    ):
        return False

    def evidence(row, records):
        items = row.get("evidence", [])
        if len(items) != 1:
            return {}
        item = items[0]
        if (
            item.get("source_sha256") != row["source_sha256"]
            or item.get("pdf_page") != row["pdf_page"]
        ):
            return {}
        for kind in ("image", "text"):
            path = Path(item.get(kind + "_path", ""))
            if not path.is_file() or sha256_file(path) != item.get(kind + "_sha256"):
                return {}
        return {
            "image_sha256": item["image_sha256"],
            "text_sha256": item["text_sha256"],
            "structure_sha256": content_fingerprint(records),
            "metadata_sha256": metadata_fingerprint(metadata),
            "checklist_sha256": row.get("checklist_sha256"),
        }

    return equivalent_evidence(
        evidence(reference, reference_records), evidence(review, target_records)
    )
