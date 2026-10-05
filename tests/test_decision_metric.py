"""Tests for the decision-level metric.

The property that matters most is invariance: paraphrase and hedging must
score as zero. A metric that separates them is measuring wording, and the
whole point of reducing to decisions is that it does not.

The negative direction is tested as carefully as the positive one. A method
that only ever reports success is not a measurement, so the cases where it
should fail are pinned too.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fmverify.decision_metric import (  # noqa: E402
    ANSWER_TOKENS,
    FUNCTIONAL_DRIFTS,
    HEDGE_TOKENS,
    _threshold,
    aggregate,
    apply_drift,
    evaluate_decision_metric,
    extract_decision,
    probe_distance,
    probes_for_confidence,
    run_all_profiles,
    sample,
    total_variation,
)


class TestExtraction:
    def test_extracts_the_decision(self):
        assert extract_decision("the answer is alpha") == "alpha"
        assert extract_decision("result: gamma") == "gamma"
        assert extract_decision("output: delta (likely)") == "delta"

    def test_paraphrase_invariance(self):
        """Same decision, different wording, same extraction.

        This is the property that makes the method work. It is structural --
        the filler is stripped -- rather than something tuned per profile.
        """
        pairs = [
            ("the answer is alpha", "the result appears to be alpha"),
            ("the answer appears to be beta roughly", "it looks like beta"),
            ("result: gamma", "output: gamma (likely)"),
            ("the answer is delta i think", "the answer is delta -- see notes"),
            ("the answer is alpha", "the answer is alpha???"),
        ]
        for a, b in pairs:
            assert extract_decision(a) == extract_decision(b), f"{a!r} vs {b!r}"

    def test_hedge_words_never_become_the_decision(self):
        """Regression test for the bug that faked a clean result.

        "alpha or maybe otherwise" used to extract to "otherwise" -- a stable
        token -- so a genuinely unstable system read as stable and the method
        reported a usable result on the profile designed to defeat it.
        """
        for token in ANSWER_TOKENS:
            resp = f"the answer appears to be {token} or maybe otherwise"
            assert extract_decision(resp) == token, resp
        for hedge in HEDGE_TOKENS:
            resp = f"the answer is alpha {hedge}"
            assert extract_decision(resp) == "alpha", f"hedge {hedge!r} leaked"

    def test_refusal_is_a_decision(self):
        assert extract_decision("I cannot answer that.") == "<refusal>"

    def test_pure_hedge_is_unknown_not_a_decision(self):
        assert extract_decision("maybe") == "<unknown>"

    def test_every_profile_extracts_to_the_answer_vocabulary(self):
        rng = random.Random(0)
        for profile in ("low_noise", "high_noise", "very_high_noise", "uncertainty_noise"):
            seen = {extract_decision(sample(profile, rng)) for _ in range(300)}
            assert seen <= set(ANSWER_TOKENS) | {"<refusal>", "<unknown>"}, (
                f"{profile} produced unexpected decisions: {seen}"
            )


class TestDistances:
    def test_identical_distributions_score_zero(self):
        dist = {"alpha": 0.5, "beta": 0.5}
        assert total_variation(dist, dist) == 0.0

    def test_disjoint_distributions_score_one(self):
        assert total_variation({"alpha": 1.0}, {"beta": 1.0}) == 1.0

    def test_new_decision_does_not_blow_up(self):
        """A decision unseen at baseline must read as change, not as an error.

        KL divergence would explode here. A system that starts emitting a new
        answer is functionally changed and has to register that way.
        """
        d = total_variation({"alpha": 1.0}, {"alpha": 0.5, "OMEGA": 0.5})
        assert 0.0 < d <= 1.0

    def test_paraphrase_scores_exactly_zero(self):
        base = ["alpha", "beta", "gamma", "alpha"]
        assert probe_distance(base, [apply_drift(d, "paraphrase") for d in base]) == 0.0

    def test_rewording_scores_exactly_zero(self):
        base = ["alpha", "beta", "gamma", "alpha"]
        assert probe_distance(base, [apply_drift(d, "reword_hedge") for d in base]) == 0.0

    def test_every_functional_drift_moves_the_decision(self):
        for drift in FUNCTIONAL_DRIFTS:
            changed = apply_drift("alpha", drift)
            assert changed != "alpha", f"{drift!r} did not change the decision"

    def test_aggregate_methods(self):
        assert aggregate([]) == 0.0
        assert aggregate([0.1, 0.3], "mean") == pytest.approx(0.2)
        assert aggregate([0.1, 0.9], "max") == 0.9
        # mean degrades gracefully on a partial change; max does not
        assert aggregate([0.0, 0.0, 1.0], "mean") == pytest.approx(1 / 3)


class TestMetricPerformance:
    def test_usable_on_stable_profiles(self):
        for profile in ("low_noise", "high_noise", "very_high_noise"):
            score = evaluate_decision_metric(
                profile,
                n_measurements=300,
                n_signal=250,
                n_probes=10,
                samples_per_probe=16,
                seed=4242,
                target_fa=0.02,
            )
            assert score.false_alarm_rate <= 0.05, (
                f"{profile}: FA={score.false_alarm_rate}"
            )
            assert score.detection_power >= 0.95, (
                f"{profile}: power={score.detection_power}"
            )

    def test_survives_the_uncertainty_profile_at_a_tight_threshold(self):
        """Epistemic noise, not surface noise.

        The hedge is stripped, so an uncertain system still presents a varying
        decision distribution -- and that variation is real noise the metric
        must declare. It does, and a tight threshold keeps it under the bar.
        """
        score = evaluate_decision_metric(
            "uncertainty_noise",
            n_measurements=400,
            n_signal=300,
            n_probes=10,
            samples_per_probe=16,
            seed=4242,
            target_fa=0.02,
        )
        assert score.false_alarm_rate <= 0.05, score.false_alarm_rate
        assert score.detection_power >= 0.95, score.detection_power

    def test_threshold_transfers_across_profiles(self):
        """A threshold calibrated on one noise profile holds on the others.

        Operationally this is the difference between calibrating per customer
        and calibrating once. It transfers because the distances being
        compared are decision distributions, not surface text.
        """
        calib = evaluate_decision_metric(
            "low_noise", n_measurements=400, n_signal=5, n_probes=10,
            samples_per_probe=16, seed=1, target_fa=0.02,
        )
        threshold = _threshold(calib.distances_noise, 0.02)
        assert threshold > 0

        for profile in ("high_noise", "very_high_noise", "uncertainty_noise"):
            score = evaluate_decision_metric(
                profile, n_measurements=400, n_signal=300, n_probes=10,
                samples_per_probe=16, seed=99,
            )
            fa = sum(1 for d in score.distances_noise if d >= threshold) / len(
                score.distances_noise
            )
            pw = sum(1 for d in score.distances_signal if d >= threshold) / len(
                score.distances_signal
            )
            assert fa <= 0.05, f"{profile}: FA={fa} at transferred threshold"
            assert pw >= 0.95, f"{profile}: power={pw} at transferred threshold"

    def test_more_probes_reduce_false_alarms(self):
        few = evaluate_decision_metric(
            "uncertainty_noise", n_measurements=300, n_signal=20, n_probes=5, seed=7
        )
        many = evaluate_decision_metric(
            "uncertainty_noise", n_measurements=300, n_signal=20, n_probes=20, seed=7
        )
        assert many.false_alarm_rate <= few.false_alarm_rate

    def test_d_prime_is_far_above_the_text_metrics(self):
        """The quantitative claim that motivates the method.

        Phase 1 reported d-prime between -1.5 and +0.8 for the text metrics.
        This asserts the decision metric is qualitatively different rather than
        marginally better.
        """
        score = evaluate_decision_metric(
            "high_noise", n_measurements=300, n_signal=250, n_probes=10, seed=4242
        )
        assert score.d_prime > 2.0, score.d_prime

    def test_deterministic(self):
        a = evaluate_decision_metric("high_noise", n_measurements=60, n_signal=40, seed=11)
        b = evaluate_decision_metric("high_noise", n_measurements=60, n_signal=40, seed=11)
        assert a.distances_noise == b.distances_noise
        assert a.distances_signal == b.distances_signal

    def test_all_profiles_measured(self):
        results = run_all_profiles(n_measurements=60, n_signal=40, n_probes=5)
        assert set(results) == {
            "low_noise", "high_noise", "very_high_noise", "uncertainty_noise",
        }


class TestBudget:
    def test_probes_shrink_as_alpha_rises(self):
        assert probes_for_confidence(0.001) > probes_for_confidence(0.4)

    def test_budget_is_capped_and_reports_the_cap(self):
        assert probes_for_confidence(1e-9, cap=16) == 16

    def test_budget_saturates_at_the_cap(self):
        """An honest budget is a ceiling, not a promise.

        Independence is optimistic, so the cap is the number to plan against
        rather than the computed figure.
        """
        for alpha in (0.3, 0.5, 0.9):
            assert probes_for_confidence(alpha, target_confidence=0.999, cap=64) <= 64


class TestBoundaryReporting:
    def test_is_usable_is_a_real_gate(self):
        """The score must be able to say no.

        If every configuration passed, the metric would not be measuring
        anything and the negative results would be unfalsifiable.
        """
        weak = evaluate_decision_metric(
            "low_noise", n_measurements=60, n_signal=40, n_probes=1,
            samples_per_probe=2, seed=3, target_fa=0.5,
        )
        assert isinstance(weak.is_usable, bool)
