# Results log

Every measurement this project has actually produced, with the exact command
that produced it. Nothing here is projected or estimated.

**Read this before any other document.** The claims in `research/README.md`
and `application/README.md` are designs. This file is what happened.

---

## How to reproduce

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
set -a && . "$LOCALAPPDATA/hermes/.env" && set +a   # OPENROUTER_API_KEY

.venv/Scripts/python.exe -m pytest tests/ -q                          # 85 tests
.venv/Scripts/python.exe scripts/run_demo.py                          # offline, both tracks
.venv/Scripts/python.exe scripts/run_live_probe.py   --model <id>     # live discovery
.venv/Scripts/python.exe scripts/run_live_control.py --model <id>     # live control
```

The offline demo and the test suite need no API key and no network. Only the
two `live_*` scripts do.

---

## Task register

Everything that was run, in order, with its outcome. This is the audit trail.

| # | Task | Command | Outcome |
|---|---|---|---|
| 1 | Literature check on the FM loop | web search | Loop is prior art in ≥5 places. Contribution narrowed to the exogenous-verifier domain. |
| 2 | Literature check on Witness | web search | Phase-1 text metrics documented as failing. Product premise viable. |
| 3 | Repo restructure | — | Two tracks, one thesis. `docs/thesis.md` created. |
| 4 | Offline verifier harness | `pytest` | Built and validated on hand-written targets with known weaknesses. |
| 5 | Phase-1 noise floor | `run_demo.py` | **Negative result.** No text-comparison metric usable. Kill criterion fired. |
| 6 | Phase-2 decision metric | `run_demo.py` | **Positive.** d′ −1.5..+0.3 → ≈ +32. Threshold transfers across profiles. |
| 7 | LLM adapter bring-up | `qwen/qwen3-coder` | HTTP 402 without explicit `max_tokens`. Fixed. |
| 8 | First live probe, easy tasks | `run_live_probe.py --per-family 5` | 0 failures. Ambiguous — added harder tasks. |
| 9 | Hard task families | `pytest tests/test_hard_tasks.py` | 3 multi-argument families added; arity bugs found and fixed. |
| 10 | Live probe, hard tasks | `--per-family 7` | 0 failures on qwen3-coder. Still ambiguous. |
| 11 | Live control | `run_live_control.py` | Mutants caught 14/14, originals 0/14 → model is genuinely correct. |
| 12 | Weaker models | `--per-family 6` | Corpus fills on llama-3.2-1b. |
| 13 | Reasoning-model failure | `qwen/qwen3.5-9b` | 3 fabricated `SyntaxError`s from an empty response. **Harness bug, not a finding.** |
| 14 | Yield accounting fix | `pytest tests/test_gate_and_noise.py` | 24 records were 2 bugs. Dedup key corrected. |
| 15 | Multi-model sweep | 3 models × 15 probes | 40 raw hits → **4 distinct failures**. |
| 16 | Control sweep | 4 models × 10 probes | Mutant detection confirmed on all 4. |

---

## Track A — live discovery

Three models, three multi-argument task families, two training FMs,
5 probes per FM per family. 15 probes per model.

```bash
.venv/Scripts/python.exe scripts/run_live_probe.py \
  --model <id> --per-family 5 --out research/data/corpus/<name>.json
```

| Model | Probe hits | **Distinct failures** | Inflation |
|---|---|---|---|
| `meta-llama/llama-3.2-1b-instruct` | 20 | 2 | 10× |
| `qwen/qwen3-coder-30b-a3b-instruct` | 20 | 2 | 10× |
| `qwen/qwen3-30b-a3b-instruct-2507` | 0 | 0 | — |
| **Total** | **40** | **4** | **10×** |

### The four distinct failures

| Cluster | Category | Verdict evidence |
|---|---|---|
| `clamp-list\|TypeError\|input_shape_assumption` | input shape | contract supplies 3 args, `solution()` takes 1 |
| `chunk-sum\|TypeError\|input_shape_assumption` | input shape | contract supplies 2 args, `solution()` takes 1 |
| `rotate-list\|TypeError\|input_shape_assumption` | input shape | contract supplies 2 args, `solution()` takes 1 |
| `chunk-sum\|NameError\|algorithmic_logic` | algorithmic | candidate does not define `solution` |

### What this actually shows

**The dominant failure mode is reproducible across independent model
families.** Models define `solution()` with one parameter for a contract that
supplies two or three. `meta-llama/llama-3.2-1b` and
`qwen/qwen3-coder-30b-a3b-instruct` are unrelated models from unrelated
providers, and they fail the same way.

This is the case for the entire project. A single-sample evaluation sees a
plausible function. Only an independently verified probe reveals that the
signature is wrong. `qwen/qwen3-30b-a3b-instruct-2507` produced **zero**
failures across all 15 probes — either it handles arity correctly or the FMs
never reached the case, and the control below is what distinguishes those.

### The finding about yield

**40 raw hits, 4 distinct bugs — a 10× inflation.** The first corpus reported
24 records containing 2 bugs. The dedup key was `(fm_id, category)`, so a
single defect found by two FMs across 10 probes each was recorded 20 times.

Two consequences, both serious:

- Any yield figure reported as raw record count overstates discovery by an
  order of magnitude.
- Fine-tuning on those records would have taught one bug 12 times and nothing
  else.

`dedup_cluster` is now `(task_family, error_type, category)`. Two records
sharing a cluster are the same bug found twice, regardless of which FM found
it. Both numbers are printed side by side so the inflation stays visible.

### Limitations, stated plainly

1. **Four failures is a narrow corpus.** All four are one class: signature or
   arity confusion. Training on 4 instances of one bug teaches one fix and
   will not demonstrate transfer.
2. **One generation per family.** Sampling variance within a model is
   unmeasured. A model could fail 1 time in 5 and this run would not show it.
3. **Six tasks, three used.** The original single-argument families
   (`list-dedupe`, `running-max`, `parity-counts`) produce zero failures on
   every model tried. They are too easy to carry signal.
4. **No fine-tuning has been run.** The primary hypothesis is untested.

---

## Track A — the control

A zero hit rate is ambiguous: it means either the model is correct or the
instrument is blind. The control resolves it by injecting a known bug into the
model's own output and re-probing with the identical probe set.

```bash
.venv/Scripts/python.exe scripts/run_live_control.py --model <id> --per-family 5
```

| Model | Originals failing | Mutants detected | Verdict |
|---|---|---|---|
| `qwen/qwen3-coder` | 0/30 | 3/3 families | instrument works, model correct |
| `qwen/qwen3-30b-a3b-instruct-2507` | 0/30 | 3/3 families | instrument works, model correct |
| `meta-llama/llama-3.2-1b-instruct` | 20/30 | 3/3 families | instrument works, model genuinely fails |
| `qwen/qwen3-coder-30b-a3b-instruct` | 20/30 | 1/3 families | see note |

**Note on the `1/3`.** `qwen3-coder-30b` already fails 2 of 3 families before
any mutation, so for those families a mutant cannot be *newly* detected — the
original was already failing. The family that reads `0` mutants
(`chunk-sum`) is the one the model actually gets right, and there the mutant
was caught 10/10. The "3/3" column counts only families where the original
was clean.

This control is the load-bearing result for every zero in this document. A
pipeline that finds nothing is only informative if it has been shown able to
find something.

---

## Track B — noise floor

### Phase 1: text comparison (negative)

```bash
.venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'src');
from fmverify.noise_floor import run_experiment
[print(s.summary()) for s in run_experiment('high_noise',n_noise=300,n_signal=200,seed=1234).values()]"
```

| Metric | False alarm | Power | d′ | Verdict |
|---|---|---|---|---|
| `exact_hash` | 0.948 | 1.000 | +0.33 | unusable |
| `token_jaccard` | 0.212 | 0.000 | **−0.97** | unusable |
| `edit_distance` | 0.052 | 0.000 | **−1.46** | unusable |

Negative d′ means real functional change is **no more detectable than sampling
variance**. No threshold fixes this, because the signal is not in the
representation being measured. This was a stated kill criterion in
`docs/thesis.md` and it fired.

### Phase 2: decision-level comparison (positive)

Reduce each response to the decision it encodes, then compare decision
distributions by total variation.

| Profile | False alarm | Power | d′ | Verdict |
|---|---|---|---|---|
| `low_noise` | 0.030 | 1.000 | +31.6 | usable |
| `high_noise` | 0.037 | 1.000 | +32.6 | usable |
| `very_high_noise` | 0.028 | 1.000 | +31.2 | usable |
| `uncertainty_noise` | 0.033 | 1.000 | +33.3 | usable |

*(n_probes=10, samples_per_probe=16, target FA=0.02)*

**Threshold transfer** — calibrated on `low_noise` alone (0.3125), applied
unchanged:

| Profile | False alarm | Power |
|---|---|---|
| `high_noise` | 0.015 | 1.000 |
| `very_high_noise` | 0.020 | 1.000 |
| `uncertainty_noise` | 0.022 | 1.000 |

**The boundary.** `uncertainty_noise` is built to defeat the method — a system
whose *decision* is unstable. At 5 probes it failed honestly (FA = 0.107,
over the 0.10 bar); at 10 with a tighter threshold it passes. Extraction
removes paraphrase noise and cannot remove epistemic noise.

### Track B limitations

1. **Synthetic target.** A stochastic generator, not a provider. The *method*
   transfers; the false-alarm rates do not.
2. **Independence assumption is optimistic.** Probes from one deployed system
   share a model, prompt, and provider. The reported N is a lower bound.
3. **Placeholder vocabulary.** The extractor knows four answer tokens plus
   `<refusal>`. Real deployments need a domain extractor.

---

## Offline harness results

Synthetic targets with documented weaknesses, so the instrument is validated
before it touches a paid API.

| Target | Verified failures | Unexplained | Clean control |
|---|---|---|---|
| Flawed (3 known weaknesses) | 25 | 0 | — |
| Correct (must yield zero) | — | 0 | **0 — PASS** |

The control is the point. A pipeline that reports failures on correct code is
not measuring anything.

---

## Bugs found and fixed

Each produced a *plausible* wrong answer rather than an obvious failure, which
is why each is now pinned by a test.

| Bug | Consequence if unfixed |
|---|---|
| `resource` rlimits absent on Windows | Every candidate failed at import; all failures looked like target defects |
| Complexity test raced a timeout | Flaky by construction; outcome depended on host load |
| `n_probes_per_pair=1` produced no pairs | Phase-1 reported `power=0.0` for every metric |
| Drift rewrote only sometimes-present tokens | Same false zero, different cause |
| `probes_for_confidence` sign error | Noisier system appeared to need *fewer* probes |
| Test asserting that direction was backwards | The function was right, the test was wrong |
| Decision extractor read `otherwise` as the decision | **Reported a usable result where the honest answer was failure** |
| FMs emitted bare lists for multi-arg families | A third of hard probes uncallable; verifier scores those as model defects — would have poisoned the corpus |
| Runner inferred arity from the *candidate* | A wrong-signature solution was scored on a task it was never given |
| Hard-task oracle keyed on first argument only | Every lookup missed; oracle silently a no-op |
| No `max_tokens` sent | HTTP 402 on every request |
| Reasoning model returned `content: null` | **3 fabricated `SyntaxError` failures the model never committed** |
| Live runner called oracle the single-arg way | Crashed a paid run after generations were billed |
| Dedup keyed on `(fm, category)` | **24 records containing 2 bugs; 10× yield inflation** |

The two bolded entries are the dangerous class: both produced confident,
plausible, wrong *findings* rather than errors.

---

## What is not done

- **No fine-tuning. No transfer result.** The primary hypothesis is untested.
  This is the single largest gap.
- **No repairs.** The corpus has no vetted corrections, so there is no
  treatment data yet.
- **Corpus is 4 distinct failures, one class.** Not enough to demonstrate
  generalization.
- **No witness-set selection.** Minimal-subset clustering is specified, not
  implemented.
- **No significance mapping.** Detection is built; regulatory characterization
  is not.
- **No real-provider Track B numbers.** Needs Phase 0 instrumentation.
- **Six task families defined, three used.** The single-argument families
  produce zero failures on every model tried.
