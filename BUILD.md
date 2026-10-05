# Build status

What is implemented, how to run it, and what is *not* built. Read this before
the design docs — several claims in them are now supported by measurements, and
a few have been revised by them.

**For actual measurements, see [RESULTS.md](RESULTS.md).** This file is about
the code.

## Run it

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"

.venv/Scripts/python.exe -m pytest tests/ -q      # 85 tests
.venv/Scripts/python.exe scripts/run_demo.py      # both tracks, offline
```

Live runs need `OPENROUTER_API_KEY` and cost roughly $0.0002–0.0005 each:

```bash
.venv/Scripts/python.exe scripts/run_live_probe.py   --model <id>
.venv/Scripts/python.exe scripts/run_live_control.py --model <id>
```

Windows paths shown; on POSIX use `.venv/bin/python`.

The offline demo and the test suite need no API key and no network. Only the
two `live_*` scripts do, and they are the only things that spend money.

## What is built

| Module | Track | Purpose |
|---|---|---|
| `tasks.py` | A | Task contracts with reference-implementation oracles |
| `hard_tasks.py` | A | Three multi-argument families, harder by construction |
| `verifier.py` | A | Process-isolated compile-and-run, four distinct verdicts |
| `fms.py` | A | Boundary, adversarial-input, property FMs; split enforcement |
| `records.py` | A | Failure records, the acceptance gate, distinct-failure counting |
| `targets.py` | A | Candidates with documented weaknesses, plus a correct control |
| `llm_target.py` | A | Live code-model adapter over an OpenAI-compatible API |
| `noise_floor.py` | B | Phase 1: text-comparison metrics vs noise and drift |
| `decision_metric.py` | B | Phase 2: decision-level comparison |

## Measured results

Summarised here; full numbers and commands in [RESULTS.md](RESULTS.md).

- **Track A, offline:** 25 true positives, 0 unexplained on the flawed target;
  0 on the correct control.
- **Track A, live:** 3 models × 15 probes → 40 raw hits → **4 distinct
  failures**. A 10× yield inflation, and the dedup key was fixed because of it.
- **Track A, control:** injected bugs caught on every model where the original
  was clean. This is what makes each zero interpretable.
- **Track B phase 1:** negative. No text-comparison metric usable; d′ −1.5..+0.3.
- **Track B phase 2:** positive. Decision-level comparison reaches d′ ≈ +32 and
  its threshold transfers across noise profiles.

## What is not built

- **No LLM fine-tuning.** Nothing has been trained. The primary hypothesis is
  untested — this is the largest gap in the project.
- **No repairs.** The corpus has no vetted corrections, so there is no
  treatment data to train on.
- **No real-provider Track B numbers.** Track B's figures come from a synthetic
  stochastic target.
- **No witness-set selection.** Minimal-subset clustering is specified in the
  schema, not implemented.
- **No significance mapping.** Detecting change is built; assessing regulatory
  significance is not.
- **Corpus is 4 distinct failures, one class.** All signature/arity confusion.
  Too narrow to demonstrate generalization.
- **Split manifest is `frozen: false`** and `sealed_eval` is empty, so the
  transfer experiment is not yet runnable.

## Platform notes

The verifier runs candidates in a subprocess with a wall-clock deadline
enforced by the parent. POSIX `resource` rlimits are applied where available
and skipped where not — **Windows has no `resource` module**, and an earlier
version failed every candidate at import while reporting the failures as target
defects. The parent-side deadline is the limit that is real on every platform.

Process isolation bounds accidental damage — infinite loops, memory
explosions. It is **not** a security boundary against a determined adversary.
Running genuinely untrusted candidates needs containers or VMs.

## Provider notes

Two live-run failure modes that are harness problems, not model findings, and
that now raise rather than produce a record:

- **Reasoning models need token headroom.** Given a tight `max_tokens`,
  `qwen/qwen3.5-9b` spent its entire budget reasoning and returned
  `content: null`. The verifier scored the empty string as a `SyntaxError`,
  manufacturing three failures the model never committed. `EmptyResponse` is
  now raised.
- **`max_tokens` must be sent explicitly.** Omitting it makes the provider
  assume the model maximum (65536 for some models), which trips a credit cap
  and fails with HTTP 402.

## Bugs found while building this

Fourteen, tabulated in [RESULTS.md](RESULTS.md). Each is pinned by a test,
because each produced a *plausible* wrong answer rather than an obvious
failure. The two that mattered most both generated confident wrong **findings**
rather than errors: a hedge word read as a decision (reported a usable metric
where failure was honest), and an empty provider response scored as a defect.
