"""Exercise the worker boundary: frozen evidence, ownership and stale decisions."""

import importlib.util
import json
from pathlib import Path

import pytest
from test_audit import tiny_series as tiny_series

from iaea_guidance_parser.audit import run_audit

spec = importlib.util.spec_from_file_location(
    "source_review", Path(__file__).parents[1] / "tools/source_review.py"
)
work = importlib.util.module_from_spec(spec)
spec.loader.exec_module(work)


def test_assignments_are_frozen_owned_once_and_reject_changed_outputs(tiny_series, tmp_path):
    source, parsed, audit = tiny_series
    run_audit(source, parsed, audit)
    args = dict(
        parsed=parsed,
        audit=audit,
        work=tmp_path / "work",
        doc_id="DEMO",
        pages=[1],
        category="pilot",
    )
    folder = work.prepare(**args)
    with pytest.raises(ValueError, match="frozen"):
        work.prepare(**args)
    with pytest.raises(ValueError, match="already owned"):
        work.prepare(**dict(args, category="duplicate"))
    result = {
        "assignment_id": folder.name,
        "passes": ["p1"],
        "exceptions": [],
        "checklist_completed": True,
    }
    work.write_json(folder / "result.json", result)
    ledger = tmp_path / "reviews.jsonl"
    work.merge(folder, ledger)
    assert json.loads(ledger.read_text())["method"] == "visual"
    index = parsed / "documents" / "DEMO" / "structural_index.jsonl"
    records = [json.loads(s) for s in index.read_text().splitlines()]
    records[0]["record_id"] = "different sequence"
    index.write_text("".join(json.dumps(r) + "\n" for r in records))
    work.merge(folder, ledger)  # A renumbering is not a new source comparison.
    records[0]["text"] += " Altered source wording."
    index.write_text("".join(json.dumps(r) + "\n" for r in records))
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="Stale"):
        work.merge(folder, ledger)
    assert ledger.read_bytes() == before


def test_failed_sample_reopens_passes_and_tampered_images_are_rejected(tiny_series, tmp_path):
    from iaea_guidance_parser.provenance import sha256_file

    source, parsed, audit = tiny_series
    run_audit(source, parsed, audit)
    folder = work.prepare(
        parsed=parsed,
        audit=audit,
        work=tmp_path / "work",
        doc_id="DEMO",
        pages=[1],
        category="pilot",
    )
    work.write_json(
        folder / "result.json",
        {
            "assignment_id": folder.name,
            "passes": ["p1"],
            "exceptions": [],
            "checklist_completed": True,
        },
    )
    work.write_json(
        folder / "sample.json",
        {
            "result_sha256": sha256_file(folder / "result.json"),
            "status": "failed",
            "reason": "Sample missed a source distinction.",
            "references": ["p1"],
        },
    )
    ledger = tmp_path / "reviews.jsonl"
    work.merge(folder, ledger)
    assert json.loads(ledger.read_text())["verification_status"] == "blocked"
    image = Path(work.read_json(folder / "binding.json")["evidence"][0]["image_path"])
    image.write_bytes(b"not the reviewed image")
    with pytest.raises(ValueError, match="Cached evidence"):
        work.merge(folder, ledger)


def test_merge_checks_current_outputs_and_unowned_context(tiny_series, tmp_path):
    import shutil

    import fitz

    from iaea_guidance_parser.exporters import write_outputs, write_series_outputs
    from iaea_guidance_parser.series import parse_one_document

    source, parsed, audit = tiny_series
    pdf = source / "example.pdf"
    with fitz.open(pdf) as document:
        document.new_page().insert_text((72, 72), "2.2. Separate source item.")
        document.saveIncr()
    result = parse_one_document(
        pdf_path=pdf,
        pdf_root=source,
        out_root=parsed,
        series_config={"document_defaults": {"document_id": "DEMO"}},
    )
    write_outputs(result.output_dir, result.metadata, result.records)
    write_series_outputs(parsed, series_config={}, results=[result], failures=[])
    run_audit(source, parsed, audit)
    folder = work.prepare(
        parsed=parsed,
        audit=audit,
        work=tmp_path / "work",
        doc_id="DEMO",
        pages=[1],
        context=[2],
        category="continuation",
    )
    packet = work.read_json(folder / "packet.json")
    context_ids = packet["items"][1]["record_ids"]
    assert any(packet["records"][key]["page_start_pdf"] == 2 for key in context_ids)
    work.write_json(
        folder / "result.json",
        {
            "assignment_id": folder.name,
            "passes": ["p1"],
            "exceptions": [],
            "checklist_completed": True,
        },
    )
    current = tmp_path / "current"
    shutil.copytree(parsed, current)
    index = current / "documents" / "DEMO" / "structural_index.jsonl"
    records = [json.loads(s) for s in index.read_text().splitlines()]
    records[0]["text"] += " Substantive change."
    index.write_text("".join(json.dumps(r) + "\n" for r in records))
    ledger = tmp_path / "reviews.jsonl"
    with pytest.raises(ValueError, match="Stale parsed page"):
        work.merge(folder, ledger, parsed=current)
    # An unowned continuation is evidence too, even when the owned page is unchanged.
    records[0]["text"] = records[0]["text"].removesuffix(" Substantive change.")
    context_record = next(r for r in records if r["page_start_pdf"] == 2)
    context_record["text"] += " Changed continuation."
    index.write_text("".join(json.dumps(r) + "\n" for r in records))
    with pytest.raises(ValueError, match="Stale parsed page 2"):
        work.merge(folder, ledger, parsed=current)
    context = work.read_json(folder / "binding.json")["evidence"][1]
    Path(context["image_path"]).write_bytes(b"changed context")
    with pytest.raises(ValueError, match="Cached evidence"):
        work.merge(folder, ledger)
    assert not ledger.exists()


def test_document_findings_can_be_prepared_and_adjudicated_without_claiming_metadata(
    tiny_series, tmp_path
):
    from iaea_guidance_parser.provenance import sha256_file

    source, parsed, audit = tiny_series
    run_audit(source, parsed, audit)
    # A synthetic candidate exercises scope zero independently of page findings.
    finding = {
        "document_id": "DEMO",
        "pdf_page": 0,
        "check": "output_token_difference",
        "evidence": {"extra_tokens": {"source": 1}},
        "disposition": "unresolved",
    }
    with (audit / "audit_findings.jsonl").open("a") as stream:
        stream.write(json.dumps(finding) + "\n")
    folder = work.prepare(
        parsed=parsed,
        audit=audit,
        work=tmp_path / "work",
        doc_id="DEMO",
        pages=[1],
        category="document",
        document_check=True,
    )
    packet = work.read_json(folder / "packet.json")
    assert [i["pdf_page"] for i in packet["items"] if i["owned"]] == [0]
    work.write_json(
        folder / "result.json",
        {
            "assignment_id": folder.name,
            "passes": ["document"],
            "exceptions": [],
            "checklist_completed": True,
        },
    )
    work.write_json(
        folder / "adjudication.json",
        {
            "result_sha256": sha256_file(folder / "result.json"),
            "decisions": {
                "document": {
                    "status": "verified",
                    "notes": "Compared the synthetic discrepancy.",
                    "dispositions": {
                        work.finding_id(finding): {
                            "status": "source_confirmed_false_positive",
                            "reason": "The synthetic candidate is present in the source.",
                        }
                    },
                }
            },
        },
    )
    ledger = tmp_path / "reviews.jsonl"
    work.merge(folder, ledger)
    row = json.loads(ledger.read_text())
    assert row["pdf_page"] == 0
    assert row["verification_status"] == "inspected"
    assert row["metadata_verified"] is False
    assert work.samples(tmp_path / "work") == []  # Document cases are not page samples.
    adjudication = work.read_json(folder / "adjudication.json")
    approval = adjudication["decisions"]["document"]
    approval.update(metadata_verified=True, boundaries_verified=True, dispositions={})
    work.write_json(folder / "adjudication.json", adjudication)
    work.merge(folder, ledger)
    assert json.loads(ledger.read_text())["verification_status"] == "blocked"
    approval["dispositions"] = {"unassigned": {"status": "corrected", "reason": "Unsupported"}}
    work.write_json(folder / "adjudication.json", adjudication)
    with pytest.raises(ValueError, match="unassigned finding"):
        work.merge(folder, ledger)


def test_display_cache_keeps_old_evidence_and_rotates_text_with_image(tmp_path):
    import fitz

    from iaea_guidance_parser.provenance import sha256_file

    pdf = tmp_path / "spread.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_text((50, 100), "HIDDEN")
        page.insert_text((350, 100), "VISIBLE")
        page.set_cropbox(fitz.Rect(300, 0, 600, 400))
        doc.save(pdf)
    cache = tmp_path / "cache"
    old = cache / sha256_file(pdf) / "p0001-r90-dpi72"
    old.mkdir(parents=True)
    (old / "source.txt").write_text("Frozen original evidence")
    result = work.cache_page(pdf, cache, 1, rotation=90, dpi=72)
    assert result["extraction_policy"] == "displayed-cropbox-v1"
    assert (old / "source.txt").read_text() == "Frozen original evidence"
    extracted = Path(result["text_path"]).read_text()
    assert "VISIBLE" in extracted and "HIDDEN" not in extracted
    pixmap = fitz.Pixmap(result["image_path"])
    assert (pixmap.width, pixmap.height) == (400, 300)
    Path(result["text_path"]).write_text("Corrupted cache")
    assert (
        work.cache_page(pdf, cache, 1, rotation=90, dpi=72)["text_sha256"] == result["text_sha256"]
    )
