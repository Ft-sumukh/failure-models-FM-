"""Decision-level behaviour drift measurement.

The Witness separates functional change from sampling noise. It analyzes
the 'decision' made by a model rather than the wording of the response.
"""

from __future__ import annotations

import json
import numpy as np
from dataclasses import dataclass
from typing import Any, List, Dict, Optional

@dataclass
class Decision:
    """A functional representation of a model's output."""
    value: Any
    hash: str

    @classmethod
    def from_output(cls, output: Any) -> Decision:
        # In a production system, this would use a more complex 
        # decision-extraction logic (e.g., parsing JSON or extracting a result).
        val_str = json.dumps(output, sort_keys=True, default=str)
        return cls(value=output, hash=val_str)

class DriftMonitor:
    """
    Calculates behaviour drift between two model versions (or two prompt versions).
    Uses a simplified version of the d' (Cohen's d) metric mentioned in the research.
    """
    
    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    def calculate_drift(self, baseline_decisions: List[Decision], candidate_decisions: List[Decision]) -> float:
        """
        Calculates the proportion of decision flips.
        In a full implementation, this would calculate the effect size (d')
        across a distribution of responses to handle sampling noise.
        """
        if len(baseline_decisions) != len(candidate_decisions):
            raise ValueError("Baseline and candidate decision sets must be the same size.")
        
        if not baseline_decisions:
            return 0.0

        flips = 0
        for b, c in zip(baseline_decisions, candidate_decisions):
            if b.hash != c.hash:
                flips += 1
        
        return flips / len(baseline_decisions)

    def is_stable(self, drift_score: float) -> bool:
        return drift_score <= self.threshold

class WitnessGate:
    """
    An acceptance gate that requires both:
    1. The known failure is fixed.
    2. No significant behaviour drift is introduced.
    """
    
    def __init__(self, monitor: DriftMonitor):
        self.monitor = monitor

    def evaluate_fix(
        self, 
        failure_fixed: bool, 
        baseline_set: List[Decision], 
        candidate_set: List[Decision]
    ) -> tuple[bool, str]:
        if not failure_fixed:
            return False, "The targeted failure was not resolved."
        
        drift = self.monitor.calculate_drift(baseline_set, candidate_set)
        if not self.monitor.is_stable(drift):
            return False, f"Regression detected: Behaviour drift score {drift:.3f} exceeds threshold."
        
        return True, f"Fix verified: Failure resolved with stable behaviour (drift: {drift:.3f})."
