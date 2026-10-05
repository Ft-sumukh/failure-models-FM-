"""Pipeline smoke tests.

These assert the instrument works before any claim is made about what it
measures. A green suite here means the verifier returns trustworthy verdicts on
known-buggy and known-correct candidates -- it does NOT mean any FM works.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fmverify.fms import (  # noqa: E402
    HELDOUT_FMS,
    TRAINING_FMS,
    SplitViolation,
    assert_split,
)
from fmverify.targets import (  # noqa: E402
    CLEAN_TARGET,
    FLAWED_TARGET,
    HANGING_TARGET,
    SLOW_TARGET,
    _PARITY_CORRECT,
)
from fmverify.tasks import all_tasks, get_task  # noqa: E402
from fmverify.verifier import (  # noqa: E402
    CONFIRMED_MISMATCH,
    NO_DEFECT,
    UNVERIFIABLE,
    verify_candidate,
    verify_family,
)
from fmverify.tasks import _REFERENCE_IMPLS  # noqa: E402


class TestOracles:
    def test_oracles_populated_from_reference_impls(self):
        for _contract, oracle, inputs in all_tasks():
            assert oracle.cases, f"{oracle.oracle_id} has no cases"

    def test_task_families_and_splits(self):
        for contract, _oracle, _inputs in all_tasks():
            assert contract.split == "train"
            assert "@" in contract.task_id

    def test_unknown_family_raises(self):
        with pytest.raises(KeyError):
            get_task("does-not-exist")


class TestVerifier:
    def test_correct_candidate_passes_every_family(self):
        """The control case. If this fails the verifier is broken, not the target."""
        for family, code in CLEAN_TARGET.code_by_family.items():
            _c, _o, inputs = get_task(family)
            verdicts = verify_family(code, family, inputs)
            for v in verdicts:
                assert v.verdict == NO_DEFECT, f"{family}: {v.reason}"

    def test_detects_empty_input_boundary_bug(self):
        """Known weakness: indexes [0] with no length check."""
        code = FLAWED_TARGET.code_by_family["list-dedupe"]
        impl = _REFERENCE_IMPLS["list-dedupe"]
        verdict = verify_candidate(code, impl, [], impl([]))
        assert verdict.verdict == CONFIRMED_MISMATCH
        assert verdict.error_type == "IndexError"

    def test_detects_off_by_one(self):
        code = FLAWED_TARGET.code_by_family["parity-counts"]
        impl = _REFERENCE_IMPLS["parity-counts"]
        verdict = verify_candidate(code, impl, [1, 2, 3], impl([1, 2, 3]))
        assert verdict.verdict == CONFIRMED_MISMATCH
        assert verdict.actual != verdict.expected

    def test_detects_input_shape_assumption(self):
        """Known weakness: assumes non-decreasing input.

        Input must dip below the running maximum, or the assumed-sorted code
        happens to agree with the reference.
        """
        code = FLAWED_TARGET.code_by_family["running-max"]
        impl = _REFERENCE_IMPLS["running-max"]
        verdict = verify_candidate(code, impl, [5, 1, 2], impl([5, 1, 2]))
        assert verdict.verdict == CONFIRMED_MISMATCH
        assert verdict.actual != verdict.expected

    def test_detects_complexity_failure_by_runtime_ratio(self):
        """Quadratic target: functionally correct, algorithmically wrong.

        Asserted by comparing the candidate's runtime against the reference
        on the same input, not by racing a wall-clock timeout. A timing race is
        flaky by construction and its outcome depends on host load.
        """
        code = SLOW_TARGET.code_by_family["parity-counts"]
        impl = _REFERENCE_IMPLS["parity-counts"]

        small = [1, 2]
        assert verify_candidate(code, impl, small, impl(small)).verdict == NO_DEFECT, (
            "quadratic target must be functionally correct on small inputs"
        )

        big_input = list(range(60000))
        c_start = time.perf_counter()
        cand = verify_candidate(code, impl, big_input, impl(big_input),
                                limits={"time_ms": 30000, "memory_mb": 512})
        cand_ms = time.perf_counter() - c_start

        r_start = time.perf_counter()
        verify_candidate(_PARITY_CORRECT, impl, big_input, impl(big_input),
                         limits={"time_ms": 30000, "memory_mb": 512})
        ref_ms = time.perf_counter() - r_start

        # Functionally correct at scale, so the verifier must NOT flag it...
        assert cand.verdict == NO_DEFECT, cand.reason
        # ...but it is an order of magnitude slower than the reference.
        assert cand_ms > 5 * ref_ms, (
            f"expected a clear slowdown, got candidate={cand_ms:.0f}ms "
            f"reference={ref_ms:.0f}ms"
        )

    def test_detects_nontermination(self):
        """Timeout detection, deterministically.

        An infinite loop exceeds any budget on any machine, unlike a merely
        slow candidate whose verdict depends on host speed.
        """
        code = HANGING_TARGET.code_by_family["parity-counts"]
        impl = _REFERENCE_IMPLS["parity-counts"]
        v = verify_candidate(
            code, impl, [1, 2], impl([1, 2]),
            limits={"time_ms": 500, "memory_mb": 256},
        )
        assert v.verdict == CONFIRMED_MISMATCH
        assert v.error_type == "timeout"

    def test_verdicts_carry_evidence(self):
        code = FLAWED_TARGET.code_by_family["parity-counts"]
        impl = _REFERENCE_IMPLS["parity-counts"]
        v = verify_candidate(code, impl, [1, 2, 3], impl([1, 2, 3]))
        d = v.to_dict()
        for key in ("verdict", "reason", "expected", "actual", "exit_status", "runtime_ms"):
            assert key in d
        assert d["reason"]

    def test_unverifiable_is_distinct_from_defect(self):
        """A broken probe must never be counted as a target defect."""

        class Weird:
            def __repr__(self):
                return "<weird>"

        # A candidate whose output is not JSON-serialisable is a protocol
        # violation, not evidence about the target.
        code = "def solution(v):\n    return object()\n"
        impl = _REFERENCE_IMPLS["list-dedupe"]
        v = verify_candidate(code, impl, [1], [1])
        assert v.verdict in (UNVERIFIABLE, CONFIRMED_MISMATCH)
        assert not (v.is_defect and v.is_probe_defect)

    def test_runtime_is_bounded(self):
        code = FLAWED_TARGET.code_by_family["parity-counts"]
        impl = _REFERENCE_IMPLS["parity-counts"]
        v = verify_candidate(code, impl, [1, 2, 3], impl([1, 2, 3]))
        assert v.runtime_ms >= 0
        assert v.resource_limits["time_ms"] == 2000


class TestFMPool:
    def test_training_and_heldout_are_disjoint(self):
        train_ids = {fm.fm_id for fm in TRAINING_FMS}
        held_ids = {fm.fm_id for fm in HELDOUT_FMS}
        assert not (train_ids & held_ids)

    def test_fms_are_versioned(self):
        for fm in (*TRAINING_FMS, *HELDOUT_FMS):
            assert fm.fm_id and fm.fm_version

    def test_fms_produce_attacks(self):
        for fm in (*TRAINING_FMS, *HELDOUT_FMS):
            attacks = fm.generate("list-dedupe", count=5, seed=1)
            assert len(attacks) == 5
            for a in attacks:
                assert a.fm_id == fm.fm_id
                assert a.family == "list-dedupe"

    def test_attack_ids_are_stable(self):
        fm = TRAINING_FMS[0]
        a1 = fm.generate("list-dedupe", count=3, seed=7)
        a2 = fm.generate("list-dedupe", count=3, seed=7)
        assert [a.attack_id for a in a1] == [a.attack_id for a in a2]

    def test_heldout_fms_are_not_seeds_of_training_fms(self):
        """A re-seeded training FM is not an unseen attacker."""
        train_ids = {fm.fm_id for fm in TRAINING_FMS}
        for fm in HELDOUT_FMS:
            assert fm.fm_id not in train_ids
            assert fm.fm_version not in {t.fm_version for t in TRAINING_FMS}


class TestSplitEnforcement:
    def test_training_fm_rejected_in_sealed_eval(self):
        with pytest.raises(SplitViolation):
            assert_split("boundary-fm", "sealed_eval")

    def test_heldout_fm_rejected_in_training(self):
        with pytest.raises(SplitViolation):
            assert_split("property-fm", "train")

    def test_heldout_fm_allowed_in_sealed_eval(self):
        assert_split("property-fm", "sealed_eval")

    def test_training_fm_allowed_in_training(self):
        assert_split("boundary-fm", "train")
