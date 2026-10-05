"""Track B, phase 2: a metric that reduces responses to decisions.

The phase 1 result was negative and it is the reason this module exists.
Comparing two responses as text cannot work: ``exact_hash`` false-alarms on
76-98% of pure resamples, and token/edit distance have *negative* d-prime
under noise, meaning real functional change is no more detectable than
sampling variance. See ``noise_floor.py``.

The structural fix is to stop comparing text. Reduce each response to a
canonical decision first, then compare decisions:

    response --[extract]--> decision --[compare]--> distance

Paraphrase and hedging are removed by the extraction step, so resampling
noise collapses to near-zero by construction rather than by threshold tuning.
That invariance is structural, which is the whole point: a threshold can be
tuned until it looks good on one noise profile, but an extractor that discards
the irrelevant variation is quiet on every profile by design.

The method, stated without reference to any particular answer vocabulary:
**reduce each response to the decision it encodes, aggregate per-probe
distributions, and compare distributions.** Decisions are countable, so a
small N of probes is enough -- which is what makes a deployable probe budget
possible at all.

And its own boundary, which is measured in the same run: a noise profile where
the *decision itself* is unstable. Extraction removes paraphrase noise and
cannot remove epistemic noise. ``uncertainty_noise`` exists to make the method
fail honestly rather than to flatter it.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Sequence

#: Hedges and framing that vary between samples of a stable system. These are
#: stripped before comparison, which is what makes the metric quiet on noise.
#: A real deployment builds this list from its own observed sampling variation
#: -- the principle is "discard what varies when nothing has changed".
FILLER_PREFIXES = (
    "the answer is",
    "the answer appears to be",
    "the result appears to be",
    "it looks like",
    "result:",
    "output:",
    "answer:",
)

FILLER_SUFFIXES = (
    "",
    " roughly",
    " approximately",
    " i think",
    " (likely)",
    " -- see notes",
    " ???",
)

#: Hedges that attach to an answer without changing it. Stripped before the
#: content token is read. ``otherwise`` is the one that bites: "alpha or maybe
#: otherwise" naively tokenizes to "otherwise", which is a *stable* token, so
#: the hedge silently becomes the decision and an unstable system looks quiet.
#: A first cut of this extractor shipped that bug and the method reported a
#: clean result on the profile designed to defeat it.
HEDGE_TOKENS: tuple[str, ...] = (
    "or",
    "maybe",
    "otherwise",
    "perhaps",
    "possibly",
    "probably",
    "roughly",
    "approximately",
    "likely",
    "think",
    "guess",
    "unclear",
    "note",
    "notes",
)

#: Refusal and abstention are decisions, not failures of extraction. They must
#: be represented explicitly, because a system that starts refusing IS a
#: functionally changed system.
_REFUSAL = "<refusal>"
_UNKNOWN = "<unknown>"

_TOKEN_RE = re.compile(r"[a-z]+", re.IGNORECASE)

ANSWER_TOKENS: tuple[str, ...] = ("alpha", "beta", "gamma", "delta")


def extract_decision(response: str) -> str:
    """Reduce a response to the decision it encodes.

    Returns one of the answer tokens, ``<refusal>``, or ``<unknown>``. The
    domain-specific part is the token vocabulary; the general part is that the
    function maps a response onto a small countable set, so that variation
    which does not change the decision can be discarded.
    """
    text = response.strip().lower()
    for suffix in FILLER_SUFFIXES:
        if suffix and text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    for prefix in FILLER_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip().strip(".!?")

    if "cannot answer" in text or "i cannot" in text or "i'm unable" in text:
        return _REFUSAL

    tokens = _TOKEN_RE.findall(text)
    # Hedges are discarded, not ranked. Keeping them would let a filler word
    # become the decision, which is how a genuinely unstable system reads as
    # stable. An empty result after stripping means the response was pure
    # hedge, which is itself not a decision.
    tokens = [t for t in tokens if t not in HEDGE_TOKENS]
    if not tokens:
        return _UNKNOWN
    if len(tokens) == 1:
        return tokens[0]
    # A decision with a qualifier is still a decision; take the content token.
    return tokens[-1]


# --- Noise profiles. Three kinds, and the third is the adversarial case. ---


def _stable(profile: str, rng: random.Random) -> str:
    prefix = "the answer is" if profile == "low_noise" else "the answer appears to be"
    hedge = "" if profile == "low_noise" else rng.choice(FILLER_SUFFIXES[1:])
    return f"{prefix} {rng.choice(ANSWER_TOKENS)}{hedge}"


def _very_noisy(rng: random.Random) -> str:
    lead = rng.choice(["result:", "output:", ""])
    tail = rng.choice(["", " (likely)", " -- see notes", " ???"])
    return f"{lead} {rng.choice(ANSWER_TOKENS)}{tail}"


#: The profile that defeats the method. The system is genuinely uncertain: the
#: answer itself varies from sample to sample even though nothing has changed
#: functionally. A surface-level extractor cannot see through this, and the
#: experiment is built to show that rather than to hide it.
PROFILES: dict[str, Callable[[random.Random], str]] = {
    "low_noise": lambda rng: _stable("low_noise", rng),
    "high_noise": lambda rng: _stable("high_noise", rng),
    "very_high_noise": _very_noisy,
    "uncertainty_noise": lambda rng: (
        f"the answer appears to be {rng.choice(ANSWER_TOKENS)}"
        f"{rng.choice(['', ' or maybe otherwise'])}"
    ),
}


def sample(profile: str, rng: random.Random) -> str:
    return PROFILES[profile](rng)


# --- Decision-level comparison. ---


def total_variation(p: dict[str, float], q: dict[str, float]) -> float:
    """Total variation distance between two categorical distributions.

    Chosen over KL because it is symmetric, bounded in [0, 1], and does not
    blow up on a decision seen at evaluation time that was absent from the
    baseline. That last property matters operationally: a system that starts
    emitting a new decision must register as changed, not as a numeric error.
    """
    keys = set(p) | set(q)
    if not keys:
        return 0.0
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def _as_distribution(decisions: Sequence[str]) -> dict[str, float]:
    if not decisions:
        return {}
    counts = Counter(decisions)
    n = len(decisions)
    return {k: v / n for k, v in counts.items()}


def probe_distance(
    baseline_samples: Sequence[str],
    current_samples: Sequence[str],
) -> float:
    """One probe's decision: how far the decision distribution moved.

    Aggregating per-probe distributions rather than comparing individual
    responses is what lets a small probe count carry a usable confidence. A
    pairwise comparison throws away the sample size; a distributional one
    keeps it.
    """
    return total_variation(
        _as_distribution(baseline_samples), _as_distribution(current_samples)
    )


def aggregate(distances: Sequence[float], method: str = "mean") -> float:
    """Combine per-probe distances into one signal.

    ``mean`` is the default because it degrades gracefully when only some
    probes move, which is the common case for a partial capability change.
    ``max`` is offered for the opposite case -- a single capability failing
    completely -- and is more sensitive but far noisier at small N.
    """
    if not distances:
        return 0.0
    if method == "max":
        return max(distances)
    return sum(distances) / len(distances)


def probes_for_confidence(
    per_probe_alpha: float, target_confidence: float = 0.95, cap: int = 64
) -> int:
    """Smallest N so N probes clear the threshold at the target confidence.

    Under independence this is a lower bound. Probes drawn from one deployed
    system are not independent -- they share a model, a prompt, and a provider
    -- so the true N is higher. The function returns the optimistic figure and
    says so, because under-reporting the budget is the failure this whole
    track exists to prevent.
    """
    if per_probe_alpha >= 1.0:
        return cap
    if per_probe_alpha <= 0.0:
        return 1
    needed = math.log(1.0 - target_confidence) / math.log(1.0 - per_probe_alpha)
    return max(1, min(cap, math.ceil(needed)))


# --- The experiment. ---


@dataclass
class DecisionScore:
    metric: str
    profile: str
    false_alarm_rate: float
    detection_power: float
    probes_needed: int
    distances_noise: list[float] = field(default_factory=list)
    distances_signal: list[float] = field(default_factory=list)
    n_probes_per_measurement: int = 0
    aggregate_method: str = "mean"

    @property
    def d_prime(self) -> float:
        if not self.distances_noise or not self.distances_signal:
            return 0.0
        mu_n = sum(self.distances_noise) / len(self.distances_noise)
        mu_s = sum(self.distances_signal) / len(self.distances_signal)
        var_n = sum((d - mu_n) ** 2 for d in self.distances_noise) / max(
            1, len(self.distances_noise) - 1
        )
        var_s = sum((d - mu_s) ** 2 for d in self.distances_signal) / max(
            1, len(self.distances_signal) - 1
        )
        denom = math.sqrt((var_n + var_s) / 2.0)
        if denom == 0:
            return float("inf") if mu_n != mu_s else 0.0
        return (mu_s - mu_n) / denom

    @property
    def is_usable(self) -> bool:
        return self.false_alarm_rate <= 0.10 and self.detection_power >= 0.90

    def summary(self) -> str:
        return (
            f"{self.profile:<20} FA={self.false_alarm_rate:.3f} "
            f"power={self.detection_power:.3f} d'={self.d_prime:6.2f} "
            f"N={self.probes_needed:<3} {'USABLE' if self.is_usable else 'unusable'}"
        )


def _threshold(distances: Sequence[float], target_fa: float) -> float:
    if not distances:
        return 1.0
    ordered = sorted(distances)
    idx = min(len(ordered) - 1, max(0, int(round((1.0 - target_fa) * (len(ordered) - 1)))))
    return ordered[idx]


#: Functional drifts, applied to the *decision* rather than to the surface
#: form. This is the honest comparison: the earlier phase rewrote wording and
#: called it a semantic change, which flattered the text metrics.
FUNCTIONAL_DRIFTS = (
    "wrong_value",
    "collapse_to_constant",
    "refusal",
)

NON_FUNCTIONAL_DRIFTS = ("paraphrase", "reword_hedge")


def apply_drift(decision: str, drift: str) -> str:
    """Change the decision, or only the surface form. Returns the new decision.

    ``paraphrase`` returns the SAME decision. A metric that separates signal
    from noise must score it as zero; one that does not is measuring wording.
    """
    if drift == "paraphrase":
        return decision
    if drift == "reword_hedge":
        return decision
    if drift == "wrong_value":
        return "OMEGA" if decision != "OMEGA" else "SIGMA"
    if drift == "collapse_to_constant":
        return "OMEGA"
    if drift == "refusal":
        return _REFUSAL
    raise ValueError(f"unknown drift {drift!r}")


def evaluate_decision_metric(
    profile: str,
    n_measurements: int = 200,
    n_signal: int = 150,
    samples_per_probe: int = 8,
    n_probes: int = 5,
    seed: int = 4242,
    target_fa: float = 0.05,
    aggregate_method: str = "mean",
) -> DecisionScore:
    """Measure the decision-level metric against noise and against drift.

    ``samples_per_probe`` is how many responses one probe draws. ``n_probes``
    is how many probes contribute to one aggregate measurement. Both are
    reported, because "N probes" is meaningless without them and conflating
    them is how a probe budget gets overstated.
    """
    noise_rng = random.Random(seed)
    signal_rng = random.Random(seed + 7717)

    noise_distances: list[float] = []
    for _ in range(n_measurements):
        per_probe = []
        for _ in range(n_probes):
            baseline = [sample(profile, noise_rng) for _ in range(samples_per_probe)]
            current = [sample(profile, noise_rng) for _ in range(samples_per_probe)]
            per_probe.append(
                probe_distance(
                    [extract_decision(r) for r in baseline],
                    [extract_decision(r) for r in current],
                )
            )
        noise_distances.append(aggregate(per_probe, aggregate_method))

    drift = FUNCTIONAL_DRIFTS[signal_rng.randrange(len(FUNCTIONAL_DRIFTS))]
    signal_distances: list[float] = []
    for _ in range(n_signal):
        per_probe = []
        for _ in range(n_probes):
            baseline = [
                extract_decision(sample(profile, signal_rng))
                for _ in range(samples_per_probe)
            ]
            current = [
                apply_drift(d, drift) for d in baseline
            ]
            per_probe.append(probe_distance(baseline, current))
        signal_distances.append(aggregate(per_probe, aggregate_method))

    threshold = _threshold(noise_distances, target_fa)
    fa = sum(1 for d in noise_distances if d >= threshold) / max(1, len(noise_distances))
    power = sum(1 for d in signal_distances if d >= threshold) / max(
        1, len(signal_distances)
    )

    return DecisionScore(
        metric="decision_level_tv",
        profile=profile,
        false_alarm_rate=fa,
        detection_power=power,
        probes_needed=probes_for_confidence(fa if fa > 0 else 1e-6),
        distances_noise=noise_distances,
        distances_signal=signal_distances,
        n_probes_per_measurement=n_probes,
        aggregate_method=aggregate_method,
    )


def run_all_profiles(
    profiles: Sequence[str] | None = None, **kwargs
) -> dict[str, DecisionScore]:
    names = list(profiles or PROFILES.keys())
    return {p: evaluate_decision_metric(p, **kwargs) for p in names}
