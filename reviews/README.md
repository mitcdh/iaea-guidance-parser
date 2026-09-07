# Recorded source reviews

Local JSONL files contain source-bound comparisons and finding dispositions. Review ledgers, work queues and reports in this directory are ignored by Git; this guide remains versioned. Publication PDFs, rendered images and frozen worker packets also remain local. The files do not certify the complete corpus.

When present, local `WORK.md` identifies the single open-case queue, ownership rules and active correction batch. Run-specific reports, including `parser-integration-results.md`, are kept alongside it.

Schema version 2 separates the verification **method** (`visual`, `automated_structural`, `equivalent_evidence`) from its **status** (`verified`, `blocked`, `inspected`). A visual inspection may still expose a defect. Document-wide findings use physical page `0`; a document token comparison does not certify its metadata or boundaries.

Content fingerprints ignore generated record sequence IDs, while retaining text, classification, hierarchy, pagination, geometry and associations. Metadata has its own fingerprint. The 13 original comparisons were migrated only after their source and original record fingerprints matched, retaining their limited `inspected` status. Regenerated content invalidates affected reviews automatically.

Use the bounded preparation and merge helper described in [Source audit and review](../docs/auditing.md). Workers write only assignment results; Astra samples passes and records adjudications before merging. A failed sample reopens the assignment, and stale or altered evidence is rejected. Do not copy a new fingerprint into an old review to make it current.

Reproduce the audit with:

```bash
iaea-guidance-parser audit inputs/Safety --parsed outputs/Safety \
  --out outputs/Audit/Safety --reviews reviews/safety.jsonl
iaea-guidance-parser audit inputs/Security --parsed outputs/Security \
  --out outputs/Audit/Security --reviews reviews/security.jsonl
```

Independent heading proofs are recomputed by the audit. Similar-looking pages cannot inherit a pass: equivalent evidence requires exact proof and a current direct visual anchor. No corpus pages currently use equivalence reuse.

The local validation record at `docs/validation.md` (relative to the repository root) contains coverage, rejected pilot decisions and substantive work still open. It is also ignored by Git. These run artifacts are available only in the workspace where the reviews were performed or explicitly copied; a fresh checkout does not include them.
