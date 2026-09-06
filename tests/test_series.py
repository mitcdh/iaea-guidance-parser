from iaea_guidance_parser.series import (
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
