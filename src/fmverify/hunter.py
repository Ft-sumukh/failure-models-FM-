from __future__ import annotations

import random
import json
from dataclasses import dataclass
from typing import Any, Protocol, List, Dict
from .tasks import get_task, TaskContract

@dataclass(frozen=True)
class Attack:
    fm_id: str
    fm_version: str
    strategy: str
    family: str
    input_value: Any
    seed: int
    provenance: str = "generated"

    @property
    def attack_id(self) -> str:
        import hashlib
        payload = json.dumps(
            {"fm": self.fm_id, "v": self.fm_version, "family": self.family, "input": self.input_value},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

class FailureModel(Protocol):
    fm_id: str
    fm_version: str
    def generate(self, family: str, count: int, seed: int, verified_failures: List[Dict] = None) -> List[Attack]:
        ...

class LLMClient(Protocol):
    def complete(self, prompt: str) -> str:
        ...

class HunterFM:
    """
    The Hunter: An LLM-driven failure model that performs 'failure extrapolation'.
    It analyzes known verified failures to hypothesize a failure mode and 
    generates new probes to test that hypothesis.
    """
    fm_id = "hunter-fm"
    fm_version = "0.1.0"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def generate(self, family: str, count: int, seed: int, verified_failures: List[Dict] = None) -> List[Attack]:
        contract_obj, _, _ = get_task(family)
        contract_text = contract_obj.contract
        
        # If no failures are known yet, fallback to random boundary probes to start the seed
        if not verified_failures:
            return self._fallback_generation(family, count, seed)

        # Failure analysis prompt
        failure_summary = "\n".join([f"- Input: {f['input']}, Expected: {f['expected']}, Got: {f['actual']}" for f in verified_failures])
        
        prompt = f"""
        You are an adversarial AI testing agent.
        Task Contract: {contract_text}
        
        Known Verified Failures:
        {failure_summary}
        
        Analyze the pattern of these failures. Why is the target model failing?
        Generate {count} new, distinct test inputs (Python lists/values) that probe this exact failure mode 
        but vary the structure or values to ensure the failure is systemic and not a fluke.
        
        Return ONLY a JSON list of the inputs. Example: [[1, 2], [3, 4]]
        """
        
        try:
            response = self.llm.complete(prompt)
            # Basic JSON extraction from LLM response
            start = response.find("[")
            end = response.rfind("]") + 1
            inputs = json.loads(response[start:end])
        except Exception:
            return self._fallback_generation(family, count, seed)

        return [
            Attack(
                fm_id=self.fm_id,
                fm_version=self.fm_version,
                strategy=f"Failure extrapolation from {len(verified_failures)} verified failures",
                family=family,
                input_value=v,
                seed=seed,
                provenance="hunter-generated"
            ) for v in inputs[:count]
        ]

    def _fallback_generation(self, family: str, count: int, seed: int) -> List[Attack]:
        # Simple fallback if no failures are known yet
        rng = random.Random(seed)
        return [
            Attack(
                fm_id=self.fm_id,
                fm_version=self.fm_version,
                strategy="Initial seeding: random boundary probes",
                family=family,
                input_value=[rng.randint(-10, 10) for _ in range(rng.randint(0, 5))],
                seed=seed,
            ) for _ in range(count)
        ]

# Note: I am appending this to your existing logic. 
# In a real scenario, we would merge this with the BoundaryFM, PropertyFM, etc.
