import fitz
import pytest
import yaml

from iaea_guidance_parser.parser import IAEAGuidanceParser
from iaea_guidance_parser.provenance import fingerprint, sha256_file
from iaea_guidance_parser.series import (
    build_document_config,
    prune_stale_document_outputs,
    safe_path_component,
)


def test_safe_path_component():
    assert safe_path_component("NSS 17-T (Rev. 1)") == "NSS-17-T-Rev.-1"


def test_prune_stale_document_outputs_only_removes_generated_directories(tmp_path):
    documents = tmp_path / "documents"
    current = documents / "NSS-51-T"
    stale = documents / "NSS-16"
    unrelated = documents / "notes"
    for path in (current, stale):
        path.mkdir(parents=True)
        (path / "metadata.json").write_text("{}", encoding="utf-8")
        (path / "structural_index.jsonl").write_text("", encoding="utf-8")
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("manual material", encoding="utf-8")

    removed = prune_stale_document_outputs(tmp_path, [current])

    assert removed == [stale]
    assert current.is_dir()
    assert not stale.exists()
    assert unrelated.is_dir()


@pytest.fixture
def source_pdf(tmp_path):
    # Selection uses bytes alone, before any PDF text or metadata extraction.
    path = tmp_path / "publication.pdf"
    path.write_bytes(b"a specific source edition")
    return path


@pytest.fixture
def config_dir(tmp_path):
    path = tmp_path / "overrides"
    path.mkdir()
    return path


def write_override(path, config):
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_hash_override_survives_pdf_and_yaml_renames(source_pdf, config_dir):
    payload = {"document": {"title": "Reviewed title"}, "parser": {"include_text_blocks": False}}
    override = write_override(
        config_dir / "arbitrary-rule-name.yml",
        {"match": {"source_sha256": sha256_file(source_pdf)}, **payload},
    )
    # A hash match takes precedence over a selector-free legacy filename match.
    write_override(config_dir / "publication.yaml", {"document": {"title": "Legacy title"}})
    before = build_document_config(
        pdf_path=source_pdf, pdf_root=source_pdf.parent, config_dir=config_dir
    )
    relocated = source_pdf.parent / "different-folder"
    relocated.mkdir()
    renamed_pdf = source_pdf.rename(relocated / "download.PDF")
    override.rename(config_dir / "another-name.yaml")
    after = build_document_config(pdf_path=renamed_pdf, pdf_root=relocated, config_dir=config_dir)
    assert before == after == {**payload, "fallbacks": {}}
    assert "match" not in after


def test_same_filename_with_different_bytes_does_not_receive_hash_override(source_pdf, config_dir):
    write_override(
        config_dir / "publication.yaml",
        {"match": {"source_sha256": sha256_file(source_pdf)}, "document": {"title": "Old edition"}},
    )
    source_pdf.write_bytes(b"a revised source edition")
    config = build_document_config(
        pdf_path=source_pdf, pdf_root=source_pdf.parent, config_dir=config_dir
    )
    assert config["document"] == {}


def test_duplicate_hash_matches_are_rejected(source_pdf, config_dir):
    override = {"match": {"source_sha256": sha256_file(source_pdf)}, "parser": {}}
    for name in ("first", "second"):
        write_override(config_dir / f"{name}.yaml", override)
    with pytest.raises(ValueError, match=r"Multiple overrides.*first.yaml.*second.yaml"):
        build_document_config(
            pdf_path=source_pdf, pdf_root=source_pdf.parent, config_dir=config_dir
        )
    with pytest.raises(ValueError, match=r"Multiple overrides.*documents.first.*documents.second"):
        build_document_config(
            pdf_path=source_pdf,
            pdf_root=source_pdf.parent,
            series_config={"documents": {"first": override, "second": override}},
        )


@pytest.mark.parametrize(
    "selector",
    [
        None,
        {},
        "filename.pdf",
        {"source_sha256": "abc"},
        {"source_sha256": 123},
        {"source_sha256": "a" * 64, "filename": "publication.pdf"},
    ],
)
def test_invalid_source_selector_is_rejected(source_pdf, config_dir, selector):
    write_override(config_dir / "invalid.yaml", {"match": selector, "parser": {}})
    with pytest.raises(ValueError, match=r"invalid.yaml: match"):
        build_document_config(
            pdf_path=source_pdf, pdf_root=source_pdf.parent, config_dir=config_dir
        )


@pytest.mark.parametrize(
    "filename",
    ["publication.yaml", "publication.pdf.yaml", "publication.yml", "publication.pdf.yml"],
)
def test_legacy_filename_configs_keep_series_precedence(source_pdf, config_dir, filename):
    write_override(config_dir / filename, {"document": {"title": "File override"}})
    config = build_document_config(
        pdf_path=source_pdf,
        pdf_root=source_pdf.parent,
        config_dir=config_dir,
        series_config={
            "series": {"language": "en"},
            "document_defaults": {"title": "Default title"},
            "documents": {"publication.pdf": {"title": "Embedded title", "publisher": "IAEA"}},
        },
    )
    assert config["document"] == {"language": "en", "title": "File override", "publisher": "IAEA"}


@pytest.mark.parametrize("as_list", [False, True])
def test_embedded_hash_overrides_are_independent_of_names(source_pdf, config_dir, as_list):
    override = {
        "match": {"source_sha256": sha256_file(source_pdf)},
        "document": {"title": "Embedded title", "publisher": "IAEA"},
    }
    documents = [override] if as_list else {"a descriptive label": override}
    renamed_pdf = source_pdf.rename(source_pdf.with_name("renamed.pdf"))
    config = build_document_config(
        pdf_path=renamed_pdf,
        pdf_root=renamed_pdf.parent,
        series_config={"documents": documents},
    )
    assert config["document"] == override["document"]
    assert "match" not in config
    write_override(
        config_dir / "another-label.yaml",
        {"match": override["match"], "document": {"title": "File title"}},
    )
    config = build_document_config(
        pdf_path=renamed_pdf,
        pdf_root=renamed_pdf.parent,
        series_config={"documents": documents},
        config_dir=config_dir,
    )
    assert config["document"] == {"title": "File title", "publisher": "IAEA"}


@pytest.mark.parametrize("as_list", [False, True])
def test_embedded_hash_mismatch_cannot_fall_back_to_filename(source_pdf, as_list):
    override = {
        "match": {"source_sha256": "0" * 64},
        "filename": source_pdf.name,
        "document": {"title": "Wrong source"},
    }
    documents = [override] if as_list else {source_pdf.name: override}
    config = build_document_config(
        pdf_path=source_pdf, pdf_root=source_pdf.parent, series_config={"documents": documents}
    )
    assert config["document"] == {}


def test_direct_config_checks_source_before_extraction(source_pdf, config_dir):
    override = write_override(
        config_dir / "direct.yaml",
        {"match": {"source_sha256": "0" * 64}, "parser": {"font_decoders": []}},
    )
    with pytest.raises(ValueError, match="source_sha256 does not match this PDF"):
        IAEAGuidanceParser.from_pdf(source_pdf, override)


def test_direct_hash_config_preserves_rules_and_config_fingerprint(tmp_path, config_dir):
    pdf = tmp_path / "synthetic.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((70, 70), "2. EXAMPLE")
        page.insert_text((70, 100), "2.1. A synthetic paragraph.")
        document.save(pdf)
    rules = {
        "document": {"document_id": "DEMO", "title": "Reviewed title"},
        "parser": {"include_text_blocks": False},
    }
    override = write_override(
        config_dir / "direct.yaml",
        {"match": {"source_sha256": sha256_file(pdf).upper()}, **rules},
    )
    parser = IAEAGuidanceParser.from_pdf(pdf, override)
    metadata, records = parser.parse()
    assert metadata.source_sha256 == sha256_file(pdf)
    assert metadata.config_sha256 == fingerprint(rules)
    assert metadata.title == "Reviewed title"
    assert parser.include_text_blocks is False
    assert any(record.element_id == "2.1" for record in records)
