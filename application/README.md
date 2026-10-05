# Track B — Application: Witness

> **The same discipline, pointed at a system you do not control.**

Continuous, automated answer to one question:

> **Has this AI system materially changed since we last assessed it — and does
> that change count as significant?**

This is the applied half of the project. For the unifying argument, read
[../docs/thesis.md](../docs/thesis.md) first.

**Status: specified, not deployed.** The empirical premise is unverified, the
core research question is open, and no witness set has been run against a
production system. The largest risk is named in §5.

---

## The problem, in the field's own words

Model providers change what your system does without you changing anything.
Three distinct mechanisms are now observed:

| Mechanism | What happens | Why it is hard |
|---|---|---|
| **Alias drift** | You call `model-latest`; the provider silently resolves it to a new version | The API contract is unchanged. Nothing errors. |
| **In-place refresh** | Even a *pinned* name moves — a weights, tokenizer, or safety-layer change with no version bump | Pinning does not eliminate drift. The name says nothing changed. |
| **Deprecation cliff** | A pinned snapshot is retired on a date | Deterministic and scheduled, but arrives as a hard failure if untracked |

The failure surfaces weeks later: output format narrows, a JSON parser starts
receiving markdown fences, a tool-call schema shifts, and the error rate climbs
gradually. It reads as intermittent user error rather than a systematic change.
By then nobody can reconstruct which change caused it.

**The premise is not yet established by our own data.** A figure circulating in
the field — that a large majority of meaningful agent behavior changes originate
from model updates rather than from anything the team touched — traces to a
single vendor blog and is unverified. See §5. Treat it as a hypothesis, not a
foundation.

Meanwhile the obligation is live and largely untooled. EU AI Act Art. 12(2)(b)
names the exact capability: identifying situations that may lead to
*substantial modification*. Art. 72 requires post-market monitoring across the
system lifetime. Compliance guidance instructs teams to assess whether an update
constitutes a significant change requiring re-assessment — and then stops,
because no instrument for that assessment exists.

---

## What the loop was, and what this is

The failure-driven loop watches a component you control: you can fix the bug it
finds, and the compiler will confirm the fix. The same problem in production is
worse in one specific way and better in another.

**Worse:** the system is not yours. You cannot patch the provider, and there is
no PR to blame. Meanwhile the question is not "is this program correct?" but
"is this still the system we assessed, and does the difference matter
legally?" That has a deadline attached.

**Better:** the verifier is free. You do not need an LLM judge, because you
are not judging a claim — you are measuring a divergence against a stored
fingerprint. The system cannot game a measurement it does not know is
happening. This is the strongest possible form of the exogenous gate from
[thesis.md §1](../docs/thesis.md#1-the-claim).

So the shape is preserved and the roles are remapped:

| FM loop (Track A) | Witness (Track B) |
|---|---|
| Target model you control | Deployed system you do not control |
| Failure = output contradicts the contract | Failure = behavior diverges from an assessed baseline |
| FM proposes an attack | Scheduled probe execution proposes the comparison |
| Verifier compiles and runs | Fingerprint comparison against a stored baseline |
| Correction closes the gap | Significance assessment characterizes the gap |
| Gain = held-out attacker pass rate | Value = evidence for a human determination |

**Witness produces evidence for a determination, not the determination.** The
legal judgment stays with a human. That is not a hedge — it is precisely what
makes the thing adoptable by anyone who can be sued, and it is the framing that
survives contact with a compliance officer.

---

## The instrument nobody has built

A **witness set** is a small set of probe inputs whose responses fingerprint
actual system behavior.

```text
  ┌──────────────┐
  │ Seed suite   │  broad input coverage
  └──────┬───────┘
         │ cluster by behavioral similarity
         ▼
  ┌──────────────┐
  │ Witness set  │  minimal subset, versioned, frozen
  └──────┬───────┘
         │ re-run on schedule or CI trigger
         ▼
  ┌──────────────┐
  │  Fingerprint │  stored decisions + capability clusters
  └──────┬───────┘
         │ compare
         ▼
  ┌──────────────────────────────────────┐
  │  Behavioral diff                     │  which clusters moved
  │  Significance assessment             │  mapped onto intended purpose
  │  Append-only attestation artifact    │  audit evidence
  └──────────────────────────────────────┘
```

Four stages. Detection, characterization, significance, and record. The
significance step is where a hard problem is handled honestly — see §3.

---

## The metric, and how it was arrived at

This section reports what the experiments in `src/fmverify/` actually found.
Two phases, and the first one failed.

### Phase 1 — text comparison: a negative result

Every candidate that compares two responses as text was measured against both
sampling noise and real functional change.

| Metric | False alarm | Power | d′ | Verdict |
|---|---|---|---|---|
| `exact_hash` | 0.948 | 1.000 | +0.33 | unusable |
| `token_jaccard` | 0.212 | 0.000 | **−0.97** | unusable |
| `edit_distance` | 0.052 | 0.000 | **−1.46** | unusable |

*(high_noise profile, n=300 noise / 200 signal)*

`exact_hash` alarms on 95% of pure resamples — it cannot tell a re-sample from
a real change. The other two have **negative d′**: real functional change is no
more detectable than sampling variance. A negative d′ means the metric is
tracking noise, not function. There is no threshold that fixes this, because
the signal is not in the representation being measured.

This was a kill criterion, and it fired.

### Phase 2 — compare decisions, not text

The structural fix: reduce each response to **the decision it encodes**, then
compare decision distributions.

```text
response --[extract decision]--> decision --[total variation]--> distance
```

Paraphrase and hedging are discarded by the extraction step, so sampling noise
collapses **by construction** rather than by threshold tuning. That invariance
is the whole point — a threshold can be tuned until it looks good on one noise
profile, but an extractor that discards irrelevant variation is quiet on every
profile by design.

| Profile | False alarm | Power | d′ | Verdict |
|---|---|---|---|---|
| `low_noise` | 0.030 | 1.000 | +31.6 | usable |
| `high_noise` | 0.037 | 1.000 | +32.6 | usable |
| `very_high_noise` | 0.028 | 1.000 | +31.2 | usable |
| `uncertainty_noise` | 0.033 | 1.000 | +33.3 | usable |

*(n_probes=10, samples_per_probe=16, target FA=0.02, 400 noise / 300 signal)*

Two properties matter more than the headline d′:

- **The threshold transfers.** Calibrated on `low_noise` alone (0.3125) and
  applied unchanged, the false-alarm rate stays at 0.015–0.022 on every other
  profile. Calibration is therefore not a per-customer cost, which is what
  makes the product deployable rather than a bespoke integration.
- **The failure mode is known and bounded.** `uncertainty_noise` is a profile
  built to defeat the method: a system whose *decision itself* is unstable from
  sample to sample. Extraction removes paraphrase noise and cannot remove
  epistemic noise. At 5 probes the method failed it honestly (FA=0.107, above
  the 0.10 bar); at 10 probes with a tighter threshold it passes. The boundary
  is reported rather than hidden.

### Why these numbers are not yet a product

Three limits, stated plainly:

1. **The target is synthetic.** A stochastic generator stands in for a deployed
   system. The *method* transfers; these specific false-alarm rates do not.
   Real numbers require Phase 0 against a real provider.
2. **The independence assumption is optimistic.** Converting a per-probe
   false-alarm rate into a probe count assumes independent probes. Probes from
   one deployed system share a model, a prompt, and a provider, so the true N
   is higher. The reported N is a lower bound and the cap is the number to plan
   against.
3. **The answer vocabulary is a stand-in.** `extract_decision` currently
   recognises four tokens plus `<refusal>`. A real deployment needs an extractor
   for its own decision space — structured output, a schema, or a validated
   classification — which is where the engineering effort actually goes.

### Claims, in priority order

1. **Minimal witness sets.** For each real drift type, the probe count needed to
   detect it at fixed confidence — quantified, with the tradeoff curve. A
   hundred-probe set that catches 95% of drift is a deployable product; a
   thousand-probe set is a research result.
2. **Severity is not one-dimensional.** A system can drift in ways that are
   functionally irrelevant and ways that are regulatory. Show they can be
   separated, and show the text metrics in §3 cannot.
3. **Phase 2 is a result worth publishing on its own.** "Text-comparison drift
   metrics have negative d-prime under sampling noise, and reducing responses to
   decisions fixes it structurally" is a clean, reproducible claim that
   generalizes past this project.

### Ground truth is cheap

Drift events are **reproducible on demand** — swap a provider, flip a pinned
version, change a system prompt, rotate a retrieval index. Hundreds of real
drift events with real ground-truth labels are generatable for the cost of API
calls. This is not a benchmark that has to be hunted for; it is an
experimental setup that can be constructed deliberately.

---

## Design constraints

Non-negotiable, derived from what regulated buyers actually require.

1. **A light probe budget.** A witness set needing 10k calls per day is not
   deployable in a regulated enterprise. Budget for minimality from the start —
   it is the research contribution, not an optimization.
2. **The system must not know it is being assessed.** Graded on the principle
   that the exogenous signal is the entire value. Any design that relies on the
   system's self-report is disqualified.
3. **Deterministic replay.** Anyone auditing an artifact must be able to
   reproduce a diff. Store the exact request set, not just the conclusion.
4. **No legal conclusion, ever.** The system maps a change onto a declared
   intended purpose and risk profile and reports what moved. It does not decide
   whether the system is compliant. A tool that makes the legal call is a tool
   nobody in a regulated company can deploy.
5. **Cost and latency visibility.** An assessment that costs more than the
   re-assessment it triggers has failed. Report cost per assessment explicitly.
6. **Versioned, append-only, exportable.** The artifact is a compliance file.
   It has to survive being handed to an auditor three years from now.
7. **Assume probe-set decay.** The witness set will drift itself. Budget for
   maintenance, or every customer's second year fails.
8. **Provider friction is a design constraint, not an edge case.** Rate limits,
   ToS constraints, and the fact that you are characterizing someone else's
   model for a compliance file all shape the design. Assume a small budget and
   make the tool good at small budgets.

---

## Regulatory mapping

Engineering guidance for a conversation with counsel. **Not legal advice.**

| Obligation | What it requires | Where Witness fits |
|---|---|---|
| Art. 12(2)(b) — record-keeping | Logging that identifies situations which may lead to a **substantial modification** | The change-detection and characterization stages are this capability |
| Art. 72 — post-market monitoring | Systematic collection and analysis across the system lifetime | The scheduled re-run and the append-only record |
| Art. 11 / Annex IV — technical documentation | Current documentation, retained ten years | The exportable attestation artifact |
| "Significant change" assessment | Whether an update requires re-assessment or re-registration | The significance stage — **evidence, not determination** |
| Art. 26(5) — deployer monitoring | Deployers report abnormal behavior to the provider | The same diff, surfaced to the deployer rather than the provider |

**One caveat, and it is load-bearing.** The Digital Omnibus political agreement
of May 2026 moved Annex III high-risk obligations from August 2026 to December
2027, with Annex I to August 2028. At least one September 2026 industry
publication still advises treating August 2026 as operative, so the current
status is disputed between sources. **Verify the present legal position before
building any timeline on it.**

The recurring-obligation argument survives either outcome: the monitoring
requirement does not disappear, it is only deferred. What a deferral weakens is
the *urgency*, not the substance. Do not let a deadline claim carry more weight
than it can bear.

---

## Risk register

The five ways this dies, roughly in order of likelihood.

| Risk | Status | Mitigation / what would resolve it |
|---|---|---|
| **Silent drift is rare or inconsequential** | **Unverified premise.** The circulating figure traces to one vendor blog | Instrument real systems first. One week. If drift is rare, the urgency argument collapses and the product is a compliance checkbox |
| **No metric separates signal from noise** | Open | The experiment in §4. If it fails, the product has no core |
| **Probe budget incompatible with real limits** | Open | Budget-driven design. Measure cost per assessment as a first-class metric |
| **Adjacent incumbents** | Real | LangSmith, Braintrust, Arize, WhyLabs, Galileo, MLflow all do monitoring. Differentiation: they monitor *your* stack, where you control the model. This detects change in a component you do **not** control and emits regulatory-grade evidence rather than dashboards. If that cannot be stated in one sentence, it is competing on features and losing |
| **Witness-set decay** | Real | Assume it, version it, automate refresh, measure its own health |

A sixth consideration: providers may resist being characterized. Rate limits,
ToS constraints, and the fact that you are writing a compliance file about
someone else's model are all real friction. Design for it from the start.

---

## Phase plan

**Phase 0 — Measure the premise.** *Do this before writing product code.*
Instrument several real systems. Log how often provider-side model identity
actually changes, what changed, and what broke downstream. This produces the
justification for everything else, and it must be first-party data. If drift is
rare, stop here and say so.

**Phase 1 — The metric.** Across a grid of real drift types, measure how many
probes detect each at fixed confidence, and how badly each candidate metric in
§3 false-alarms on pure sampling noise. Expect the naive metrics to fail
visibly. That failure table is the research result.

**Phase 2 — Significance.** Map moved capability clusters onto declared
intended purpose and risk profile. Demonstrate separation of
functionally-irrelevant from regulatory drift. Get counsel to review the mapping
— the design is more credible with a lawyer's fingerprints on it, not fewer.

**Phase 3 — The artifact.** The append-only attestation record, export format,
and replay. The product is a file someone can hand to an auditor.

**Phase 4 — Adversarial self-test.** Deliberately construct a witness set that
*misses* an induced regression, and document the blind spot. Knowing where the
instrument fails is a stronger claim than knowing where it works, and it is the
thing no competitor will publish.

---

## The first demo

One system, one baseline, one induced drift event, one artifact:

```text
  baseline fingerprint  →  induced drift  →  behavioral diff
  →  significance assessment  →  attestation record
```

Show the diff, show the assessment reasoning, show the artifact a compliance
officer would sign. If the demo cannot be explained in one paragraph without a
hand-wave, the product is not ready to be argued about.

---

## Related

- Unifying argument and prior art: [../docs/thesis.md](../docs/thesis.md)
- The research track this is derived from: [../research/README.md](../research/README.md)
- Schemas: [schema/](schema/)
