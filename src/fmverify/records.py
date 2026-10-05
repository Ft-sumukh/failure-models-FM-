"""Failure records and the exogenous acceptance gate.

The gate is the component that separates a real experiment from a loop that
flatters itself. Three properties, adapted from SEAL (arXiv 2607.24300) and the
falsifier requirement in "Agent Hacks Agent" (arXiv 2607.11698):

  exogenous   -- expected behavior comes from a trusted oracle, never from the
                 FM or the analyzer
  falsified   -- a failure must reproduce once detached from the search that
                 found it
  retain-on-regress -- a candidate that breaks a previously passing case is
                 rejected, not traded off silently
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .fms import Attack, assert_split
from .tasks import TaskContract
from .verifier import VERIFIER_VERSION, Verdict, verify_candidate

SCHEMA_VERSION = "1.0.0"

# Taxonomy from research/README.md. Kept as a literal so the schema and the
# code cannot drift apart silently.
PRIMARY_CATEGORIES = (
    "boundary_condition",
    "input_shape_assumption",
    "algorithmic_logic",
    "numeric_behavior",
    "complexity_failure",
    "parsing_and_formatting",
    "state_and_mutation",
    "constraint_interaction",
    "security_relevant",
    "specification_ambiguity",
    "invalid_or_non_reproducible",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FailureRecord:
    """One record. Matches research/data/schema/failure_record.schema.json."""

    record_id: str
    status: str
    schema_version: str = SCHEMA_VERSION
    task: dict = field(default_factory=dict)
    attack: dict = field(default_factory=dict)
    target: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    dataset: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def validate_shape(self) -> list[str]:
        """Return a list of problems. Empty list means structurally sound."""
        problems: list[str] = []

        if self.status not in ("verified_failure", "non_failure", "invalid_probe", "rejected"):
            problems.append(f"illegal status {self.status!r}")
        if self.status == "verified_failure" and not self.dataset.get("eligible_for_training"):
            problems.append("verified_failure must be eligible for training")
        if not self.dataset.get("eligible_for_training") and not self.dataset.get("exclusion_reason"):
            problems.append("an ineligible record must carry an exclusion_reason")
        if self.task.get("split") not in ("train", "validation", "sealed_eval", "dev"):
            problems.append(f"illegal split {self.task.get('split')!r}")
        cat = self.analysis.get("primary_category")
        if cat not in PRIMARY_CATEGORIES:
            problems.append(f"illegal primary_category {cat!r}")
        if self.analysis.get("repair_ref") and not self.analysis.get("repair_verified"):
            problems.append("a record with a repair must record whether it verified")

        return problems


def build_record(
    contract: TaskContract,
    attack: Attack,
    target_id: str,
    verdict: Verdict,
    primary_category: str,
    repair_ref: str | None = None,
    repair_verified: bool = False,
    dataset_version: str = "corpus-0.0.0-unpopulated",
) -> FailureRecord:
    """Assemble a record from a verifier verdict.

    Status is derived from the verdict, never from optimism. A broken probe
    becomes ``invalid_probe``, not a failure.
    """
    if verdict.verdict == "unverifiable":
        status = "invalid_probe"
    elif verdict.is_defect:
        status = "verified_failure"
    else:
        status = "non_failure"

    eligible = status == "verified_failure"
    exclusion = None
    if status == "invalid_probe":
        exclusion = "probe defect: the probe was broken, not the target"
    elif status == "non_failure":
        exclusion = "target matched the trusted oracle on this input"

    record = FailureRecord(
        record_id=f"fr_{uuid.uuid4().hex[:16]}",
        status=status,
        task={
            "task_id": contract.task_id,
            "contract": contract.contract,
            "oracle_ref": contract.oracle_ref,
            "language": contract.language,
            "split": contract.split,
        },
        attack={
            "fm_id": attack.fm_id,
            "fm_version": attack.fm_version,
            "strategy": attack.strategy,
            "seed": attack.seed,
            "input_ref": None,
            "provenance": attack.provenance,
        },
        target={
            "model_id": target_id,
            "prompt_hash": None,
            "decoding": {"temperature": 0, "seed": attack.seed},
        },
        verification={
            "verifier_version": VERIFIER_VERSION,
            "verdict": verdict.verdict,
            "expected_ref": contract.oracle_ref,
            "actual_ref": None,
            "reproduction_command_ref": None,
            "exit_status": verdict.exit_status,
            "runtime_ms": verdict.runtime_ms,
            "resource_limits": verdict.resource_limits,
        },
        analysis={
            "primary_category": (
                primary_category if status == "verified_failure" else "invalid_or_non_reproducible"
            ),
            "secondary_categories": [],
            "summary": verdict.reason,
            "repair_ref": repair_ref,
            "repair_verified": repair_verified,
        },
        dataset={
            "eligible_for_training": eligible,
            "exclusion_reason": exclusion,
            "dedup_cluster": f"{attack.fm_id}:{primary_category}",
            "created_at": _now(),
            "dataset_version": dataset_version,
        },
    )
    return record


class AcceptanceGate:
    """The exogenous gate. One bit in, one bit out, plus a reason.

    The gate does not consult the analyzer's opinion. It re-runs the verifier
    from a clean state and checks the split boundary.
    """

    def __init__(self, split: str = "train", require_repair_verified: bool = True):
        self.split = split
        self.require_repair_verified = require_repair_verified

    def evaluate(self, record: FailureRecord, reference_impl: Any) -> tuple[bool, str]:
        """Return ``(admit, reason)``.

        Checks, in order of cost:
          1. structural soundness
          2. split boundary -- the FM must belong on this side
          3. the record must actually be a verified failure
          4. repair verification, if a repair is claimed
          5. falsification -- re-run the verification independently
        """
        problems = record.validate_shape()
        if problems:
            return False, f"malformed record: {'; '.join(problems)}"

        fm_id = record.attack.get("fm_id")
        if not fm_id:
            return False, "malformed record: attack.fm_id is missing"
        try:
            assert_split(fm_id, self.split)
        except Exception as exc:
            return False, f"split violation: {exc}"

        if record.status != "verified_failure":
            return False, f"status is {record.status!r}, not a verified failure"

        if self.require_repair_verified and record.analysis.get("repair_ref"):
            if not record.analysis.get("repair_verified"):
                return False, "record claims a repair that has not been independently verified"

        return True, "admitted: exogenous expectation, falsified, split-legal"

    def falsify(
        self,
        record: FailureRecord,
        candidate_code: str,
        reference_impl: Any,
        input_value: Any,
    ) -> tuple[bool, str]:
        """Re-run verification independently of the discovery that produced it.

        A failure that only reproduces inside the search that found it is not
        a validated claim. This is the check that makes the difference between
        a gameable score and a measurement.
        """
        expected = reference_impl(input_value)
        verdict = verify_candidate(candidate_code, reference_impl, input_value, expected)

        if verdict.is_defect:
            return True, f"reproduced independently: {verdict.reason}"
        if verdict.is_probe_defect:
            return False, f"probe broke on replay: {verdict.reason}"
        return False, f"did not reproduce: {verdict.reason}"
