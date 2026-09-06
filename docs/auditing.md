# Source audit and review

Consistency QA asks whether the records agree with their own schema and classification policy. Source auditing asks what differs between those records and the PDF. Neither a passing test suite nor an empty warning list establishes that the source has been reproduced accurately.

## Run the checks

Install Poppler so `pdftotext` is on your PATH. The parser itself only needs the Python package dependencies.

```bash
iaea-guidance-parser audit inputs/Safety \
  --parsed outputs/Safety --out tmp/audit/Safety
```

Repeat for Security. Use the source folder represented by the manifest. The audit checks input hashes, per-document and combined records, counts, knowledge JSONL, Markdown content/order, and Markdown part hashes. It also reports source PDFs absent from the manifest, invalid page ranges, duplicate record IDs, and internal table markers that escaped parsing.

`audit_summary.json` binds the run to the manifest and canonical-record hashes and records the audit implementation and dependency versions. `audit_findings.jsonl` contains evidence for every candidate. `audit_coverage.csv` has one row per physical PDF page, with source/output fingerprints and separate text-check and visual-review states.

## Understand the comparison

Poppler supplies independent page text. Token comparison normalizes case, Unicode presentation forms, and ordinary end-of-line hyphenation. It retains numbers and technical operators such as `<` and `≤`. Printed numerals are excluded only when a parsed page label and source margin geometry agree; the exclusion is recorded.

The page check searches records whose physical page ranges overlap the source page. Those windows can overlap, so it also compares whole-document token multiplicities. Differences are candidates, not an accuracy percentage. Poppler can expose clipped, hidden, or overprinted objects that do not appear in the rendered PDF; do not add them to the output merely to eliminate a token difference. This comparison alone does not establish sentence meaning, reading order, cell relationships, or the correctness of a font decoder.

Pages with source captions, substantial drawings/images, parsed tables/figures, headings, metadata, glyph problems, or unexplained text differences enter the visual-review queue. Source-derived checks can expose content missing from the parser's own inventory. Small crop marks are not treated as substantive diagrams.

## Review the source

For each document, check title, edition/year, series/category, page offsets, and section/appendix/annex boundaries. Compare every queued page with its rendered source. In tables, check row/column headers, blank and merged cells, continued pages, symbols, units, and numbered labels. In prose, check omissions, duplication, interrupted paragraphs, footnote anchors, and requirements. Figure captions and their source locations must stay associated with the actual figure.

Fix shared extraction or parsing defects in code. Use a narrow document override for an exception supported by source evidence. Reparse affected documents, check their changes, and regenerate a complete series before accepting a final corpus.

A visual review can be supplied as JSONL with these fields copied from the coverage CSV:

```json
{"document_id":"EXAMPLE","pdf_page":1,"source_sha256":"...","output_sha256":"...","visual_review":"reviewed","notes":"Describe what was compared and any remaining limitation."}
```

Pass it using `--reviews path/to/reviews.jsonl`. The repository’s recorded comparisons are in [`reviews/`](../reviews/README.md). Reviews apply only while both fingerprints match. A populated note and matching hashes establish that a review was recorded; the tool cannot verify the quality of the reviewer’s judgment. Never populate the file automatically merely to clear the pending count.

A review can also attach a decision to a specific check on that page:

```json
{"document_id":"EXAMPLE","pdf_page":1,"source_sha256":"...","output_sha256":"...","visual_review":"reviewed","notes":"Compared the page image and parsed records.","dispositions":{"source_text_difference":{"status":"source_confirmed_false_positive","reason":"The independent extractor includes hidden objects absent from the rendered page."}}}
```

Allowed dispositions are `corrected`, `source_confirmed_false_positive`, `intentional_exclusion`, and `unresolved`. A decision applies to that check on that page, so use it only if the reason accounts for the entire finding. Document-wide duplication findings require separate investigation.

Findings remain evidence-bearing candidates even when a page is visually reviewed. Record whether each issue was corrected, is a source-confirmed false positive, reflects an intentional exclusion, or remains unresolved. Retain source evidence and the before/after output when correcting it.

## Current limitations

- Captioned grids with recoverable geometry receive cell structures. Tables bounded only by horizontal rules retain raw text and page bounds, with no inferred columns. Fully borderless tables and forms may remain raw text.
- Figures are not exported as images. Source text suppressed from prose is retained with figure-region evidence where associated with a caption.
- Some line-to-record source spans are unavailable after normalization; page ranges remain the primary citation. A span is emitted only for an exact normalized-line match.
- Multi-page record windows can conceal a local omission if the same words occur elsewhere in the window. Visual review and document-level multiplicity checks remain necessary.
- Unknown fonts, image-only text, and ambiguous reading order need source-image review. No OCR or language-model transcription is silently substituted.

The current completion status belongs in the audit report and validation record. Do not describe a corpus as fully source-verified while required visual checks or substantive findings remain unresolved.
