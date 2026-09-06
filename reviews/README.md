# Recorded source reviews

These JSONL files record visual comparisons made during the repository review. They contain publication/page identifiers, source and parsed-page fingerprints, notes, and any explicit disposition of an audit finding. The publications and rendered review images remain local.

`reviewed` means a comparison was recorded. Read its notes: a reviewed page can still have unresolved layout or extraction problems. These files do not assert that the whole corpus is accurate.

Reproduce the matching audit with:

```bash
iaea-guidance-parser audit inputs/Safety --parsed outputs/Safety \
  --out outputs/Audit/Safety --reviews reviews/safety.jsonl
iaea-guidance-parser audit inputs/Security --parsed outputs/Security \
  --out outputs/Audit/Security --reviews reviews/security.jsonl
```

A source or parsed-page change invalidates the corresponding review automatically. Perform the comparison again before updating either fingerprint. See [the review procedure](../docs/auditing.md) and [validation record](../docs/validation.md).
