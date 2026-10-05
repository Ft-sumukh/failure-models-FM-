# The thesis

Everything in this repository follows from one claim. This document states it,
sets it against the literature, and names the conditions under which we would
abandon it.

---

## 1. The claim

> **A verifier that descends from the model under test is not evidence.**

If the thing deciding whether an output is correct was produced by the same
process that produced the output, then agreement between them carries no
information about correctness. It is a self-consistent fiction, and it is the
default architecture of most automated red-teaming and self-improvement work
in this field.

The fix is not a better judge. The fix is a signal the system under evaluation
cannot author, observe, or game.

---

## 2. The literature that forces this

These are the papers we are standing on. We cite them as prior art and do not
claim any of their contributions.

### The verifier–deployment gap

**SEAL — "Self-Authored Verification Is Unreliable in Heuristic
Self-Improving Agents"** (arXiv 2607.24300)

Self-improving agents accumulate capability by rewriting policies and
controllers, and typically rely on self-authored tests to decide whether to
accept each edit. The agent controls both the optimized object and its
verifier. Result: self-assigned scores stay near perfect while real deployment
performance degrades or stays flat. The paper names the discrepancy the
**verifier–deployment gap**, stratifies it by capability (weaker agents damage
previously acquired strategies behind easy self-tests; stronger agents still
misjudge the deployment distribution), and shows that ordinary self-written
constraints do not close it. Its fix, SEAL, adds an exogenous audit the agent
cannot author or inspect, delivers single-bit accept/reject, and retains the
incumbent on regression.

> **What we take:** the gate design. One bit, from outside, and retain-on-
> regress. Track A's acceptance gate is this idea instantiated where the
> exogenous signal happens to be a compiler and a test oracle.

### The attack on the loop itself

**"Reflections on Trusting Trust, Revisited: Contaminating Self-Improving AI
Coding Agents"** (arXiv 2609.17817)

The sharpest result in this space. The question is whether an adversary who
controls only a *benchmark* can induce a self-modifying coding agent to write
vulnerable code on neutral tasks. Answer: yes, for DGM, SICA, and Hyperagents.
A poisoned benchmark caused agents to evolve unconditional HTTPS certificate
validation bypass, producing vulnerable code in 30/30 samples on clean
held-out tasks, and contamination persisted through later evolution on clean
benchmarks. The attack must clear three gates: the seed agent must produce the
vulnerability on poisoned but not clean tasks; the self-improvement step must
represent it as a reusable improvement; the implementation step must preserve
rather than sanitize it.

> **What we take:** the threat model. Any FM loop that improves itself from
> its own discovery signal is, by construction, running this attack's setup.
> The mitigations they conclude are required — independent constraint
> enforcement, held-out neutral tasks, hard rejection for insecure behavior —
> are baseline requirements in Track A, not an enhancement.

### How to measure without fooling yourself

**"Agent Hacks Agent: Autoresearch for Production-Agent Red-Teaming"**
(arXiv 2607.11698)

Two methodological commitments we adopt wholesale. First, **falsification**: a
discovered break only counts if it still reproduces once detached from the
search that found it. The paper is explicit that judges are gameable and raw
success counts inflate; the falsifier is what converts a gameable score into a
validated claim. Second, **frozen, single-shot, held-out evaluation**: at
evaluation every method gets the same held-out instances and emits one attack
per instance with no further search, which measures whether the discovered
artifact is reusable rather than a product of its own search budget.

> **What we take:** discovery and evaluation must not share a search loop.
> Track A's sealed split and Track B's frozen witness set are both this rule.

### The loop we are not claiming to invent

**GPT-Red** (OpenAI, `openai.com/index/unlocking-self-improvement-gpt-red/`)
trains a red-teaming agent via self-play to elicit failures against
simultaneously-trained defenders, then uses those failures as training data and
measures robustness on held-out attacks. Structurally this is the FM loop. The
domain is prompt injection and the model is internal and closed, so no public
replication exists — but the loop is demonstrated.

**"Learning from Failure: Inference-Time Self-Improvement for Computer-Use
Agents"** (arXiv 2606.31270) uses the phrase "failure-driven self-improvement
loop" and does this for computer-use agents: an LLM diagnoses failure modes,
proposes inference-time fixes, generates lightly human-verified patches, and
upgrades the agent. 42.3% → 48.9% on OSWorld.

**SWE-smith** (arXiv 2504.21798) synthesizes 50k bug-bearing task instances
across 128 repositories and states the principle directly: execution-based
validation can validate solutions *and* identify bug candidates that cause
regression. **FeatAdd** (ICLR 2026) has agents add a feature and harvests the
failures caught by the test suite.

**"Self-Evolving Coding Agents"** (arXiv 2608.03392) surveys the space —
SIFT, EvoRepair, self-play SWE-RL, ACE, Sol-Ver, CURE — which tells us plainly
that reviewers will know it, and that a paper proposing the loop itself will
not survive review.

> **What we take:** the loop is prior art in five domains. Our contribution
> cannot be the loop. See §4.

### Why the instrumentation matters more than the loop

The test-generation literature is a warning about measurement:

- LLM-generated test **mutation score peaks around 0.546** versus **0.690** for
  human-written tests; one study found it can approach *zero* because the
  tests exercise ineffective logic such as interfaces and empty methods
  (arXiv 2511.21382, and related studies).
- Structural coverage sits **near ceiling and barely discriminates** between
  configurations — model choice explains more variance than prompt choice
  (arXiv 2609.24341).
- A 2026 large-scale replication across 11 SOTA LLMs (arXiv 2607.22880) finds
  coverage and mutation **stop being reliable indicators in exactly the
  setting that matters** — when the artifact under test may already be wrong
  and you are trying to expose the problem. It is context-dependent: useful
  when the code is assumed bug-free and you are doing regression testing,
  unreliable when you are hunting a live defect.

> **What we take:** proxy metrics fail precisely where we need them. Every
> number in both tracks is chosen because it survives contact with the failure
> mode it is meant to detect, and both tracks carry an explicit
> naive-metric baseline so the failure is documented if it occurs.

---

## 3. Two domains, one principle

The principle is the same in both tracks. The exogenous signal differs.

| | Track A — Research | Track B — Application |
|---|---|---|
| System under test | A code model you control | A deployed AI system you do **not** control |
| What "failure" means | Output contradicts the task contract | Behavior diverges from an assessed baseline |
| Exogenous verifier | Compiler, sandbox, independent test oracle | Actual behavioral divergence, measured not narrated |
| Why it cannot be gamed | The target does not write the oracle | The target does not know it is being measured |
| What we produce | A measured transfer result | An evidence artifact for a legal determination |
| Fails when | Held-out attacker shows no gain, or regressions appear | Drift turns out to be rare, or no metric separates signal from noise |

The reason Track B exists at all is that Track A's discipline is not optional
in production. The same verifier–deployment gap that makes a self-authored
test suite worthless makes a self-reported compliance claim worthless. The
question "is this still compliant?" has exactly the structure of "is this
program still correct?" — and it is answered the same way, by measuring
behavior against something the system did not author.

---

## 4. What our contribution actually is

Not the loop. Three things, in order of how much they matter:

1. **A demonstration in a domain where the verifier is trustworthy.** Code
   generation is the one setting where the verification signal is exogenous by
   construction. Showing that a failure-driven loop produces real transfer
   here — with the data-handling and gate discipline of SEAL and Agent Hacks
   Agent actually implemented rather than cited — is a contribution the
   existing LLM-judge-based instantiations cannot make.

2. **An instrument for the application.** Minimal probe sets that detect
   functional change while staying invariant to sampling noise. No such
   instrument is published, and the naive candidates are documented failures
   (hashes flag temperature noise; embedding distance flags paraphrase;
   LLM judges are gameable; coverage and mutation are context-dependent).

3. **An honest report of where it breaks.** Including the negative case. If the
   exogenous gate does not close the poisoning hole, that is a publishable
   result and we will report it.

---

## 5. Kill criteria

Cheap experiments. Both listed here are designed to invalidate the project.
Run them early and on purpose.

### Shared

- If held-out-attacker results do not separate from baseline beyond run
  variance, Track A is dead. Do not rescue it with more training data or a
  different attacker — that is the definition of overfitting the split.

### Track A specific

- If the sealed evaluation shows gains on training FMs but not on the held-out
  FM family, the loop produces attacker-specific memorization. Report that as
  the result.
- If a deliberately poisoned correction survives the acceptance gate into a
  candidate model, our gate is insufficient and must be redesigned before any
  claim is made.

### Track B specific

- **Measure the premise first.** Instrument several real systems and log how
  often provider-side model identity actually changes, and what breaks when it
  does. If silent drift is rare, the urgency argument collapses. This is one
  week of work and it is the single largest risk to the whole track.
- ~~If no candidate metric separates functional change from sampling noise at
  any probe-set size, the product has no core. Stop.~~ **Fired, and the
  follow-up worked.** Phase 1 measured this and no text-comparison metric
  separated signal from noise — `exact_hash` false-alarmed on 95% of resamples
  and the content metrics had negative d′. The kill criterion was correct and
  was treated as a finding rather than buried. Phase 2 replaced text comparison
  with decision-level comparison and reached d′ ≈ +32 on the same profiles,
  with thresholds that transfer across noise profiles. **The condition for
  stopping is now narrower and falsifiable: the method fails when the system's
  decision distribution is itself unstable, and that boundary is measured
  rather than assumed.**
- If the probe budget needed for adequate detection power is incompatible with
  a regulated enterprise's rate limits and cost floor, the product is not
  deployable regardless of how well detection works.
- If a witness set cannot be maintained without continuous manual curation, it
  will silently decay in every customer and the second-year business does not
  exist.

### What is still unmeasured

Track B's numbers come from a synthetic stochastic target, not a provider. The
*method* transfers; the specific false-alarm rates do not. The probe-count
figure assumes independent probes, which probes from one deployed system are
not. Both limits are stated in
[application/README.md](../application/README.md#why-these-numbers-are-not-yet-a-product)
and neither is resolved without Phase 0.

---

## 6. What we will not do

- Claim a loop as an invention when five prior works instantiate it.
- Report a score without a denominator, a split, and a protocol.
- Treat a self-authored judge as a verifier.
- Call a red-team prompt a discovery, a passing test a proof of safety, or an
  improved score a general capability result.
- Offer a legal conclusion. We produce evidence. Counsel makes determinations.
- Do not promise compounding. Each iteration is a hypothesis, retained only if
  the predeclared evaluation supports it.
