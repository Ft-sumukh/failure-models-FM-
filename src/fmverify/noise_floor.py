"""Track B: the noise floor.

The open problem in application/README.md, stated as code. A witness set is
only useful if real functional change is distinguishable from sampling noise.
Every candidate metric is measured against both, because the naive candidates
are documented not to work:

    exact hash      flags sampling noise, misses paraphrase
    embedding       flags surface change, under-flags semantic change
    llm judge       gameable -- the verifier-deployment gap
    coverage        near ceiling, barely discriminates
    mutation        context-dependent, fails in exactly our setting

Deterministic and offline by design. If the instrument needed a live model to
measure its own noise floor, it would be measuring the model instead. The
stochastic target below stands in for a live system; the *method* is what
transfers, and ``protocol.py`` is where a real provider adapter would attach.

What is measured, and why it is the right measurement:

  false_alarm_rate  -- P(flag change | nothing changed). A metric that alarms
                       on pure noise is useless no matter how well it detects
                       real change.
  detection_power   -- P(flag change | real functional change).
  d_prime           -- separation between the two distributions, comparable
                       across metrics.
  probes_needed     -- the N-vs-detection-power curve. The product's core
                       claim is that small N suffices; this measures whether
                       that is true.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

# --- Metrics. Each maps (response_a, response_b) -> distance in [0, 1]. ---


def exact_hash_metric(response_a: str, response_b: str) -> float:
    """0 if identical, 1 if different. The naive default.

    Included to be measured, not because it works. On a stochastic target it
    alarms on every resample.
    """
    return 0.0 if response_a == response_b else 1.0


def token_jaccard_metric(response_a: str, response_b: str) -> float:
    """1 - Jaccard over whitespace tokens. Stands in for embedding distance.

    Cheaper than an embedding model and behaves the same way for the purposes
    that matter here: sensitive to surface form, comparatively insensitive to
    meaning. A real deployment swaps in a sentence embedding here.
    """
    ta = set(response_a.lower().split())
    tb = set(response_b.lower().split())
    if not ta and not tb:
        return 0.0
    union = ta | tb
    if not union:
        return 0.0
    return 1.0 - (len(ta & tb) / len(union))


def normalized_edit_metric(response_a: str, response_b: str) -> float:
    """Levenshtein distance normalized by length. A structural metric."""
    if response_a == response_b:
        return 0.0
    la, lb = len(response_a), len(response_b)
    if la == 0 or lb == 0:
        return 1.0
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if response_a[i - 1] == response_b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb] / max(la, lb)


METRICS: dict[str, Callable[[str, str], float]] = {
    "exact_hash": exact_hash_metric,
    "token_jaccard": token_jaccard_metric,
    "edit_distance": normalized_edit_metric,
}


# --- A stochastic target, deterministic given a seed. ---

#: Every profile must emit the same answer vocabulary. ``apply_functional_drift``
#: rewrites those tokens, and a profile that omits them produces drift pairs
#: where nothing changed -- which silently inflates the noise distribution and
#: makes every metric look broken. That bug shipped once; the test suite now
#: pins this invariant.
ANSWER_TOKENS = ("alpha", "beta", "gamma", "delta")

NOISE_PROFILES: dict[str, Callable[[random.Random], str]] = {
    "low_noise": lambda rng: f"the answer is {rng.choice(ANSWER_TOKENS)}",
    "high_noise": lambda rng: (
        f"the answer appears to be {rng.choice(ANSWER_TOKENS)}"
        f"{rng.choice(['', ' roughly', ' approximately', ' i think'])}"
    ),
    "very_high_noise": lambda rng: (
        f"{rng.choice(['result:', 'output:', ''])}"
        f"{rng.choice(ANSWER_TOKENS)}"
        f"{rng.choice(['', ' (likely)', ' -- see notes', ' ???'])}"
    ),
}


def sample_response(profile: str, rng: random.Random) -> str:
    return NOISE_PROFILES[profile](rng)


def apply_functional_drift(
    response: str, drift: str, rng: random.Random
) -> tuple[str, bool]:
    """Apply a real functional change to a response.

    The drift must be semantic, not cosmetic. ``paraphrase`` changes the
    surface form only -- a metric that fires on it is over-sensitive, and
    counting that as detection is how naive metrics flatter themselves.

    ``drift_applied`` is returned so a caller can assert the drift actually
    changed something. A "drift" that rewrites nothing is not a drift, and
    silently treating it as one is what made the first run of this experiment
    report power=0.0 for every metric.
    """
    if drift == "none":
        return response, False

    if drift == "paraphrase":
        if "the answer is" in response:
            return response.replace("the answer is", "the result appears to be"), True
        return (
            response.replace("the answer appears to be", "it looks like"),
            response != "it looks like",
        ) or (response, True)

    if drift == "wrong_value":
        for token in ANSWER_TOKENS:
            if token in response:
                return response.replace(token, "OMEGA"), True
        return response + " OMEGA", True

    if drift == "reversed":
        present = [t for t in ANSWER_TOKENS if t in response]
        if len(present) >= 2:
            swapped = response.replace(present[0], "\x00").replace(present[1], present[0])
            return swapped.replace("\x00", present[1]), True
        if present:
            return response.replace(present[0], "epsilon"), True
        return response + " epsilon", True

    if drift == "refusal":
        return "I cannot answer that.", True

    raise ValueError(f"unknown drift type {drift!r}")


#: The drift taxonomy used in the experiment. ``none`` and ``paraphrase`` are
#: negatives -- the latter is the discriminating case, because a metric that
#: detects it is measuring wording, not function.
FUNCTIONAL_DRIFTS = ("wrong_value", "reversed", "refusal")
NON_FUNCTIONAL_DRIFTS = ("none", "paraphrase")


@dataclass
class MetricScore:
    metric: str
    false_alarm_rate: float
    detection_power: float
    probes_needed: int
    distances_noise: list[float] = field(default_factory=list)
    distances_signal: list[float] = field(default_factory=list)

    @property
    def d_prime(self) -> float:
        """Separability. Higher is better; 0 means indistinguishable."""
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
            # Zero variance in both: separated only if the means differ.
            return float("inf") if mu_n != mu_s else 0.0
        return (mu_s - mu_n) / denom

    @property
    def is_usable(self) -> bool:
        """A metric is usable only if it is quiet on noise AND loud on signal."""
        return self.false_alarm_rate <= 0.10 and self.detection_power >= 0.90

    def summary(self) -> str:
        return (
            f"{self.metric:<14} false_alarm={self.false_alarm_rate:.3f} "
            f"power={self.detection_power:.3f} d'={self.d_prime:.2f} "
            f"probes={self.probes_needed:<4} "
            f"{'USABLE' if self.is_usable else 'unusable'}"
        )


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[idx]


def calibrate_threshold(
    distances: Sequence[float], target_false_alarm_rate: float = 0.05
) -> float:
    """Pick a threshold from the NOISE distribution only.

    Calibrating on signal as well would bake the answer into the metric. The
    threshold comes from one distribution and is then applied to both.
    """
    if not distances:
        return 1.0
    return _percentile(distances, 1.0 - target_false_alarm_rate)


def probes_for_confidence(
    distances: Sequence[float],
    threshold: float,
    per_probe_alpha: float,
    target_confidence: float = 0.95,
    cap: int = 64,
) -> int:
    """Smallest N such that N independent probes clear the threshold with
    ``target_confidence`` overall, assuming independence.

    Independence is an assumption, not a fact, so this is a lower bound on N.
    Correlated probes need more, and under-reporting here would make the
    probe budget look better than it is.
    """
    if per_probe_alpha >= 1.0:
        return cap
    if per_probe_alpha <= 0.0:
        return 1
    # N such that (1 - alpha)^N <= 1 - confidence, i.e.
    #   N >= log(1 - confidence) / log(1 - alpha)
    # Both logarithms are negative, so the quotient is positive. Do not drop
    # the sign on either side: taking absolute values inverts the ordering and
    # makes a noisier system look like it needs FEWER probes.
    needed = math.log(1.0 - target_confidence) / math.log(1.0 - per_probe_alpha)
    return max(1, min(cap, math.ceil(needed)))


def evaluate_metric(
    metric_name: str,
    profile: str,
    n_noise: int = 400,
    n_signal: int = 200,
    n_probes_per_pair: int = 3,
    seed: int = 1234,
    target_false_alarm_rate: float = 0.05,
) -> MetricScore:
    """Measure one metric against noise and against real functional change.

    The experiment from application/README.md Phase 1, in code. Deterministic
    given ``seed``.

    ``n_probes_per_pair`` is how many samples are drawn to form one distance.
    It must be at least 2: with 1 there is no pair, no noise distribution, and
    the threshold silently degenerates to 1.0 -- which made every metric look
    like it had zero variance on the first run. The guard below turns that
    silent failure into an error.
    """
    if n_probes_per_pair < 2:
        raise ValueError(
            "n_probes_per_pair must be >= 2; with 1 sample there is no pair "
            "to measure and the threshold degenerates"
        )
    metric = METRICS[metric_name]
    # Separate streams: the drift choice must not perturb the noise
    # distribution, or the false-alarm rate stops being comparable.
    noise_rng = random.Random(seed)
    signal_rng = random.Random(seed + 9973)

    noise_distances: list[float] = []
    for _ in range(n_noise):
        probes = [sample_response(profile, noise_rng) for _ in range(n_probes_per_pair)]
        baseline = probes[0]
        for p in probes[1:]:
            noise_distances.append(metric(baseline, p))

    signal_distances: list[float] = []
    drift = FUNCTIONAL_DRIFTS[signal_rng.randrange(len(FUNCTIONAL_DRIFTS))]
    no_op = 0
    for _ in range(n_signal):
        base = sample_response(profile, signal_rng)
        drifted, applied = apply_functional_drift(base, drift, signal_rng)
        if not applied:
            no_op += 1
            continue
        signal_distances.append(metric(base, drifted))

    if no_op:
        # A drift that changes nothing is not a drift. Silently counting those
        # as signal pairs is what produced power=0.0 on the first run.
        raise AssertionError(
            f"drift {drift!r} was a no-op for {no_op}/{n_signal} signal pairs; "
            "the drift taxonomy and the noise profiles disagree"
        )
    if not signal_distances:
        raise AssertionError("no signal pairs were generated")

    threshold = calibrate_threshold(noise_distances, target_false_alarm_rate)

    false_alarms = sum(1 for d in noise_distances if d >= threshold) / max(
        1, len(noise_distances)
    )
    detections = sum(1 for d in signal_distances if d >= threshold) / max(
        1, len(signal_distances)
    )

    per_probe_alpha = false_alarms if false_alarms > 0 else 1e-6
    probes_needed = probes_for_confidence(
        noise_distances, threshold, per_probe_alpha
    )

    return MetricScore(
        metric=metric_name,
        false_alarm_rate=false_alarms,
        detection_power=detections,
        probes_needed=probes_needed,
        distances_noise=noise_distances,
        distances_signal=signal_distances,
    )


def run_experiment(
    profile: str = "low_noise",
    metrics: Sequence[str] | None = None,
    n_noise: int = 400,
    n_signal: int = 200,
    seed: int = 1234,
) -> dict[str, MetricScore]:
    """Run every metric against one noise profile."""
    names = list(metrics or METRICS.keys())
    return {
        name: evaluate_metric(
            name, profile, n_noise=n_noise, n_signal=n_signal, seed=seed
        )
        for name in names
    }
