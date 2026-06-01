# Golden Set

Real-world slop and legit controls. The benchmark that proves the firewall works
without relying on vibes.

## Layout

```
golden/
  slop/    cases that SHOULD be flagged (label == "slop")
  legit/   cases that should NOT be flagged (any label != "slop")
```

The parent directory name is the ground-truth bucket. Filenames are `NN_short_desc.json`.

## Case format

```json
{
  "config_yaml": null,
  "event": { ...a NormalizedEvent (see core/schemas.py)... }
}
```

`event.repo.clone_path` is overwritten by the runner with the shared fixture clone,
so any file your case references in the diff must exist under `tests/fixtures/clone/`.

## Running

```bash
python eval_golden.py              # full report + results.json, fails if FPR > 0
python eval_golden.py --max-fpr 0.05
```

Needs the default provider's API key (the `api_key_env` named in `config/models.toml`, e.g.
`DEEPSEEK_API_KEY`, loaded from `brain/.env`): the golden set hits the real model.

## Metric

Positive class = slop (the thing the firewall catches).

- **TPR** = slop caught / all slop. Recall.
- **FPR** = legit flagged as slop / all legit. The one that matters most: every
  false positive is a maintainer told their good PR is garbage. Target 0%.

`results.json` is the machine-readable artifact (per-case label, confidence, timing)
and the data feed for the landing-page agent trace.

## Scaling to the headline

Six synthetic cases prove the harness. The "0% FPR across N real PRs" claim needs
real open-source examples: hallucinated symbols, cosmetic churn, mismatched
descriptions, mixed with legit controls. Add JSON files to the right bucket and rerun.
