# Build status

What is implemented, how to run it, and what is *not* built. Read this before
the design docs — several claims in them are now supported by measurements, and
a few have been revised by them.

## Run it

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"

.venv/Scripts/python.exe -m pytest tests/ -q      # 65 tests
.venv/Scripts/python.exe scripts/run_demo.py      # both tracks, end to end
```

Windows paths shown; on POSIX use `.venv/bin/python`.

No API keys are required. Every experiment is deterministic given a seed, and
the targets are hand-written rather than model-generated. That is deliberate:
the instrument has to be trustworthy before it is pointed at anything
expensive, and a live model in the loop makes a green test suite mean very
little.

## What is built

| Module | Track | Purpose |
|---|---|---|
| `tasks.py` | A | Task contracts with reference-implementation oracles |
| `verifier.py` | A | Process-isolated compile-and-run, four distinct verdicts |
| `fms.py` | A | Boundary, adversarial-input, property FMs; split enforcement |
| `records.py` | A | Failure records and the exogenous acceptance gate |
| `targets.py` | A | Candidates with documented weaknesses, plus a correct control |
| `noise_floor.py` | B | Phase 1: text-comparison metrics vs noise and drift |
| `decision_metric.py` | B | Phase 2: decision-level comparison |

## Measured results

**Track A — the instrument discriminates.** On the flawed target, the training
FMs produce 25 verified failures matching documented weaknesses, with 0
unexplained. On the correct control target, 0 verified failures. A pipeline
that reports failures on correct code is not measuring anything, so the control
is the load-bearing result.

**Track B phase 1 — negative, and it was a real kill.** No text-comparison
metric separates functional change from sampling noise:

| Metric | False alarm | Power | d′ |
|---|---|---|---|
| `exact_hash` | 0.948 | 1.000 | +0.33 |
| `token_jaccard` | 0.212 | 0.000 | −0.97 |
| `edit_distance` | 0.052 | 0.000 | −1.46 |

**Track B phase 2 — the structural fix works.** Reducing responses to decisions
before comparing gives d′ ≈ +32 on every noise profile, and a threshold
calibrated on one profile transfers to the others (FA 0.015–0.022). The method
fails where the decision distribution is itself unstable, and that boundary is
measured rather than assumed.

## What is not built

- **No LLM target adapter.** Targets are hand-written. The real experiment
  needs an adapter for an actual code model, and the fine-tuning step on top
  of it.
- **No fine-tuning.** Nothing in this repo has trained a model. The held-out FM
  number the demo prints is a *baseline*, not a transfer result.
- **No real provider measurement.** Track B's numbers come from a synthetic
  stochastic target. The method transfers; the false-alarm rates do not.
- **No witness-set selection.** The minimal-subset clustering step is
  specified in the schema and README but not implemented.
- **No significance mapping.** Detecting change and assessing regulatory
  significance are separate steps; only the first is built.
- **Corpus is empty.** By design, and `split_manifest.json` is `frozen: false`.

## Platform notes

The verifier runs candidates in a subprocess with a wall-clock deadline
enforced by the parent. POSIX `resource` rlimits are applied where available
and skipped where not — **Windows has no `resource` module**, and an earlier
version failed every candidate at import while reporting the failures as target
defects. The parent-side deadline is the limit that is real on every platform.

Process isolation bounds accidental damage — infinite loops, memory
explosions. It is **not** a security boundary against a determined adversary.
Running genuinely untrusted candidates needs containers or VMs.

## Bugs found while building this

Each is now pinned by a test, because each one produced a *plausible* wrong
answer rather than an obvious failure.

- `resource` missing on Windows — every candidate looked like a target defect.
- A complexity test raced a timeout; flaky by construction. Replaced with a
  non-termination target and a runtime-ratio assertion.
- The first noise-floor run reported `power=0.0` for every metric: drift
  rewrote only tokens the profiles sometimes omitted, and
  `n_probes_per_pair=1` produced no pairs at all. Both now raise.
- `probes_for_confidence` had a sign error inverting its result, making a
  noisier system appear to need *fewer* probes. The test asserting the
  direction was also backwards — the function was right, the test was wrong.
- The decision extractor let the hedge word "otherwise" become the decision in
  `"alpha or maybe otherwise"`. That made an unstable system read as stable and
  the method reported a usable result on the profile built to defeat it. This
  was the most dangerous bug in the set: it produced a *positive* result where
  the honest answer was a failure.
