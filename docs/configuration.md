# Configuration

## Effective configuration precedence

Precedence, from lowest to highest:

1. series-level defaults under `series` and `parser`;
2. `document_defaults` and `fallbacks` in the series config;
3. per-document overrides under `documents` in the series config;
4. optional YAML file in `config_dir` named by PDF stem or file name.

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

Per-document controls are under `configs/document_overrides/`. Their filenames
deliberately match the PDF stems so the series command can load them through
`--config-dir`. Overrides may provide source-verified metadata or narrowly
scoped parser rules:

- `label_exceptions` accepts a reviewed unpunctuated paragraph, table or figure
  label only on specified PDF pages and, when supplied, in a specified region;
- `local_scope_patterns` attaches restarted schedule numbering to a stable
  parent such as a UN number without rewriting the printed paragraph ID;
- `outline_regions` preserves a reviewed form or specimen outline as one raw
  table rather than treating its internal labels as publication paragraphs;
- `font_decoders` applies a verified character mapping only to named fonts on
  named pages. Unknown or ambiguous glyph mappings remain unchanged and are
  reported by QA.

The shared grammar accepts the punctuation and dash variants found across both
series, including three-digit transport paragraphs, multilevel labels, appendix
and annex labels, requirement headings, and period- or colon-terminated table
and figure captions. Each recognized label records both its canonical ID and
its original source spelling in `extra`.
