# Validation record

**Status:** repository restructuring, the runnable example, regeneration, and automated review of every source page are complete. Exhaustive visual source verification is **not complete**.

This records the repository review performed on 6 September 2026. The refactor and automated corpus checks are separate from source accuracy: a reproducible export can still contain an extraction error.

## Refactor baseline

The working repository, configurations, tests, and generated outputs were copied to `tmp/review-baseline/` before editing. Existing uncommitted changes were preserved. The original parser and the refactored parser were then run from separate frozen source snapshots over the same local corpus.

| Series | Documents | Physical pages | Original records |
| --- | ---: | ---: | ---: |
| Safety | 135 | 16,611 | 69,857 |
| Security | 47 | 4,248 | 17,054 |
| Total | 182 | 20,859 | 86,911 |

The behavior-only refactor produced identical canonical and knowledge JSONL, per-document Markdown, metadata, and preview CSV: 910 per-document artifacts and four combined JSONL files were compared. The 17 combined/part Markdown files also matched after normalizing run identifiers. Output-directory paths and run identifiers were the only permitted manifest differences. The local comparison evidence is `tmp/review-comparison.json`.

The changes make the line-consumption order explicit, centralize buffer operations and provenance, and consolidate the old Security-only correction pipeline into a read-only source audit. Tests are organized by behavior. The primary output filenames and record fields remain; source evidence and table layout fields are additions.

## Verification

The synthetic demonstration was generated, parsed, and visually inspected. A separate, freshly installed environment also ran the README commands successfully. It used Python 3.14.6 and PyMuPDF 1.28.2; this is a clean-install check, not a claim of testing every supported Python/dependency combination.

The final code passes **86 tests** in both the working and clean environments. Ruff lint/format checks and `git diff --check` pass. The tests include blank and merged cells, raw ruled tables, heading/table boundaries, adjacent drawings, footer detection, hyphenated diagram labels, export tampering, and review invalidation.

| Series | Final records | Pages text-checked | Visual pages reviewed | Visual pages pending | Integrity failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| Safety | 69,860 | 16,611 | 10 | 11,630 | 0 |
| Security | 17,030 | 4,248 | 3 | 3,412 | 0 |
| Total | 86,890 | 20,859 | 13 | 15,042 | 0 |

All 182 source hashes still match the original baseline. Effective configuration fingerprints and the recorded runtime match the final source/configuration snapshot. Canonical records agree with per-document outputs, knowledge JSONL, Markdown content/order, and part hashes. No internal table markers remain.

There are 26 tables with cell grids and 660 additional table fragments bounded by horizontal rules. The 26 grids retain their raw-text tokens in their captions and exported cells. The ruled fragments preserve raw text and page bounds without claiming inferred columns.

A separate Poppler screen of the first 12 pages of every publication found exact normalized source sequences for all 182 titles, publication years, series numbers, and STI identifiers, and for all 81 supplied PDF ISBNs. The other 101 PDF ISBN fields remain explicitly absent. This confirms textual evidence, not the semantic suitability of every metadata choice.

The source audit reports **3,163 findings**: 2,595 page-text differences, 139 document-level token differences, and 429 pages with unresolved glyphs. One finding is a recorded source-confirmed false positive; 3,162 remain unresolved screening findings. These are not counts of distinct confirmed errors or an accuracy score. Warning counts can also change when a long record is split into several records.

The final baseline comparison accounts for source labels and retained figure-region text. Token reductions are associated with removed margin numerals, corrected symbol/unit encoding, and identifiers rejoined after false footnote splitting. This comparison does not establish reading order or diagram relationships.

Local evidence lives under `outputs/Audit/`:

- `validation-summary.json`: final counts and fingerprints.
- `final-corpus-comparison.json`: per-document changes, source/configuration checks, and table export checks.
- `metadata-source-screen.json`: field-by-field independent metadata evidence.
- `Safety/` and `Security/`: findings, every-page coverage CSV, and audit summaries.

The source/configuration snapshot is `tmp/final-verified-source/`; the recoverable starting workspace is `tmp/review-baseline/`. These generated artifacts are excluded from Git. The recorded visual comparisons are versioned in [`reviews/`](../reviews/README.md).

## Source-backed corrections

| Source example (physical PDF page) | Finding and correction |
| --- | --- |
| NSS-9-G-REV1, page 100 | Numbered table cells were being interpreted as publication paragraphs. Captioned grids now keep cell positions, blank cells, merged spans, and raw text. A footer just outside the previous geometric cutoff is recognized with pagination evidence. |
| SSG-66, page 43 | The same short label legitimately repeats in separate appendix scopes. QA now compares labels within their actual scope. |
| GSG-2, pages 28, 53–54 | Source images establish Greek letters and comparison operators that a legacy Symbol font exposed as private-use glyphs. A page/font-scoped decoder supplies the verified characters. Additional configured pages use the same embedded font encoding. |
| GS-G-2-1, page 40 | The caption's words were separate PDF objects. Same-baseline caption fragments now form one table label. |
| NSS-22-G, page 79 | Separate drawing strokes form a figure's entry-log grid. Interior labels now remain figure evidence instead of becoming publication headings. Some title text outside the drawing bounds still appears as headings. |
| SSG-15-REV1, page 115; SSR-6-REV2, page 43 | Full-corpus text comparison caught an internal table marker being absorbed by a heading. Heading preparation now preserves table boundaries; the audit rejects leaked markers. Adjacent horizontal rule segments are also joined to capture both columns of the SSG-15 table. |
| SSG-20-REV1, page 6 | The copyright/cataloguing page spells its identifier `STI/PUB1981`. A narrow override preserves that spelling and fills the previously empty metadata field. |
| SSG-92, pages 130–131 | Diagram labels such as `LH1-4 Analytical` were mistaken for embedded footnotes. Footnote detection now excludes digits joined to a preceding hyphenated label. Rotated, multi-page figure relationships remain unresolved. |
| NSS-49-T, page 137 | Internal object order placed rows before their caption and attached them to the previous table. Horizontal rules now bound a raw table fragment without inventing columns. Text below the last rule remains separate. |

These are focused corrections, not certificates that every aspect of each listed page is correct. Ruled tables without a complete grid retain raw text and page geometry; their row/column relationships remain a visual-review task.

## Remaining source work

The independent comparison covers every physical page. It does not establish complete semantic accuracy. The pending table/figure, source-discrepancy, glyph, and metadata/structure pages still need recorded visual comparisons before the corpus can be described as fully source-verified. A reviewed page can also retain an unresolved finding; read its notes.

Known cases include ambiguous borderless table relationships, bibliography entries classified as headings, unrecognized equation glyphs, and hidden or overprinted source objects. On NSS-22-G page 79, Poppler extracts an additional worksheet and prose that are absent from the rendered page; copying those tokens into the output would create a new error. On NSS-49-T page 137, duplicate source text objects also require further review even though the table's scope has been corrected.

The audit ledger distinguishes completed text checks, recorded visual reviews, and pending pages. Matching hashes make reviews specific to the actual source and output. Read [Source audit and review](auditing.md) for reproducing the checks and resolving the remaining findings. Generated corpus files and detailed reports stay local because the source publications are not part of the repository.
