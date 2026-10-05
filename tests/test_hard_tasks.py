"""The harder task families must be able to fail.

If every reference implementation passes every case trivially, the task set
carries no signal and a live probe returning zero verified failures means
nothing. These tests assert the benchmark is capable of distinguishing a
correct implementation from an incorrect one -- before any model is queried.

A bug here is subtle and expensive: an oracle that silently fails to match
would report every candidate as wrong, and the pipeline would look productive
while measuring nothing.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fmverify.hard_tasks as H  # noqa: E402
from fmverify.tasks import _REFERENCE_IMPLS, get_task  # noqa: E402
from fmverify.verifier import (  # noqa: E402
    CONFIRMED_MISMATCH,
    NO_DEFECT,
    verify_candidate,
)


def _candidate_source(family: str) -> str:
    """The reference implementation, renamed to `solution`.

    The hard families name their references ``_ref_clamp_list`` while the
    originals use ``_ref_list_dedupe``. A single rename pattern therefore
    misses half the corpus, so the name is read off the function itself
    rather than reconstructed from the family key.
    """
    impl = _REFERENCE_IMPLS[family]
    return inspect.getsource(impl).replace(f"def {impl.__name__}", "def solution")


def _expected(family: str, args: list) -> object:
    impl = _REFERENCE_IMPLS[family]
    n = len(inspect.signature(impl).parameters)
    return impl(*args) if n > 1 else impl(args)


class TestHardTaskOracles:
    def test_all_hard_families_registered(self):
        for family in H.hard_families():
            contract, oracle, inputs = get_task(family)
            assert contract.oracle_ref.startswith("ref:")
            assert oracle.cases, f"{family} has no oracle cases"
            assert inputs, f"{family} has no seeded inputs"

    def test_seeded_inputs_are_full_argument_lists(self):
        """A seeded input must be the whole positional-argument list.

        Storing only the first argument makes every oracle lookup miss, which
        silently turns the oracle into a no-op -- a real bug that shipped once.
        """
        for family in H.hard_families():
            _contract, _oracle, inputs = get_task(family)
            n_args = len(inspect.signature(_REFERENCE_IMPLS[family]).parameters)
            for args in inputs:
                assert isinstance(args, list), f"{family}: {args!r}"
                assert len(args) == n_args, (
                    f"{family}: input {args!r} has {len(args)} elements but the "
                    f"reference takes {n_args}"
                )

    def test_reference_implementations_pass_their_own_cases(self):
        for family in H.hard_families():
            _contract, _oracle, inputs = get_task(family)
            src = _candidate_source(family)
            for args in inputs:
                verdict = verify_candidate(
                    src, _REFERENCE_IMPLS[family], args, _expected(family, args)
                )
                assert verdict.verdict == NO_DEFECT, (
                    f"{family} {args}: reference impl fails its own oracle "
                    f"-- {verdict.reason}"
                )

    def test_oracle_cases_match_the_reference(self):
        import json

        for family in H.hard_families():
            _contract, oracle, inputs = get_task(family)
            for args in inputs:
                key = json.dumps(args, sort_keys=True, default=str)
                assert key in oracle.cases, (
                    f"{family}: no oracle case for {args!r} -- every lookup "
                    "would miss"
                )
                assert oracle.cases[key] == _expected(family, args)

    def test_every_family_is_multi_argument(self):
        """These tasks exist to need more than a bare list argument."""
        for family in H.hard_families():
            n = len(inspect.signature(_REFERENCE_IMPLS[family]).parameters)
            assert n > 1, f"{family} takes {n} argument(s); it is not a hard task"


class TestHardTasksCanFail:
    """A benchmark that cannot fail carries no information."""

    def test_chunk_sum_catches_a_dropped_tail(self):
        """The canonical off-by-one: dropping the short final chunk.

        The fixture uses integer division to count chunks, which is how the
        bug actually appears in practice. A ``while i + size <= len(values)``
        loop and a ``for i in range(0, len(values), size)`` loop both handle
        the tail correctly, so neither of those would demonstrate anything.
        """
        buggy = """
def solution(values, size):
    out = []
    n = len(values) // size
    for j in range(n):
        out.append(sum(values[j * size:(j + 1) * size]))
    return out
"""
        args = [[1, 2, 3, 4], 3]
        assert _expected("chunk-sum", args) == [6, 4]
        verdict = verify_candidate(
            buggy, _REFERENCE_IMPLS["chunk-sum"], args,
            _expected("chunk-sum", args),
        )
        assert verdict.verdict == CONFIRMED_MISMATCH
        assert verdict.actual == [6]

    def test_rotate_list_catches_a_left_rotation(self):
        """Direction ambiguity: the contract says right."""
        left = """
def solution(values, k):
    if not values:
        return []
    k = k % len(values)
    return values[k:] + values[:k]
"""
        args = [[1, 2, 3], 1]
        verdict = verify_candidate(
            left, _REFERENCE_IMPLS["rotate-list"], args,
            _expected("rotate-list", args),
        )
        assert verdict.verdict == CONFIRMED_MISMATCH

    def test_clamp_list_catches_swapped_bounds(self):
        swapped = """
def solution(values, lo, hi):
    return [min(max(v, hi), lo) for v in values]
"""
        args = [[-5, 0, 5], 0, 10]
        verdict = verify_candidate(
            swapped, _REFERENCE_IMPLS["clamp-list"], args,
            _expected("clamp-list", args),
        )
        assert verdict.verdict == CONFIRMED_MISMATCH

    def test_clamp_list_contract_resolves_lo_greater_than_hi(self):
        """Ambiguity is a real failure mode, so the contract must resolve it.

        If the contract did not state the precedence, a candidate implementing
        the other reading would be recorded as a defect when it is actually
        ambiguous -- and that mislabel is what the
        ``specification_ambiguity`` category exists to prevent.
        """
        contract, _oracle, _inputs = get_task("clamp-list")
        assert "lo > hi" in contract.contract
        args = [[-1, 0, 1], 1, -1]
        assert _expected("clamp-list", args) == [-1, -1, -1]


class TestFMProbeArity:
    """Every FM probe must be callable against its family's contract.

    A probe whose shape does not match the contract's arity cannot be checked
    by the oracle. The verifier reports it as a target defect, so an FM
    emitting bare lists for a two-argument family silently poisons the corpus
    with failures the model did not have. This class existed because
    ``boundary-fm`` did exactly that on all three hard families.
    """

    ALL_FAMILIES = [
        "list-dedupe", "running-max", "parity-counts",
        "clamp-list", "rotate-list", "chunk-sum",
    ]

    def test_every_fm_emits_callable_probes(self):
        import fmverify.hard_tasks  # noqa: F401  -- registers hard families
        from fmverify.fms import ALL_FMS

        for family in self.ALL_FAMILIES:
            impl = _REFERENCE_IMPLS[family]
            n = len(inspect.signature(impl).parameters)
            for fm_id, fm in ALL_FMS.items():
                for attack in fm.generate(family, count=8, seed=2026):
                    try:
                        if n > 1:
                            impl(*attack.input_value)
                        else:
                            impl(attack.input_value)
                    except TypeError as exc:
                        pytest.fail(
                            f"{fm_id} produced a probe the oracle cannot call "
                            f"for {family} (arity {n}): "
                            f"{attack.input_value!r} -- {exc}"
                        )

    def test_multi_arg_families_never_get_bare_lists(self):
        import fmverify.hard_tasks  # noqa: F401
        from fmverify.fms import ALL_FMS, MULTI_ARG_FAMILIES

        for family in MULTI_ARG_FAMILIES:
            n = len(inspect.signature(_REFERENCE_IMPLS[family]).parameters)
            for fm_id, fm in ALL_FMS.items():
                for attack in fm.generate(family, count=8, seed=2026):
                    assert len(attack.input_value) == n, (
                        f"{fm_id} -> {family}: probe {attack.input_value!r} has "
                        f"{len(attack.input_value)} element(s), contract needs {n}"
                    )


class TestMultiArgumentRunner:
    def test_single_argument_tasks_still_work(self):
        """The arity change must not have broken the original families."""
        for family in ("list-dedupe", "running-max", "parity-counts"):
            contract, _oracle, inputs = get_task(family)
            src = _candidate_source(family)
            for args in inputs:
                verdict = verify_candidate(
                    src, _REFERENCE_IMPLS[family], args, _expected(family, args)
                )
                assert verdict.verdict == NO_DEFECT, f"{family} {args}: {verdict.reason}"

    def test_wrong_arity_is_a_defect_not_a_harness_crash(self):
        """A candidate with the wrong signature is a real defect, not a crash.

        It must be recorded as a TypeError from the target's side, so the
        corpus does not attribute an arity mistake to the probe.
        """
        src = "def solution(values):\n    return values\n"
        args = [[1, 2, 3], 2]
        verdict = verify_candidate(
            src, _REFERENCE_IMPLS["chunk-sum"], args, _expected("chunk-sum", args)
        )
        assert verdict.verdict == CONFIRMED_MISMATCH
        assert verdict.error_type == "TypeError"

    def test_unserializable_result_is_reported_distinctly(self):
        """A candidate returning a set is a protocol violation on the probe
        side, not evidence about the model's logic."""
        src = "def solution(values, size):\n    return {1, 2}\n"
        verdict = verify_candidate(
            src, _REFERENCE_IMPLS["chunk-sum"], [[1, 2, 3], 2], [3, 5]
        )
        assert verdict.verdict in (CONFIRMED_MISMATCH, "unverifiable")
