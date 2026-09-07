# Configuration

## Automatic recognition before adding an override

The PDF parser retains span typography internally and can identify inline
footnote markers, bold glossary openings and aligned wrapped headings. It can
infer a heading parent from a stronger size/style level within the same major
section. Explicit anchors, glossary openings, heading joins and parent maps
take precedence, including an explicitly empty parent list.

Automatic footnote resolution runs after records are assembled. A unique
raised marker must belong to a paragraph or definition and match a smaller
numbered note body below it. PDF notes without a unique owner remain unlinked
with an audit finding. Direct text-only `PageText` inputs retain legacy
heuristics; they do not establish source verification.

`PageText.source_typography`, glossary openings and caption ownership are
internal evidence. Canonical records retain existing fields; automatically
resolved notes add `extra.inline_anchor` with source page, line and bounding
box. Existing `extra.source_anchor` still identifies an explicit override.

Simple detected grids now share reviewed-cell validation and shading
extraction. Keep reviewed geometry for ambiguous cells or regions. Damaged
font mappings, exceptional reading rotations and image transcriptions remain
source-hash-bound. Before removing existing rules, compare affected publications
against their reviewed outputs and retain the evidence locally as described in
[Recorded source reviews](../reviews/README.md).

## Effective configuration precedence

Precedence, from lowest to highest:

1. series-level defaults under `series` and `parser`;
2. `document_defaults` and `fallbacks` in the series config;
3. per-document overrides under `documents` in the series config;
4. optional YAML file in `config_dir` matched by source SHA-256 (or legacy filename).

Document category/type are inferred from each PDF unless explicitly supplied
under `document_defaults`, per-document overrides or config-dir YAML. A
`fallbacks.document_category` is only used when the parser cannot infer the
category from the publication itself.

The normal series configs are:

```text
configs/nuclear_security_series.yaml
configs/nuclear_safety_series.yaml
```

They tell the parser whether a folder is Security or Safety. The parser normally infers individual document IDs, titles and categories from the PDFs.

## Match an override to its source

Per-document controls are under `configs/document_overrides/`. Each override
declares the **SHA-256 of its source PDF**, using the same `source_sha256` value
recorded in `metadata.json` and the series manifest. YAML filenames are descriptive;
renaming either the PDF or its YAML file does not affect matching through
`--config-dir`.

For example, the NSS-49-T override starts with:

```yaml
match:
  source_sha256: "a3f7c1e701661d74f09f7b8cfb9b4faa77282607ef396e42fedd88d04fd42876"
parser:
  lettered_list_continuation_pages: [137]
```

Copy the hash from existing source metadata, or calculate it in Python:

```python
from hashlib import sha256
from pathlib import Path

print(sha256(Path("path/to/publication.pdf").read_bytes()).hexdigest())
```

Matching happens before extraction, so a font decoder can be selected even when
the PDF's title text is damaged. A hash identifies the exact file bytes: a revised
or resaved PDF needs its rules checked before binding them to its new hash.
There is no title or series-number fallback for a hash-bound override.

Within each configuration layer, a hash match takes precedence over legacy
filename matching. Multiple matching hashes in the same layer are an error.
Selectors must contain only `source_sha256`, as a quoted 64-character hexadecimal
string. A nonmatching hash never falls back to that YAML file's name. Supplying
an override directly with `parse --config` also checks the hash and rejects a
different source before extraction.

The same `match` block works inside an entry under a series config's `documents`,
whether entries form a mapping or a list. A mapping key becomes a descriptive
label when its entry has a hash selector. Selector-free configurations retain the
older filename/stem/relative-path lookup; selector-free files in `--config-dir`
retain the PDF filename/stem convention. The `match` block is excluded from the
effective configuration fingerprint because it selects rules without changing them.

## Source-specific rules

Overrides may provide source-verified metadata or narrowly scoped parser rules:

- `label_exceptions` accepts a reviewed nonstandard paragraph, table or figure
  label only on specified PDF pages and, when supplied, in a specified region;
- `local_scope_patterns` attaches restarted schedule numbering to a stable
  parent such as a UN number without rewriting the printed paragraph ID. It can
  also keep exact, page-bound specimen headings within their own outline, as
  NSS-39-T does for a memorandum of understanding numbered 1–9. NSS-29-G
  uses separate scopes for its regulation articles and two MOU specimens;
- `outline_regions` preserves a reviewed form or specimen outline as one raw
  table rather than treating its internal labels as publication paragraphs;
- `lettered_list_continuation_pages` enables resuming a paragraph's lettered
  list across a table or figure on explicitly reviewed physical pages. The next
  label must follow consecutively from `(a)`; table notes need their own scope.
  NSS-49-T page 137 provides the source-backed example;
- `font_decoders` applies a verified character mapping only to named fonts on
  named pages. Unknown or ambiguous glyph mappings remain unchanged and are
  reported by QA;
- `figure_layouts` binds reviewed figure rectangles to a source SHA-256 and
  physical pages. Each page must include every figure's exact `caption` and
  `bbox`, including its labels and notes but excluding the caption. This keeps
  diagram text out of nearby prose. Optional `transcription` text requires a
  `reviewer` and source `notes`; it identifies the whole reviewed region rather
  than claiming letter geometry. NSS-24-G supplies an example: its image-only
  “RISK” label is transcribed, while other labels and notes remain native PDF
  text. Reading exports show the figure text and identify transcriptions;
- `reading_order_pages` orders text by its position on explicitly reviewed
  pages. Use it only where that order matches the source layout;
- `image_transcriptions` supplies reviewed text for image-only pages. It requires
  a matching `source_sha256` and a `pages` mapping whose entries contain `lines`,
  `reviewer` and source `notes`. Pages with extractable text are rejected.
  NSS-15 uses this for its two covers after independent transcription and visual
  comparison. Records retain explicit transcription provenance and whole-page
  references; reading exports display a source note. This is an opt-in correction,
  not an OCR step or an automated verification pass;
- `hyphenated_words` preserves the printed hyphen in exact reviewed compounds
  split across lines. NSS-13 uses `off-site` and `non-arrival`; ordinary word
  wraps still join normally. Longer compounds such as NSS-39-T’s
  `defence-in-depth` retain either internal hyphen when split across lines.
  This does not replace words in the source;
- `page_reading_rotations` maps physical pages to 90, 180 or 270 degrees when
  their text must be read sideways. It changes reading order while retaining
  the original PDF coordinates;
- `table_layouts` supplies reviewed cell rectangles and row/column spans for
  tables whose printed rules do not define every logical cell. The caption
  must match the source, the logical grid must be complete, and every cell's
  text is extracted from its PDF rectangle. Include every table on a configured
  page, because these layouts replace automatic table detection there. NSS-13
  uses this for its rotated categorization table and unruled uranium subrows.
  The complete `caption` must match consecutive source lines, including any
  `(cont.)` suffix. Text inside the reviewed table bounds but outside its cells
  is retained in `extra.table.notes` and after the grid in reading exports.
  NSS-37-G uses this for its wrapped caption and notes a–d on the continuation;
  `retain_cell_shading: true` also captures solid rectangular background fills
  as each cell's `background_color`. Use it after reviewing tables whose shading
  carries meaning, such as NSS-24-G's rating tables. Reading exports preserve
  the color and add `[shaded]` for readers that strip styling; this annotation
  is separate from the printed cell text. Transparent fills require review;
- `ignore_actual_text_pages` uses underlying PDF glyphs on explicitly reviewed
  pages whose `ActualText` replacement layer is wrong. NSS-30-G uses this for
  duplicate list markers. Compare the glyph text with the rendered source before
  opting in; replacement text can also contain useful accessibility information;
- `heading_continuations` recognizes an exact source heading on specified pages
  and joins its lines, retaining their original fragments. A single-line entry
  can establish a reviewed heading whose typography was not recognized;
- `subheading_parents` records reviewed intermediate headings within a named
  major section. For example, NSS-14 places `Detection` beneath its use/storage
  recommendations and `Security system`. The mapping is keyed first by the exact
  major heading, then by the subheading; each value lists its intermediate
  parents in source order. If an identical subheading has different parents
  within the section, its value can instead map each physical page to the
  reviewed parent list, as in NSS-13's transport recovery and mitigation headings;
- `page_regions` starts a source region at a physical page boundary after
  flushing the preceding content. A value may be a region name or a mapping
  with `region` and `section`. Do not add a boundary inside continued prose;
- `glossary_terms` maps each term to its exact first source line. Matching the
  full opening avoids mistaking a term mentioned in prose for a new definition;
- `footnote_pages` limits footnote detection to a reviewed list of physical
  pages. `footnote_anchors` links a specified note label to its source paragraph
  or term, with the visible anchor text retained as evidence. For unnumbered
  prose, a null `element_id` with exact anchor text and section context avoids
  inventing a paragraph label. An explicitly anchored publisher citation is
  retained even when its opening resembles a running footer. An optional
  `section_path` preserves the anchor's section when a later heading appears
  above the note on the same page. Reviewed labels also allow notes beginning
  with a URL or lowercase text. Use `source_region` when the note belongs to
  body text but appears below a References heading, as in NSS-36-G page 37.

The [NSS-20 override](../configs/document_overrides/Security/NSS-20.yaml)
shows these controls together: its definitions start on PDF page 21, while
footnote 2 on page 24 belongs to the `radioactive material` definition.
These are source-specific decisions, not defaults for other publications.

The [NSS-35-G override](../configs/document_overrides/Security/NSS-35-G.yaml)
uses exact labels such as `Action 8-5:` to keep 118 actions as individual
paragraphs. Their canonical IDs normalize the dash; `extra.source_label`
retains the printed label. Page-specific parents distinguish recurring
`Operator actions` headings across eight stages. Together these controls
preserve the stage, type of content and responsible party without adding a
new record type or a rule for unrelated publications.

SF-1 supplies another example: `Principle 1: Responsibility for safety` is a
heading, followed by its own statement and then paragraph 3.3. Its override
keeps that heading and statement out of paragraph 3.2. The same file identifies
the visible anchors for its citation and technical notes. Check the source
before applying either decision to another publication.

The shared grammar accepts the punctuation and dash variants found across both
series, including three-digit transport paragraphs, multilevel labels, appendix
and annex labels, requirement headings, and period- or colon-terminated table
and figure captions. Each recognized label records both its canonical ID and
its original source spelling in `extra`.

Annex and appendix bibliographies retain their enclosing section path. Labels
such as `[I–1]` remain reference IDs, and an exact heading such as
`REFERENCES TO ANNEX 1` starts references without discarding the annex context.
These are parser rules; the source review must still establish the heading’s
role and the links between labels and entries.
