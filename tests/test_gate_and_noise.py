"""Tests for the acceptance gate and the noise-floor instrument.

The gate tests matter more than the FM tests: a gate that admits bad records
is the exact failure this project exists to avoid, and it fails silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fmverify.fms import TRAINING_FMS, assert_split  # noqa: E402
from fmverify.noise_floor import (  # noqa: E402
    ANSWER_TOKENS,
    FUNCTIONAL_DRIFTS,
    METRICS,
    NON_FUNCTIONAL_DRIFTS,
    apply_functional_drift,
    calibrate_threshold,
    evaluate_metric,
    exact_hash_metric,
    probes_for_confidence,
    run_experiment,
    sample_response,
)
from fmverify.records import (  # noqa: E402
    AcceptanceGate,
    FailureRecord,
    build_record,
    count_distinct_failures,
    filter_new_failures,
)
from fmverify.targets import CLEAN_TARGET, FLAWED_TARGET  # noqa: E402
from fmverify.tasks import _REFERENCE_IMPLS, get_task  # noqa: E402
from fmverify.verifier import NO_DEFECT, verify_candidate  # noqa: E402

import random  # noqa: E402


def _record_for(family: str, code: str, fm, input_value, category: str, split="train"):
    contract, _oracle, _inputs = get_task(family)
    impl = _REFERENCE_IMPLS[family]
    verdict = verify_candidate(code, impl, input_value, impl(input_value))
    from fmverify.fms import Attack

    attack = Attack(
        fm_id=fm.fm_id,
        fm_version=fm.fm_version,
        strategy="test",
        family=family,
        input_value=input_value,
        seed=0,
    )
    return build_record(contract, attack, "target-test@1.0.0", verdict, category), impl


class TestRecordShape:
    def test_verified_failure_is_eligible(self):
        rec, _ = _record_for(
            "list-dedupe",
            FLAWED_TARGET.code_by_family["list-dedupe"],
            TRAINING_FMS[0],
            [],
            "boundary_condition",
        )
        assert rec.status == "verified_failure"
        assert rec.dataset["eligible_for_training"] is True
        assert rec.validate_shape() == []

    def test_non_failure_carries_exclusion_reason(self):
        rec, _ = _record_for(
            "list-dedupe",
            CLEAN_TARGET.code_by_family["list-dedupe"],
            TRAINING_FMS[0],
            [1, 2, 3],
            "boundary_condition",
        )
        assert rec.status == "non_failure"
        assert rec.dataset["eligible_for_training"] is False
        assert rec.dataset["exclusion_reason"]

    def test_illegal_category_is_caught(self):
        rec, _ = _record_for(
            "list-dedupe",
            FLAWED_TARGET.code_by_family["list-dedupe"],
            TRAINING_FMS[0],
            [],
            "not_a_real_category",
        )
        problems = rec.validate_shape()
        assert any("primary_category" in p for p in problems)

    def test_eligible_without_verified_repair_is_caught(self):
        rec, _ = _record_for(
            "list-dedupe",
            FLAWED_TARGET.code_by_family["list-dedupe"],
            TRAINING_FMS[0],
            [],
            "boundary_condition",
        )
        rec.analysis["repair_ref"] = "some/patch.py"
        rec.analysis["repair_verified"] = False
        problems = rec.validate_shape()
        assert any("repair" in p for p in problems)


class TestYieldAccounting:
    """Raw record count must never be reported as discovery yield.

    A candidate with one defect fails identically on every probe for its
    family, so raw record count scales with probe budget while distinct
    failure count does not. A run that reported 24 records where 2 distinct
    bugs existed would have overstated the corpus 12x, and a fine-tuning run
    on those records would have taught one bug 12 times.
    """

    def _make(self, family, error_type, category, n):
        from fmverify.fms import Attack
        from fmverify.tasks import get_task
        from fmverify.verifier import Verdict

        contract, _o, _i = get_task(family)
        out = []
        for i in range(n):
            attack = Attack(
                fm_id="boundary-fm", fm_version="0.3.1", strategy="t",
                family=family, input_value=[], seed=i,
            )
            verdict = Verdict(
                verdict="confirmed_mismatch",
                reason=f"candidate raised {error_type}",
                error_type=error_type,
            )
            out.append(
                build_record(contract, attack, "t@1", verdict, category)
            )
        return out

    def test_repeated_hits_of_one_bug_collapse(self):
        recs = self._make("clamp-list", "TypeError", "input_shape_assumption", 12)
        assert len(recs) == 12
        assert count_distinct_failures(recs) == 1
        assert len(filter_new_failures(recs)) == 1

    def test_different_families_are_different_failures(self):
        a = self._make("clamp-list", "TypeError", "input_shape_assumption", 6)
        b = self._make("chunk-sum", "TypeError", "input_shape_assumption", 6)
        assert count_distinct_failures(a + b) == 2

    def test_different_error_types_are_different_failures(self):
        a = self._make("clamp-list", "TypeError", "input_shape_assumption", 4)
        b = self._make("clamp-list", "IndexError", "boundary_condition", 4)
        assert count_distinct_failures(a + b) == 2

    def test_different_fms_finding_the_same_bug_do_not_double_count(self):
        """The FM is the finder, not the finding.

        Two FMs noticing the same defect is one defect.
        """
        from fmverify.fms import Attack
        from fmverify.tasks import get_task
        from fmverify.verifier import Verdict

        contract, _o, _i = get_task("clamp-list")
        recs = []
        for fm_id in ("boundary-fm", "adversarial-input-fm"):
            attack = Attack(
                fm_id=fm_id, fm_version="1", strategy="t",
                family="clamp-list", input_value=[], seed=0,
            )
            verdict = Verdict(
                verdict="confirmed_mismatch", reason="raised TypeError",
                error_type="TypeError",
            )
            recs.append(
                build_record(contract, attack, "t@1", verdict,
                             "input_shape_assumption")
            )
        assert count_distinct_failures(recs) == 1

    def test_filter_respects_a_preset_seen_set(self):
        recs = self._make("clamp-list", "TypeError", "input_shape_assumption", 4)
        cluster = recs[0].dataset["dedup_cluster"]
        fresh = filter_new_failures(recs, seen={cluster})
        assert fresh == []

    def test_filter_preserves_source_order(self):
        recs = (
            self._make("clamp-list", "TypeError", "input_shape_assumption", 3)
            + self._make("chunk-sum", "IndexError", "boundary_condition", 3)
        )
        fresh = filter_new_failures(recs)
        assert len(fresh) == 2
        assert fresh[0] is recs[0]
        assert fresh[1] is recs[3]


class TestAcceptanceGate:
    def test_admits_a_clean_verified_failure(self):
        rec, _ = _record_for(
            "list-dedupe",
            FLAWED_TARGET.code_by_family["list-dedupe"],
            TRAINING_FMS[0],
            [],
            "boundary_condition",
        )
        admit, reason = AcceptanceGate(split="train").evaluate(rec, _REFERENCE_IMPLS["list-dedupe"])
        assert admit, reason

    def test_rejects_non_failure(self):
        rec, _ = _record_for(
            "list-dedupe",
            CLEAN_TARGET.code_by_family["list-dedupe"],
            TRAINING_FMS[0],
            [1, 2, 3],
            "boundary_condition",
        )
        admit, reason = AcceptanceGate(split="train").evaluate(rec, _REFERENCE_IMPLS["list-dedupe"])
        assert not admit
        assert "non_failure" in reason

    def test_rejects_heldout_fm_in_training_split(self):
        from fmverify.fms import HELDOUT_FMS

        rec, _ = _record_for(
            "list-dedupe",
            FLAWED_TARGET.code_by_family["list-dedupe"],
            HELDOUT_FMS[0],
            [],
            "boundary_condition",
        )
        rec.dataset["eligible_for_training"] = True
        admit, reason = AcceptanceGate(split="train").evaluate(rec, _REFERENCE_IMPLS["list-dedupe"])
        assert not admit
        assert "split violation" in reason

    def test_falsifier_rejects_a_failure_that_does_not_reproduce(self):
        """The check that distinguishes a validated claim from a gameable score."""
        rec, impl = _record_for(
            "list-dedupe",
            FLAWED_TARGET.code_by_family["list-dedupe"],
            TRAINING_FMS[0],
            [],
            "boundary_condition",
        )
        gate = AcceptanceGate(split="train")
        # The real buggy code reproduces.
        ok, reason = gate.falsify(rec, FLAWED_TARGET.code_by_family["list-dedupe"], impl, [])
        assert ok, reason
        # Correct code does not reproduce the claimed failure.
        bad, reason2 = gate.falsify(rec, CLEAN_TARGET.code_by_family["list-dedupe"], impl, [])
        assert not bad
        assert "did not reproduce" in reason2

    def test_falsifier_rejects_a_failure_only_reproducing_under_its_own_input(self):
        rec, impl = _record_for(
            "list-dedupe",
            FLAWED_TARGET.code_by_family["list-dedupe"],
            TRAINING_FMS[0],
            [],
            "boundary_condition",
        )
        gate = AcceptanceGate(split="train")
        # Same buggy code, but the claimed input was never the trigger.
        ok, reason = gate.falsify(rec, FLAWED_TARGET.code_by_family["list-dedupe"], impl, [1, 2, 3, 1])
        assert not ok, "boundary bug should not fire on a non-empty input"


class TestNoiseFloorInstrument:
    def test_all_profiles_emit_the_shared_answer_vocabulary(self):
        """Regression test for the bug that made the first run report power=0."""
        rng = random.Random(0)
        for profile in ("low_noise", "high_noise", "very_high_noise"):
            for _ in range(200):
                resp = sample_response(profile, rng)
                assert any(t in resp for t in ANSWER_TOKENS), f"{profile}: {resp!r}"

    def test_every_functional_drift_actually_changes_the_response(self):
        rng = random.Random(0)
        for profile in ("low_noise", "high_noise", "very_high_noise"):
            base = sample_response(profile, rng)
            for drift in FUNCTIONAL_DRIFTS:
                drifted, applied = apply_functional_drift(base, drift, rng)
                assert applied, f"{drift!r} was a no-op on {base!r}"
                assert drifted != base, f"{drift!r} returned the input unchanged"

    def test_paraphrase_is_not_a_functional_drift(self):
        """Paraphrase changes wording only. Treating it as signal would be
        how a naive metric flatters itself."""
        assert "paraphrase" in NON_FUNCTIONAL_DRIFTS
        assert "paraphrase" not in FUNCTIONAL_DRIFTS

    def test_single_sample_per_pair_is_rejected(self):
        with pytest.raises(ValueError):
            evaluate_metric("exact_hash", "low_noise", n_probes_per_pair=1)

    def test_exact_hash_fires_on_pure_noise(self):
        """The documented failure mode, pinned as a test.

        Any change in sampled output is 'change' to a hash diff, so a hash diff
        alarms on almost every resample of a stochastic system.
        """
        score = evaluate_metric("exact_hash", "high_noise", seed=5)
        assert score.false_alarm_rate > 0.5, (
            "exact-hash should false-alarm heavily on a noisy system; "
            f"got {score.false_alarm_rate}"
        )

    def test_threshold_is_calibrated_on_noise_only(self):
        noise = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        t = calibrate_threshold(noise, target_false_alarm_rate=0.05)
        assert t > 0.5, "threshold should sit above the bulk of the noise"

    def test_probes_needed_shrinks_as_per_probe_alpha_rises(self):
        """Direction check on the confidence arithmetic.

        More probes are needed when each probe is LESS likely to trip. This
        assertion was initially written backwards -- asserting that a noisier
        system needs more probes -- which is false: a noisier system trips more
        easily, so fewer probes are needed to confirm a real change. The
        function was right and the test was wrong.
        """
        quiet = probes_for_confidence([], 0.5, per_probe_alpha=0.001)
        noisy = probes_for_confidence([], 0.5, per_probe_alpha=0.40)
        assert noisy < quiet

    def test_probes_needed_is_capped(self):
        """The cap is reported, not silently exceeded.

        A cap that hides the true requirement would make the probe budget look
        better than it is, which is the failure mode this whole track is
        designed to avoid.
        """
        n = probes_for_confidence([], 0.5, per_probe_alpha=1e-9, cap=32)
        assert n == 32

    def test_experiment_is_deterministic(self):
        a = evaluate_metric("token_jaccard", "high_noise", seed=42)
        b = evaluate_metric("token_jaccard", "high_noise", seed=42)
        assert a.distances_noise == b.distances_noise
        assert a.distances_signal == b.distances_signal

    def test_metrics_are_measured_not_asserted_usable(self):
        """The instrument reports failure honestly.

        This test does NOT require a metric to pass. It requires the harness
        to produce a real measurement. If a future metric works, it will show
        up as usable without any change here.
        """
        results = run_experiment("high_noise", n_noise=200, n_signal=150, seed=1234)
        assert set(results) == set(METRICS)
        for score in results.values():
            assert 0.0 <= score.false_alarm_rate <= 1.0
            assert 0.0 <= score.detection_power <= 1.0
            assert score.probes_needed >= 1

    def test_content_metrics_cannot_reach_power_under_high_noise(self):
        """A property-based metric cannot separate these two distributions.

        Pinned because it is the finding: token and edit metrics have NEGATIVE
        d-prime under high noise, meaning real functional change is no more
        detectable than a resample. If this ever becomes false, the method
        changed and the finding needs revisiting.
        """
        for name in ("token_jaccard", "edit_distance"):
            score = evaluate_metric(name, "high_noise", seed=1234)
            assert score.detection_power < 0.5, (
                f"{name} unexpectedly reached power={score.detection_power} "
                "under high noise; the negative-d-prime finding needs revisiting"
            )
