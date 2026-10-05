"""Task contracts and trusted oracles.

An oracle here is never a model's answer. It is a reference implementation or
a property suite, versioned independently, and it is the only thing permitted
to define expected behavior.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class TaskContract:
    """The source of truth for verification.

    ``oracle_ref`` must point at something that exists and is versioned. A
    contract whose oracle is "whatever the FM said" is not a contract.
    """

    task_id: str
    contract: str
    oracle_ref: str
    language: str = "python"
    split: str = "train"

    @property
    def task_family(self) -> str:
        """Family drives splitting. Rows never do."""
        return self.task_id.split("@", 1)[0]

    def content_hash(self) -> str:
        payload = f"{self.task_id}|{self.contract}|{self.oracle_ref}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class ReferenceOracle:
    """A trusted expected-output source.

    Two modes, deliberately distinct:

    ``cases``  -- a mapping of input to expected output, from a reference
                  implementation. Strong signal.
    ``property`` -- a predicate over (input, output). Weaker but scales, and
                  the weakness is recorded so it can be weighted.
    """

    oracle_id: str
    cases: dict[str, Any] = field(default_factory=dict)
    property: Callable[[Any, Any], bool] | None = None
    property_id: str | None = None

    def check(self, input_value: Any, output_value: Any) -> tuple[bool, str]:
        """Return ``(passes, reason)``. Never returns a narrative as a verdict."""
        key = json.dumps(input_value, sort_keys=True, default=str)
        if key in self.cases:
            expected = self.cases[key]
            if output_value == expected:
                return True, "matches reference case"
            return False, f"expected {expected!r}, got {output_value!r}"
        if self.property is not None:
            try:
                ok = bool(self.property(input_value, output_value))
            except Exception as exc:  # a broken property is a broken probe
                return False, f"property raised {type(exc).__name__}: {exc}"
            if ok:
                return True, f"satisfies {self.property_id}"
            return False, f"violates {self.property_id}"
        return False, "no trusted expectation available for this input"


# --- A small reference task set. Real oracles, no model involvement. ---


def _ref_dedupe(values: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _ref_running_max(values: list[int]) -> list[int]:
    out: list[int] = []
    best = None
    for v in values:
        best = v if best is None or v > best else best
        out.append(best)
    return out


def _ref_parity_counts(values: list[int]) -> dict[str, int]:
    return {
        "even": sum(1 for v in values if v % 2 == 0),
        "odd": sum(1 for v in values if v % 2 != 0),
    }


#: Each entry: contract, oracle, and a set of inputs including the edges that
#: boundary FMs will go after.
TASK_LIBRARY: dict[str, tuple[TaskContract, ReferenceOracle, list[Any]]] = {
    "list-dedupe": (
        TaskContract(
            task_id="list-dedupe@1.0.0",
            contract=(
                "Given a list of integers, return the list with duplicates "
                "removed and first-occurrence order preserved. Empty input must "
                "return an empty list. No valid input may raise."
            ),
            oracle_ref="ref:list-dedupe/v1",
        ),
        ReferenceOracle(oracle_id="ref:list-dedupe/v1", cases={}),
        [
            [], [1], [1, 1], [1, 2, 3], [3, 1, 3, 1, 2], [0, 0, 0],
            [-1, 1, -1], list(range(50)),
        ],
    ),
    "running-max": (
        TaskContract(
            task_id="running-max@1.0.0",
            contract=(
                "Given a list of integers, return a list of the same length "
                "where element i is the maximum of values[0..i]. Empty input "
                "must return an empty list."
            ),
            oracle_ref="ref:running-max/v1",
        ),
        ReferenceOracle(oracle_id="ref:running-max/v1", cases={}),
        [
            [], [5], [5, 3], [3, 5, 2, 8, 1], [-3, -7, -1], list(range(100, 0, -1)),
        ],
    ),
    "parity-counts": (
        TaskContract(
            task_id="parity-counts@1.0.0",
            contract=(
                "Given a list of integers, return a dict with keys 'even' and "
                "'odd' holding how many elements fall in each class. Empty "
                "input must return {'even': 0, 'odd': 0}."
            ),
            oracle_ref="ref:parity-counts/v1",
        ),
        ReferenceOracle(oracle_id="ref:parity-counts/v1", cases={}),
        [[], [1], [2], [1, 2, 3, 4], [-2, -1, 0, 1, 2], list(range(20))],
    ),
}

_REFERENCE_IMPLS = {
    "list-dedupe": _ref_dedupe,
    "running-max": _ref_running_max,
    "parity-counts": _ref_parity_counts,
}


def populate_oracles() -> None:
    """Fill each oracle's cases from its reference implementation.

    Called once at setup. The expected values are computed by ordinary Python,
    not by any model, which is the entire point.
    """
    for family, (_contract, oracle, inputs) in TASK_LIBRARY.items():
        impl = _REFERENCE_IMPLS[family]
        for value in inputs:
            import json as _json

            oracle.cases[_json.dumps(value, sort_keys=True, default=str)] = impl(value)


populate_oracles()


def get_task(family: str) -> tuple[TaskContract, ReferenceOracle, list[Any]]:
    if family not in TASK_LIBRARY:
        raise KeyError(f"unknown task family {family!r}")
    return TASK_LIBRARY[family]


def all_tasks() -> list[tuple[TaskContract, ReferenceOracle, list[Any]]]:
    return list(TASK_LIBRARY.values())
