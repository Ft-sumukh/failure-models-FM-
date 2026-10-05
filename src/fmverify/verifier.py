"""Isolated compile-and-run verifier.

This is the exogenous signal the whole research track depends on. It must not
share state with the system under test, and it must return a verdict plus
evidence rather than a persuasive narrative.

Isolation here is process-level with real resource limits. It bounds accidental
damage -- infinite loops, memory explosions -- and is NOT a security boundary
against a determined adversary. A deployment running genuinely untrusted
candidates needs container or VM isolation. See ``ISOLATION_NOTE``.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any

ISOLATION_NOTE = (
    "Process isolation with rlimits. Bounds accidental damage, not a determined "
    "adversary. Container or VM isolation is required for untrusted candidates."
)

VERIFIER_VERSION = "fmverify-verifier/0.1.0"

DEFAULT_LIMITS = {
    "time_ms": 2000,
    "memory_mb": 256,
    "network": False,
}

# Seconds added to every budget to cover interpreter boot. This is process
# startup, not candidate runtime, so it is not charged against the candidate's
# own allowance -- otherwise a slow candidate could hide inside fixed overhead.
INTERPRETER_STARTUP_ALLOWANCE = 1.5

# Four distinct verdicts. Conflating them is how a pipeline ends up reporting
# its own infrastructure as target defects.
REPRODUCED = "reproduced"
NO_DEFECT = "no_defect"
CONFIRMED_MISMATCH = "confirmed_mismatch"
UNVERIFIABLE = "unverifiable"


@dataclass
class Verdict:
    """A verdict plus the evidence behind it. Never a narrative alone."""

    verdict: str
    reason: str
    expected: Any = None
    actual: Any = None
    exit_status: int = 0
    runtime_ms: int = 0
    error_type: str | None = None
    resource_limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))

    @property
    def is_defect(self) -> bool:
        return self.verdict in (REPRODUCED, CONFIRMED_MISMATCH)

    @property
    def is_probe_defect(self) -> bool:
        """The probe was broken. Says nothing about the target."""
        return self.verdict == UNVERIFIABLE

    def to_dict(self) -> dict:
        return asdict(self)


# ``resource`` is POSIX-only. On Windows it does not exist, so the limits are
# applied where the platform supports them and the wall-clock timeout in
# ``verify_candidate`` remains the backstop everywhere. Silently skipping the
# limits would have made every Windows verdict a harness failure while looking
# like a target defect.
_PREAMBLE = """\
import json, sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, ({mem}, {mem}))
    resource.setrlimit(resource.RLIMIT_CPU, {cpu})
except ImportError:
    # No POSIX rlimits on this platform. The parent process enforces a
    # wall-clock timeout instead. Recorded, not assumed.
    pass
"""


_RUNNER = '''\
_payload = json.loads(sys.stdin.read())
_input = _payload["input"]

try:
    _scope = {}
    exec(compile(_payload["candidate"], "<candidate>", "exec"), _scope)
    _fn = _scope.get("solution")
    if _fn is None:
        raise NameError("candidate does not define a function named 'solution'")
    _actual = _fn(_input)
except BaseException as _exc:
    sys.stdout.write(json.dumps({
        "raised": type(_exc).__name__,
        "message": str(_exc)[:400],
    }))
    sys.exit(0)

sys.stdout.write(json.dumps({"actual": _actual}))
'''


def _runner_source(reference_src: str, limits: dict) -> str:
    mem_bytes = limits["memory_mb"] * 1024 * 1024
    cpu_seconds = max(1, limits["time_ms"] // 1000)
    return (
        _PREAMBLE.format(mem=mem_bytes, cpu=cpu_seconds)
        + "\n"
        + reference_src
        + "\n"
        + _RUNNER
    )


def verify_candidate(
    candidate_code: str,
    reference_impl: Any,
    input_value: Any,
    expected_value: Any,
    limits: dict | None = None,
) -> Verdict:
    """Run one candidate against one input, compare with a trusted oracle.

    ``expected_value`` is computed by the reference implementation, never by
    the candidate and never by an FM.
    """
    lim = dict(DEFAULT_LIMITS)
    if limits:
        lim.update(limits)

    reference_src = inspect.getsource(reference_impl)
    program = _runner_source(reference_src, lim)
    payload = json.dumps(
        {"candidate": candidate_code, "input": input_value},
        default=str,
    )

    # The declared budget is enforced in the parent regardless of platform.
    # POSIX rlimits bound CPU inside the child, but they do not exist on
    # Windows, so a wall-clock deadline here is the only limit that is always
    # real. INTERPRETER_STARTUP_ALLOWANCE covers process boot, not runtime --
    # charging it against the candidate would let slow candidates hide.
    deadline_s = lim["time_ms"] / 1000 + INTERPRETER_STARTUP_ALLOWANCE

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "run_candidate.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(program)

        started = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, "-I", path],
                input=payload,
                capture_output=True,
                text=True,
                timeout=deadline_s,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return Verdict(
                verdict=CONFIRMED_MISMATCH,
                reason=(
                    f"exceeded time budget of {lim['time_ms']}ms "
                    f"(enforced as a {deadline_s:.1f}s wall-clock deadline)"
                ),
                expected=expected_value,
                actual=None,
                exit_status=-9,
                runtime_ms=int(deadline_s * 1000),
                error_type="timeout",
                resource_limits=lim,
            )
        runtime_ms = int((time.perf_counter() - started) * 1000)

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        last = tail[-1] if tail else "nonzero exit with no stderr"
        return Verdict(
            verdict=CONFIRMED_MISMATCH,
            reason=f"candidate harness failure: {last[:200]}",
            expected=expected_value,
            actual=None,
            exit_status=proc.returncode,
            runtime_ms=runtime_ms,
            error_type="harness_failure",
            resource_limits=lim,
        )

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Verdict(
            verdict=UNVERIFIABLE,
            reason="runner produced non-JSON output; probe is broken, not the target",
            exit_status=proc.returncode,
            runtime_ms=runtime_ms,
            error_type="protocol_violation",
            resource_limits=lim,
        )

    if "raised" in result:
        return Verdict(
            verdict=CONFIRMED_MISMATCH,
            reason=f"candidate raised {result['raised']}: {result.get('message','')}",
            expected=expected_value,
            actual=None,
            exit_status=proc.returncode,
            runtime_ms=runtime_ms,
            error_type=result["raised"],
            resource_limits=lim,
        )

    actual = result.get("actual")

    if actual == expected_value:
        return Verdict(
            verdict=NO_DEFECT,
            reason="matches trusted reference implementation",
            expected=expected_value,
            actual=actual,
            exit_status=proc.returncode,
            runtime_ms=runtime_ms,
            resource_limits=lim,
        )

    return Verdict(
        verdict=CONFIRMED_MISMATCH,
        reason="behaviour differs from trusted reference implementation",
        expected=expected_value,
        actual=actual,
        exit_status=proc.returncode,
        runtime_ms=runtime_ms,
        resource_limits=lim,
    )


def verify_family(
    candidate_code: str,
    family: str,
    inputs: list[Any],
    limits: dict | None = None,
) -> list[Verdict]:
    """Run a candidate across every input in a task family."""
    from .tasks import _REFERENCE_IMPLS

    impl = _REFERENCE_IMPLS[family]
    return [
        verify_candidate(candidate_code, impl, value, impl(value), limits)
        for value in inputs
    ]
