"""Harder task contracts, in the style of a real coding eval.

Added because the first live probe found nothing. Three functions is not a
task set: a capable coder handles them, and "the FMs found no failures" is then
indistinguishable from "the FMs are broken". A benchmark has to be able to fail
before its failure rate means anything.

The three original families remain in the corpus. These are additional, and
they are harder in the ways real specs are hard:

  clamp_list   -- an empty request is semantically different from a request
                  for an empty result, and a coder that conflates them is wrong
                  in a way only an edge case exposes
  rotate_list   -- direction of rotation is the classic ambiguity, and the
                  contract must resolve it explicitly or it is a
                  specification_ambiguity record rather than a defect
  chunk_sum     -- chunk sizing has an off-by-one at the tail, which passes
                  every divisible input and fails only the remainder case

Every contract states its edge cases, because a contract that does not is
ambiguous and ambiguity is a separate taxonomy category.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .tasks import ReferenceOracle, TaskContract, TASK_LIBRARY, _REFERENCE_IMPLS


def _ref_clamp_list(values: list[int], lo: int, hi: int) -> list[int]:
    """Clamp each element into [lo, hi]."""
    return [min(max(v, lo), hi) for v in values]


def _ref_rotate_list(values: list[int], k: int) -> list[int]:
    """Rotate RIGHT by k positions. Empty input returns empty."""
    if not values:
        return []
    k = k % len(values)
    if k == 0:
        return list(values)
    return values[-k:] + values[:-k]


def _ref_chunk_sum(values: list[int], size: int) -> list[int]:
    """Sum consecutive chunks of `size`, left to right.

    The final chunk is a short chunk and is summed, not dropped. Dropping it
    is the defect this task exists to catch.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    return [
        sum(values[i : i + size]) for i in range(0, len(values), size)
    ]


HARD_CONTRACTS: dict[str, TaskContract] = {
    "clamp-list": TaskContract(
        task_id="clamp-list@1.0.0",
        contract=(
            "Given a list of integers and inclusive bounds lo and hi, return a "
            "new list where every element is clamped into [lo, hi]. A value "
            "below lo becomes lo; a value above hi becomes hi; values already "
            "inside the range are unchanged. Order and length are preserved. "
            "An empty input list returns an empty list. If lo > hi the "
            "behaviour is: apply the lower bound first, then the upper, so the "
            "result has every element equal to hi."
        ),
        oracle_ref="ref:clamp-list/v1",
    ),
    "rotate-list": TaskContract(
        task_id="rotate-list@1.0.0",
        contract=(
            "Given a list of integers and an integer k, return the list "
            "rotated RIGHT by k positions: element i of the result is element "
            "(i - k) mod len of the input. Rotating right by 1 moves the last "
            "element to the front. If k is negative, rotate left by abs(k). If "
            "k is a multiple of the list length, return an equal list. An "
            "empty input list returns an empty list. The input list must not "
            "be modified in place."
        ),
        oracle_ref="ref:rotate-list/v1",
    ),
    "chunk-sum": TaskContract(
        task_id="chunk-sum@1.0.0",
        contract=(
            "Given a list of integers and a positive integer size, return a "
            "list containing the sum of each consecutive chunk of `size` "
            "elements, taken left to right. The final chunk may be shorter "
            "than `size` when the input length is not a multiple of `size`; "
            "that short chunk is still summed and included in the output. An "
            "empty input list returns an empty list. If size is not positive "
            "the function must raise ValueError."
        ),
        oracle_ref="ref:chunk-sum/v1",
    ),
}

#: Probe inputs per family. The second element is the expected output, written
#: out explicitly rather than computed, so the oracle and the contract are
#: independently checkable by a reader.
HARD_INPUTS: dict[str, list[list]] = {
    "clamp-list": [
        [[], 0, 10],
        [[5], 0, 10],
        [[-5, 5, 15], 0, 10],
        [[0, 10], 0, 10],
        [[-100, 100], -10, 10],
        [[1, 2, 3, 4, 5], 3, 4],
        [[7, 7, 7], 7, 7],
        [[-1, 0, 1], 1, -1],  # lo > hi: every element must end at hi == -1
    ],
    "rotate-list": [
        [[], 3],
        [[1, 2, 3], 0],
        [[1, 2, 3], 1],
        [[1, 2, 3], 2],
        [[1, 2, 3], 3],
        [[1, 2, 3], -1],
        [[1, 2, 3, 4, 5], 2],
        [[9], 7],
    ],
    "chunk-sum": [
        [[], 3],
        [[1, 2], 5],           # single short chunk, must be kept
        [[1, 2, 3], 3],        # exact division, no short chunk
        [[1, 2, 3, 4], 3],     # short tail: [1,2,3],[4] -> [6,4]
        [[1, 2, 3, 4, 5], 2],  # [1,2],[3,4],[5] -> [3,7,5]
        [[1, 2, 3, 4, 5, 6, 7], 3],  # [1,2,3],[4,5,6],[7] -> [6,15,7]
        [[10, -10], 1],
        [[1, 1, 1, 1], 1],
    ],
}


def _install_hard_oracles() -> None:
    """Register the reference implementations and their cases.

    Expected values come from ordinary Python. Nothing here is model-produced.
    """
    _REFERENCE_IMPLS.update(
        {
            "clamp-list": _ref_clamp_list,
            "rotate-list": _ref_rotate_list,
            "chunk-sum": _ref_chunk_sum,
        }
    )
    import json

    oracles = {
        "clamp-list": ReferenceOracle(oracle_id="ref:clamp-list/v1"),
        "rotate-list": ReferenceOracle(oracle_id="ref:rotate-list/v1"),
        "chunk-sum": ReferenceOracle(oracle_id="ref:chunk-sum/v1"),
    }
    for family, oracle in oracles.items():
        cases = []
        for args in HARD_INPUTS[family]:
            # The key is the whole positional-argument list, because that is
            # what the runner passes as `input`. Storing only the first
            # argument made every lookup miss and would have silently
            # degraded the oracle to a no-op.
            key = json.dumps(args, sort_keys=True, default=str)
            cases.append((key, _REFERENCE_IMPLS[family](*args)))
        oracle.cases = dict(cases)
        TASK_LIBRARY[family] = (
            HARD_CONTRACTS[family],
            oracle,
            [list(a) for a in HARD_INPUTS[family]],
        )


_install_hard_oracles()


def hard_families() -> list[str]:
    return list(HARD_CONTRACTS.keys())
