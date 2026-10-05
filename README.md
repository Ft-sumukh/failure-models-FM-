# FAILURE MODELS (FMs)

> **Break it before the world does. Then prove the break was real.**

One idea, two tracks.

Failure-Driven AI inverts the usual training signal. Instead of showing a model
what success looks like, you let something hunt for the smallest, clearest
case where it fails, verify that the failure is real, and turn the evidence
into a correction. A different attacker goes next.

That loop is a **research** program. The same machinery, pointed at systems
you do not control and cannot fix, is an **application**: a continuous answer
to the question every regulated team now has to answer and nobody has built
instruments for.

|  | Track | Question it answers | Artifact |
|---|---|---|---|
| **A** | [Research — the FM loop](research/) | Does learning from verified failures improve a model on failures from an attacker it never trained against? | A measured result about transfer |
| **B** | [Application — Witness](application/) | Has this AI system materially changed since we last assessed it, and does that count as significant? | Regulatory-grade evidence for a human determination |

**Start with [RESULTS.md](RESULTS.md)** — every measurement taken so far, with
the command that produced it. **[BUILD.md](BUILD.md)** covers the code.

Where things actually stand:

- **Track A** has a working loop, validated offline and against live models. A
  3-model sweep produced **40 probe hits resolving to 4 distinct failures**,
  and the control confirms the instrument finds injected bugs whenever the
  original was clean. **No model has been fine-tuned, so the primary
  hypothesis is still untested.**
- **Track B** produced a genuine negative result, then a fix. Text-comparison
  metrics have d′ between −1.5 and +0.3 and are unusable; reducing responses to
  decisions before comparing reaches **d′ ≈ +32** with a threshold that
  transfers across noise profiles. Measured on a synthetic target, so the
  method transfers and the numbers do not.

---

## The one argument underneath both tracks

Everything in this repository rests on a single claim:

> **A verifier that descends from the model under test is not evidence.**

This is not a hunch, it is the documented failure mode of this entire field.

- **SEAL** (arXiv 2607.24300) names the *verifier–deployment gap*: agents that
  author their own tests can hold near-perfect self-scores while deployed
  performance degrades. Their fix is one bit of acceptance signal the agent
  cannot see or author.
- **Reflections on Trusting Trust, Revisited** (arXiv 2609.17817) shows
  benchmark-driven self-improvement is *poisonable*. A controlled benchmark
  induced SICA and Hyperagents to evolve unconditional HTTPS certificate
  bypass — 30/30 on clean held-out tasks — and the contamination survived
  subsequent evolution against clean benchmarks.
- **Agent Hacks Agent** (arXiv 2607.11698) shows the measurement fix: a
  falsifier that discards breaks which do not reproduce once detached from the
  search that found them, plus frozen, single-shot, held-out evaluation.

Track A exploits the same principle in the one domain where the verifier *is*
exogenous and therefore trustworthy: a program either compiles, passes an
independent test, and reproduces — or it does not. The compiler is not the
model's friend. Neither is the test oracle.

Track B refuses to trust a verifier at all and uses behavioral divergence
instead. It is the applied form of the same discipline: measure the system,
not the system's account of itself.

Read [docs/thesis.md](docs/thesis.md) for the full argument, the prior art we
are standing on, and the things that would kill us.

---

## Track A — Research: the FM loop

> Full design: **[research/README.md](research/README.md)**

**FM means Failure Model.** An FM is an adversarial generator whose job is to
expose weaknesses, not to help. It can be a language model, a fuzzer, a
mutation engine, hand-built heuristics, or a mix. The name describes its role
in the system, not a requirement that it be a neural network.

```text
FM proposes → target attempts → verifier decides → analyzer explains
  → human gate vets → dataset → candidate model → unseen FM evaluates
```

A plausible explanation is not a verified failure. An FM that does not
reproduce is not a discovery, it is a noise event.

The primary hypothesis is narrow and falsifiable:

> Does fine-tuning on verified failures found by training FMs improve the
> target's performance on failures from **held-out FMs**, without unacceptable
> regressions on standard code tasks?

**Status: specified, not run.** No robustness result is claimed anywhere in
this repository. See [research/data/README.md](research/data/README.md) for
the record schema and split policy.

---

## Track B — Application: Witness

> Full design: **[application/README.md](application/README.md)**

The verification problem reappears in production with a deadline attached.

Model providers change what your system does without you changing anything.
Three distinct mechanisms are now observed in the wild: **alias drift** (you
call `-latest`), **in-place refresh** (even pinned names move — a weights or
safety-layer change with no version bump), and **deprecation cliffs**. No
exception fires. No deploy exists to blame. The failure surfaces weeks later
as a slow parser-error rate that reads like intermittent user error.

Meanwhile the obligation is live and largely untooled. EU AI Act Art. 12(2)(b)
names the exact capability — identifying situations that may lead to
*substantial modification*. Art. 72 requires post-market monitoring across the
system lifetime. Compliance guidance tells teams to assess whether an update
constitutes a significant change requiring re-assessment, and then stops.

Witness is a small set of probe inputs whose responses fingerprint actual
system behavior. Re-run on a schedule. When the fingerprint moves you get a
diff, an automatic significance assessment, and an append-only audit artifact.

**The honest framing: Witness produces evidence for a determination, not the
determination.** The legal judgment stays with a human. That is not a hedge —
it is what makes the thing adoptable by anyone who can be sued.

**Status: specified, not deployed.** The empirical premise is unverified and
the research core is open. See the risk register in
[application/README.md](application/README.md).

---

## Repository layout

```text
README.md                    this file — two tracks, one argument
RESULTS.md                   every measurement, with the command that produced it
BUILD.md                     what is implemented, what is not, how to run it
docs/
  thesis.md                  the unifying claim, prior art, kill criteria
research/
  README.md                  Track A: the FM loop, in full
  data/
    README.md                record schema, split policy, admission rules
    schema/                  JSON Schema + worked template
    splits/                  split manifest (empty, by design)
    corpus/                  live run output (gitignored, regenerated per run)
application/
  README.md                  Track B: Witness, in full
  schema/                    witness-set and attestation schemas
src/fmverify/                the harness
tests/                       85 tests
scripts/                     run_demo.py (offline), run_live_*.py (live)
```

---

## What is not claimed

- **No model has been fine-tuned. No transfer result exists.** The primary
  hypothesis is untested, and this is the largest gap in the project.
- **No transfer measurement.** `sealed_eval` is empty, so the held-out
  attacker has never been run against a candidate model.
- **The live corpus is 4 distinct failures, all one class** — signature and
  arity confusion. Too narrow to demonstrate generalization. 40 probe hits
  resolved to those 4; raw record count overstates yield by 10×.
- **No witness set has been deployed.** Silent drift is **not** yet established
  as frequent or consequential by our own data — the figure circulating in the
  field traces to a single vendor blog and we treat it as unverified.
- **No legal conclusion is offered.** The regulatory mapping in
  [application/README.md](application/README.md) is engineering guidance for
  a conversation with counsel, not a legal opinion, and one deadline in it is
  actively disputed between sources.

The broader ingredients have precedents across adversarial training, fuzzing,
data-centric learning, automated testing, and AI governance. This project
earns its claims through its measurements or not at all.

---

## How to start contributing

Read in this order:

1. [RESULTS.md](RESULTS.md) — what has actually been measured, including the failures.
2. [docs/thesis.md](docs/thesis.md) — what we believe and what would prove us wrong.
3. [BUILD.md](BUILD.md) — the code, and what is not built.
4. [research/README.md](research/README.md) — the loop and the experiment.
5. [application/README.md](application/README.md) — the product and its risks.
6. [research/data/README.md](research/data/README.md) — how a failure becomes a record.

The next real step is the transfer experiment: fine-tune on the verified
failures, then measure against the held-out `property-fm` on tasks it has
never seen. That is the first thing in this project that can actually be
wrong.
