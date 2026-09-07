from types import SimpleNamespace

from iaea_guidance_parser.pdf_extract import (
    _decode_font_span,
    _printed_page_candidate,
    _PrintedPageCandidate,
    _RawPage,
    _remove_printed_page_line,
    _validated_printed_page_candidates,
)


class _FakePage:
    def __init__(self, blocks):
        self.rect = SimpleNamespace(height=1000)
        self._blocks = blocks

    def get_text(self, mode, sort=True):
        assert mode == "blocks"
        assert sort is True
        return self._blocks


def test_printed_page_candidate_requires_margin_geometry_and_plausible_value():
    page = _FakePage(
        [
            (20, 400, 80, 420, "2400"),
            (500, 950, 520, 970, "12"),
        ]
    )

    candidate = _printed_page_candidate(page, page_count=80, lines=["2400", "content", "12"])

    assert candidate == _PrintedPageCandidate(value="12", edge="bottom")


def test_printed_page_removal_targets_only_the_edge_occurrence():
    lines = ["12", "A table cell", "12"]

    _remove_printed_page_line(lines, _PrintedPageCandidate(value="12", edge="bottom"))

    assert lines == ["12", "A table cell"]


def test_printed_page_removal_drops_duplicate_footer_objects_at_the_same_edge():
    lines = ["A table cell", "77", "77", "77"]

    _remove_printed_page_line(lines, _PrintedPageCandidate(value="77", edge="bottom"))

    assert lines == ["A table cell"]


def test_printed_page_validation_rejects_an_isolated_margin_numeral():
    pages = [
        _RawPage(10, ["1"], _PrintedPageCandidate("1", "bottom")),
        _RawPage(11, ["2"], _PrintedPageCandidate("2", "bottom")),
        _RawPage(12, ["240"], _PrintedPageCandidate("240", "bottom")),
        _RawPage(13, ["4"], _PrintedPageCandidate("4", "bottom")),
    ]

    candidates = _validated_printed_page_candidates(pages)

    assert {page: candidate.value for page, candidate in candidates.items()} == {
        10: "1",
        11: "2",
        13: "4",
    }


def test_font_decoder_applies_verified_ascii_offset_and_preserves_spaces():
    # In the affected legacy fonts, these extracted code points display as text
    # exactly 31 ASCII positions later.
    assert _decode_font_span("5)& 803-%", {"ascii_offset": 31}) == "THE WORLD"
