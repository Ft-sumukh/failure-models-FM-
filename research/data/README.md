# Failure dataset

The record format, split policy, and admission rules that make a failure
usable as training signal — and auditable as a claim.

**Status: schema and policy in use. Corpus holds 4 distinct failures, all one
class. No fine-tuning has been run. See [../../RESULTS.md](../../RESULTS.md)
for the live measurements.

The corpus directory is **gitignored**. Its contents are model outputs from a
specific provider at a specific time, and a stale corpus is worse than an
empty one — records generated before the dedup key was fixed would have
reported 24 failures where 4 existed. Regenerate with
`scripts/run_live_probe.py` rather than committing a run.

Related: [../README.md](../README.md) · [../../docs/thesis.md](../../docs/thesis.md)

---

## Why a record and not a text file

Because the two failure modes of an adversarial loop are both invisible in
aggregate output:

1. **A wrong label that quietly poisons training.** If bad records look like
   good ones in a log, nobody notices until the model degrades.
2. **A boundary breach that nobody can prove didn't happen.** If evaluation
   data and training data live in the same table, "we held it out" becomes an
   unverifiable claim.

A record separates four facts that must never be merged: **what the FM asked**,
**what the target returned**, **what the verifier observed**, and **what the
analyzer inferred**. Each can be audited or rejected independently.

---

## Record schema

JSON Schema draft 2020-12. Validated by
`schema/failure_record.schema.json`.

| Field | Type | Purpose |
|---|---|---|
| `record_id` | string | Stable unique identifier |
| `status` | enum | `verified_failure`, `non_failure`, `invalid_probe`, `rejected` |
| `schema_version` | string | Record format version |
| `task` | object | Contract, oracle reference, language, split |
| `attack` | object | FM identity, version, strategy, seed, input, provenance |
| `target` | object | Model, prompt hash, decoding settings, output reference |
| `verification` | object | Verifier version, verdict, expected/actual, replay, limits |
| `analysis` | object | Taxonomy labels, summary, proposed repair, repair verification |
| `dataset` | object | Training eligibility, exclusion reason, dedup cluster, timestamps |

The three fields that carry the most weight, and why:

- **`attack.fm_id` + `fm_version`** — the split is meaningless without them.
  "Held-out attacker" is a claim about identity, and a renamed FM defeats it.
- **`verification.verdict` + `reproduction_command_ref`** — a failure is only
  real if it replays. The command reference is what makes that checkable by
  someone who does not trust us.
- **`dataset.exclusion_reason`** — the most important field in the schema.
  Records that were considered and *rejected* are what prove the admission
  process discriminates. An inclusion-only log cannot demonstrate that.

### Status is not a binary

`verified_failure` is the only status eligible for training. `non_failure` means
the FM proposed something that did not reproduce — worth keeping, because it
calibrates the FM's precision. `invalid_probe` means the probe itself was
broken (bad task, flaky test, infrastructure fault, wrong oracle) and says
nothing about the target. `rejected` means it was a real failure but failed the
gate. Conflating these three inflates both yield and the appearance of rigor.

---

## Split policy

Four splits, partitioned **before** any discovery happens, and partitioned by
family rather than by row.

| Split | Purpose | Used by training? | Used by tuning? |
|---|---|---|---|
| `train` | FM discovery and treatment data | Yes | Yes |
| `validation` | Hyperparameters, checkpoint selection | No | Yes |
| `sealed_eval` | Transfer measurement, once, at the end | Never | Never |
| `dev` | Infrastructure, prompts, smoke tests | No | No |

Partition keys, in priority order:

1. **FM family** — at least one family is reserved from all training and tuning.
2. **Attack template** — the same template stays in one split.
3. **Task family** — related task variants stay together.
4. **Exact hash** — as a last-resort backstop, not a primary mechanism.

Hash-based splitting is a common default and it is insufficient. Near-duplicate
tasks and variant templates leak across hash boundaries constantly, because
they differ in surface form while testing identical capability.

**The rule that matters:** a renamed or reprompted training FM is not an unseen
attacker. Attacker identity is the claim under test. If the held-out FM shares a
generator lineage, a prompt, or a template with any training FM, the
evaluation measures recall of the training distribution.

### Splits live in the manifest, not in the code

`splits/split_manifest.json` declares each split's reserved families before
discovery. Adding a family to `sealed_eval` after seeing results is the single
easiest way to invalidate this entire project, and it is not detectable from
the published numbers. A family moves to a different split only by opening a
documented change with a reason and a date, and results under the old
assignment are reported too.

---

## Admission rules

A record reaches the training corpus only if **all** of these hold:

1. **Valid task** — the contract is well-formed and the oracle is trusted.
2. **Reproduces** — the failure replays from a clean environment via
   `reproduction_command_ref`, detached from the search that found it.
3. **Exogenous expectation** — expected behavior comes from a trusted oracle
   or independent property, never from the FM or the analyzer.
4. **Repair verifies** — any proposed correction independently passes the
   verifier. An unverified repair is a hypothesis.
5. **No split contamination** — the record's task family and FM family belong
   to the training split.
6. **Not a near-duplicate** — fails both exact-hash and semantic-family
   deduplication against the corpus.
7. **Logged either way** — admitted or rejected, with a reason. Silence is not
   a disposition.

Rule 2 is the falsifier requirement from "Agent Hacks Agent" (arXiv
2607.11698): a break that only reproduces inside the search loop that found it
is not a validated claim.

---

## File layout

```text
data/
  README.md                          this file
  schema/
    failure_record.schema.json       record schema
    example_record.json              one verified failure, fully worked
  splits/
    split_manifest.json              reserved families per split — empty
  corpus/                            created when the first verified failure exists
```

## Anti-patterns this format exists to prevent

- One CSV of prompts and outputs, with the oracle's expectations inlined. Makes
  it impossible to audit whether a label came from an oracle or from the model
  that guessed wrong.
- Shuffling and splitting at the end, after discovery, when you already know
  which rows are hard.
- Storing only successes. Silent, uncorrectable, and it means the negative
  results are unreproducible.
- Version pinning in prose rather than in a field. "We used X" in a paper body
  is not a version.
- Logging the score but not the protocol. A pass rate without a denominator,
  split, and attack budget is not a measurement.
