"""Target models: deliberately flawed implementations under test.

In a real run these are LLM outputs. Here they are hand-written candidates with
*known, documented* weaknesses, which makes the whole pipeline testable
offline and deterministically -- essential while establishing that the
instrument works at all.

Each target declares which weaknesses it carries. That declaration is ground
truth for scoring the FMs, and it is independent of anything the FMs produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Target:
    target_id: str
    code_by_family: dict[str, str]
    known_weaknesses: dict[str, list[str]] = field(default_factory=dict)
    description: str = ""


# --- Deliberately buggy: indexes [0] without a length check. ---
_DEDUPE_NO_EMPTY_CHECK = '''
def solution(values):
    first = values[0]
    out = []
    seen = set()
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out
'''

# --- Correct dedupe. The control target. ---
_DEDUPE_CORRECT = '''
def solution(values):
    out = []
    seen = set()
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out
'''

# --- Buggy: assumes the input is already non-decreasing. ---
# A running maximum over unsorted input is NOT the input. This diverges from
# the reference on any input that dips below an earlier element, which is
# exactly the input-shape assumption the FMs are supposed to find.
_RUNNING_MAX_ASSUMED_SORTED = '''
def solution(values):
    return list(values)
'''

_RUNNING_MAX_CORRECT = '''
def solution(values):
    out = []
    best = None
    for v in values:
        best = v if best is None or v > best else best
        out.append(best)
    return out
'''

# --- Buggy: off-by-one on the final element. ---
_PARITY_OFF_BY_ONE = '''
def solution(values):
    even = 0
    odd = 0
    for v in values[:-1]:
        if v % 2 == 0:
            even += 1
        else:
            odd += 1
    return {"even": even, "odd": odd}
'''

_PARITY_CORRECT = '''
def solution(values):
    even = 0
    odd = 0
    for v in values:
        if v % 2 == 0:
            even += 1
        else:
            odd += 1
    return {"even": even, "odd": odd}
'''

# --- Buggy: genuine O(n^2). Correct on small inputs, times out at scale. ---
_PARITY_SLOW = '''
def solution(values):
    # Inverted: the accumulator is rebuilt from scratch on every element, so
    # work grows quadratically. Functionally right, algorithmically wrong.
    running = []
    for v in values:
        if v % 2 == 0:
            running = running + [1]
        else:
            running = running + [0]
    even = sum(1 for x in running if x == 1)
    return {"even": even, "odd": len(running) - even}
'''


#: The flawed target. Three families, three distinct failure modes:
#: boundary, input-shape assumption, and off-by-one.
FLAWED_TARGET = Target(
    target_id="target-flawed@1.0.0",
    description="Hand-written candidate with documented, known weaknesses.",
    code_by_family={
        "list-dedupe": _DEDUPE_NO_EMPTY_CHECK,
        "running-max": _RUNNING_MAX_ASSUMED_SORTED,
        "parity-counts": _PARITY_OFF_BY_ONE,
    },
    known_weaknesses={
        "list-dedupe": ["boundary_condition", "input_shape_assumption"],
        "running-max": ["input_shape_assumption"],
        "parity-counts": ["boundary_condition"],
    },
)

#: The correct target. Used as a regression control: an FM that reports
#: failures here is producing false positives, and a pipeline that cannot
#: detect that is not measuring anything.
CLEAN_TARGET = Target(
    target_id="target-clean@1.0.0",
    description="Correct reference implementation, used to calibrate false positives.",
    code_by_family={
        "list-dedupe": _DEDUPE_CORRECT,
        "running-max": _RUNNING_MAX_CORRECT,
        "parity-counts": _PARITY_CORRECT,
    },
    known_weaknesses={},
)

#: Correct except for a complexity failure: quadratic, correct on small inputs.
#: Asserted by measured runtime ratio rather than by racing a timeout, because
#: a timing race is flaky by construction.
SLOW_TARGET = Target(
    target_id="target-slow@1.0.0",
    description="Functionally correct, but quadratic where the contract implies linear.",
    code_by_family={"parity-counts": _PARITY_SLOW},
    known_weaknesses={"parity-counts": ["complexity_failure"]},
)

#: Never terminates. Used to test timeout detection deterministically: an
#: infinite loop exceeds any wall-clock budget on any machine, so the verdict
#: cannot depend on how fast the host happens to be.
HANGING_TARGET = Target(
    target_id="target-hanging@1.0.0",
    description="Non-terminating candidate, for deterministic timeout detection.",
    code_by_family={"parity-counts": "def solution(values):\n    while True:\n        pass\n"},
    known_weaknesses={"parity-counts": ["complexity_failure"]},
)

TARGETS = {
    t.target_id: t
    for t in (FLAWED_TARGET, CLEAN_TARGET, SLOW_TARGET, HANGING_TARGET)
}
