# Source audit and review

Consistency QA checks the records against their schema and classification policy. Source verification checks whether those records faithfully represent the PDF. Passing tests or matching tokens cannot establish reading order, heading roles or table relationships.

## Run the independent audit

Install Poppler so `pdftotext` and `pdftoppm` are on your PATH. The parser itself only needs the Python package dependencies.

```bash
iaea-guidance-parser audit inputs/Safety \
  --parsed outputs/Safety --out tmp/audit/Safety \
  --reviews reviews/safety.jsonl
```

Repeat for Security. Use the source folder represented by the manifest. The audit checks source hashes, per-document and combined records, counts, knowledge JSONL, Markdown content/order, and Markdown part hashes. It reports missing publications, invalid page ranges, duplicate record IDs, and leaked internal table markers.

| File | Purpose |
| --- | --- |
| `audit_summary.json` | Counts and the source/output/runtime fingerprints for this audit. |
| `audit_findings.jsonl` | Individual findings, stable evidence-derived identifiers and dispositions. |
| `audit_coverage.csv` | One row per physical PDF page, including method, status and evidence. |
| `audit_document_coverage.jsonl` | Separate metadata and boundary checks for each publication. |
| `source_text_cache/` | Independent Poppler text cached by PDF hash and checked before reuse. |

The page comparison searches records whose physical page ranges overlap the source page. Whole-document token multiplicities additionally expose duplication that overlapping windows can conceal. Case, Unicode presentation forms and ordinary line-end hyphenation are normalized; technical operators remain distinct. A printed numeral is excluded only when the parsed label and source margin geometry agree, with that decision recorded in coverage.

These differences are candidates, not an accuracy score. Hidden or overprinted objects can appear in extraction without appearing in the rendered page. Adding them merely to remove a warning can introduce an error.

## What counts as verification

Independent text now uses explicit displayed-CropBox clipping, and review
images render that same CropBox. Cache paths include the `displayed-cropbox-v1`
policy as well as source hash, page, rotation and resolution. Old evidence is
preserved; changing policy never silently reuses its text or images. Cached
rotated evidence rotates both representations.

Parser association warnings produce unresolved audit findings and require
review. Automatic parsing does not grant a visual pass. Integration staging
retains historical reviews only when their strict fingerprints still match;
provenance-only differences are reported separately without weakening those
fingerprints.

Every required case needs evidence. Review methods and outcomes are separate:

| Method | Required evidence |
| --- | --- |
| `visual` | A recorded comparison with rendered source pages and the applicable checklist. |
| `automated_structural` | Recomputed independent proof of heading wording, placement, ordering and hierarchy. |
| `equivalent_evidence` | A current direct visual pass and exact matching rendered-image, independent-text, parsed-structure, metadata and checklist fingerprints. |

`verified` means all applicable checks passed. `blocked` means a defect or ambiguity remains. `inspected` records a comparison without certifying every check. `pending` and `not_scheduled` are not passes. The legacy `visual_review=reviewed` column counts inspections, including blocked ones.

Heading automation applies only when structure is the page's sole risk. It requires every parsed heading and every source bookmark targeting that page to agree, exact heading geometry, independent text ordering, and matching paths on the page's other records. Missing, partial or conflicting outlines require inspection. Token equality alone never establishes a heading's role. No broad font-style or exclusion rule has been approved as an alternative.

Footnote and definition pages require visual relationship checks. The risk
screen also recognizes independent source note labels and `DEFINITIONS` or
`GLOSSARY` headings when the parser has absorbed or misclassified their text.
Matching every token does not prove a note's anchor or a term's definition.

Document-wide token findings use physical page `0`; they cannot be closed by a nearby page pass. Metadata and all section/appendix/annex boundaries must also be verified for every document. A token investigation does not certify those checks. If a document finding is too large for one assignment, collect bounded page investigations and have the coordinator reconcile every occurrence before recording a document decision.

## Prepare a small assignment

The helper runs from an installed development checkout. It does not launch agents or change the parser.

For the current corpus, reserve work in the single `outputs/Audit/work-queue.csv` before preparing a packet. The local `reviews/WORK.md` defines ownership and staged outcomes; see [Recorded source reviews](../reviews/README.md) for artifact locations. Assignment directories hold evidence; they are not independent work queues.

```bash
python tools/source_review.py prepare \
  --parsed outputs/Safety --audit tmp/audit/Safety \
  --work tmp/review-work --doc-id SSG-66 \
  --pages 44 --context 45 --category continuation
```

This creates `packet.json` for the worker and a machine-generated `binding.json`. Source text and images are cached by source hash, physical page, rotation and resolution. The packet contains the publication identity, relevant records, discrepancies, fixed checklist and exact result schema. Each overlapping record appears once. Context records remain available because a missing continuation link can itself be the defect.

A visual assignment has at most four source pages including context. Text assignments have at most twelve page items and 2,500 supplied words; document-token packets also have the 2,500-word limit. Larger cases must be split. Use `--rotation 90` when a landscape figure needs it, `--cache PATH` to share existing evidence, and `--document-check` to investigate document-wide excess tokens using bounded page context. The helper rejects document packets whose excess terms occur outside the supplied output context.

Use one work directory for a processing wave. It prevents competing assignments from owning the same page. Context pages have no review ownership. Packets are frozen once prepared. Corrections and calibration rechecks use a separate, explicitly documented wave; the coordinator must transfer ownership and retain the original evidence. The helper does not maintain a cross-directory assignment registry.

## Delegate comparisons, retain judgment

Use fresh `gpt-5.6-luna` workers at `max` reasoning, `fork_turns="none"`, with no more than three concurrent workers. Astra retains planning, ambiguity resolution, corrections and exclusion-rule approval. Supply only the packet path and this bounded instruction:

> Read only the assigned evidence and checklist. Display the source images and compare the specified source and parsed content. Close unambiguous matches; report discrepancies with exact page/record references. Escalate uncertainty without guessing. Write only the assignment's result.json. Do not edit parser code, canonical outputs or the shared ledger, browse, or delegate further. Limit each exception to 120 words and the summary to 150 words. Do not reproduce hashes or publication metadata.

Workers return pass identifiers and exceptions using the packet schema. A pass must cover every applicable check on its owned page. Source wording, symbols, units, headings, table cells and blanks, notes, captions, continuations and page references all matter. A claimed omission or duplicate needs the exact substring and record; a changed word must not be excused as normalization. Figure-image exports are outside scope.

For text-only investigations, Astra interprets the result separately. A worker's text comparison cannot be merged as automated structural proof.

## Sample and merge

```bash
python tools/source_review.py sample --work tmp/review-work
python tools/source_review.py merge tmp/review-work/assignments/ASSIGNMENT \
  --parsed outputs/Safety --ledger reviews/safety.jsonl
```

The sample selects a deterministic 2% of visual passes, rounded up with at least one per document. Astra inspects the selected pages. Record the outcome in `sample.json`, bound to the worker result's SHA-256, with `status`, `sampled_ids`, `reason` and `references`. A failed sample reopens every pass in that assignment. Reopen decisions depending on the same faulty rule; do not generalize merely from similar appearance.

Astra may write `adjudication.json`, also bound by `result_sha256`, with `decisions` keyed by owned item ID. Each decision needs `status` and source-backed `notes`; it may include individual finding `dispositions`. Allowed disposition statuses are `corrected`, `source_confirmed_false_positive`, `intentional_exclusion` and `unresolved`, each with a reason covering the entire finding. The decision key is the finding's `finding_id`, not its check name.

Merge checks the frozen packet, complete ownership, result limits, current source and publication metadata, meaningful parsed content, and every cached image/text artifact including context. It writes the shared ledger only after validation succeeds. `--parsed` explicitly checks the current canonical outputs when an assignment was prepared against a frozen snapshot. A stale assignment must be reassessed; changing its fingerprint to make it pass is not verification.

The ledger uses schema version 2. Content fingerprints omit volatile record sequence IDs but retain wording, roles, hierarchy, page references, geometry and associations. Publication metadata has a separate fingerprint. A matching legacy review is migrated as `inspected`, preserving its limitations; it is never silently upgraded to a complete pass. Legacy reviews without matching source and original full-record fingerprints are rejected.

The tool validates evidence bindings, not the truth of a reviewer's judgment. Matching hashes and a populated checklist cannot replace the source comparison. Exact-evidence reuse also requires a current directly inspected anchor; a changed or blocked anchor invalidates dependent reuse.

## Correct and recheck

Group only discrepancies with a demonstrated common cause. Reproduce each defect on a minimal example, fix the shared rule or add a narrow source-backed override, and reparse affected publications first. Keep worker evidence frozen. Meaningful record or metadata changes invalidate the corresponding reviews; record renumbering alone does not.

After fixes stabilize, regenerate both complete series and rerun the independent audit and export-integrity checks. Retain the before/after corpus comparison. Complete acceptance requires every required case closed, no unresolved substantive defects, matching source/output/export/review fingerprints, and passing tests, lint and formatting.

## Known limits

Captioned grids preserve cells when geometry is recoverable. Ruled or borderless tables may retain raw text without unambiguous column relationships. Some normalized lines lack exact source spans, leaving page ranges as their citation. Multi-page records can conceal local omissions. Rotated diagrams, image-only text, unknown glyphs and hidden PDF objects still require source inspection. Explicitly reviewed image transcriptions are hash-bound, carry their provenance and whole-page references, and display a source note in reading exports. They require visual verification; no OCR or language-model transcription is silently substituted.

The local `docs/validation.md` records current coverage and the pilot's rejected decisions; it is ignored by Git along with the [review artifacts](../reviews/README.md). The corpus must not be described as fully source-verified while required checks or substantive findings remain open.
