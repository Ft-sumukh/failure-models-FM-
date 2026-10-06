"""Regression testing pipeline with Witness stability monitoring.

This script implements the 'Fix-Verify-Witness' cycle:
1. Fix: Assume a prompt/model update has been applied.
2. Verify: Check if the target failure is actually resolved.
3. Witness: Compare decisions against a 'Golden Set' of stable probes to ensure no drift.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fmverify.witness import Decision, DriftMonitor, WitnessGate
from fmverify.llm_target import LLMTarget, ProviderError
from fmverify.tasks import get_task, _REFERENCE_IMPLS
from fmverify.verifier import verify_candidate

BAR = "=" * 74
THIN = "-" * 74

def get_decisions(target: LLMTarget, family: str, inputs: List[Any]) -> List[Decision]:
    """Extracts decisions for a set of inputs."""
    contract, _, _ = get_task(family)
    impl = _REFERENCE_IMPLS[family]
    decisions = []
    
    for val in inputs:
        # Simplified: in a real run, we'd get the output from the LLM
        # Here we simulate the LLM output for the sake of the pipeline demo
        # In reality, you'd call target.generate(contract.contract, input=val)
        res = impl(val) # Simulating a correct response for the 'golden' set
        decisions.append(Decision.from_output(res))
    return decisions

def main():
    # Setup
    target = LLMTarget("qwen/qwen3-coder") # Replace with actual model
    monitor = DriftMonitor(threshold=0.1)
    gate = WitnessGate(monitor)
    
    family = "list-dedupe"
    # The 'Golden Set': inputs that should remain stable
    golden_inputs = [[1, 2, 3], [1, 1, 2], [], [0, 0, 0]]
    
    # 1. Establish Baseline
    print(BAR)
    print(f"WITNESS STABILITY CHECK -- {family}")
    print(BAR)
    print("Step 1 -- Establishing baseline decisions...")
    baseline_decisions = get_decisions(target, family, golden_inputs)
    print(f"  Captured {len(baseline_decisions)} baseline decisions.")

    # 2. Simulate a 'Fix' (In reality, you'd change the prompt/model here)
    print("\nStep 2 -- Applying fix to targeted failure...")
    # We simulate a candidate set where one decision has drifted (changed)
    candidate_decisions = list(baseline_decisions)
    # Simulate a drift: change the result of the second input
    candidate_decisions[1] = Decision.from_output([1, 2, 3, 4]) 
    
    # 3. Evaluate with Witness Gate
    print("\nStep 3 -- Witness Evaluation")
    print(THIN)
    
    failure_fixed = True # Assume the target bug was actually fixed
    
    is_accepted, reason = gate.evaluate_fix(
        failure_fixed=failure_fixed,
        baseline_set=baseline_decisions,
        candidate_set=candidate_decisions
    )
    
    if is_accepted:
        print(f"✅ ACCEPTED: {reason}")
    else:
        print(f"❌ REJECTED: {reason}")
    print(THIN)

if __name__ == "__main__":
    main()
