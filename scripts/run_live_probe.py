"""Run real FMs against a real code model with Hunter extrapolation.

This script implements a multi-pass discovery pipeline:
1. Discovery: Standard FMs find initial failures.
2. Extrapolation: HunterFM analyzes failures to find systemic bugs.
3. Verification: Final failures are verified and recorded.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fmverify.hard_tasks  # noqa: E402,F401
from fmverify.fms import AUXILIARY_FMS, TRAINING_FMS, assert_split # noqa: E402
from fmverify.hunter import HunterFM, LLMClient # noqa: E402
from fmverify.llm_target import LLMTarget, ProviderError, probe_cost_estimate # noqa: E402
from fmverify.records import (
    AcceptanceGate,
    build_record,
    count_distinct_failures,
    filter_new_failures,
)
from fmverify.tasks import _REFERENCE_IMPLS, get_task # noqa: E402
from fmverify.verifier import UNVERIFIABLE, verify_candidate # noqa: E402

BAR = "=" * 74
THIN = "-" * 74

class SimpleLLMClient(LLMClient):
    """A mock client for demonstration. Replace with real OpenAI/Anthropic client."""
    def complete(self, prompt: str) -> str:
        # In a real scenario, this would call an LLM API.
        # Here we simulate a JSON response of a few a likely edge-case inputs.
        return '[[0], [1, 1, 1], [-1, -1]]'

def _expected(family: str, args):
    impl = _REFERENCE_IMPLS[family]
    n = len(inspect.signature(impl).parameters)
    try:
        return impl(*args) if n > 1 else impl(args)
    except TypeError:
        return None

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen/qwen3-coder", help="OpenRouter model id")
    ap.add_argument("--families", default="clamp-list,rotate-list,chunk-sum", help="families")
    ap.add_argument("--per-family", type=int, default=4, help="probes per family per FM")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default="research/data/corpus/live_probe_records.json")
    ap.add_argument("--include-aux", action="store_true")
    args = ap.parse_args()

    families = [f.strip() for f in args.families.split(",") if f.strip()]
    try:
        target = LLMTarget(args.model, temperature=args.temperature, max_tokens=args.max_tokens)
    except ProviderError as exc:
        print(f"FATAL: {exc}")
        return 2

    # Setup FMs
    initial_fms = list(TRAINING_FMS)
    if args.include_aux:
        initial_fms += list(AUXILIARY_FMS.values())
    
    hunter = HunterFM(llm=SimpleLLMClient())
    
    total_probes = (len(families) * len(initial_fms) * args.per_family) + (len(families) * args.per_family)
    envelope = probe_cost_estimate(total_probes)

    print(BAR)
    print(f"LIVE PROBE WITH HUNTER -- {args.model}")
    print(BAR)
    print(f"  families      : {', '.join(families)}")
    print(f"  initial FMs   : {', '.join(fm.fm_id for fm in initial_fms)}")
    print(f"  hunter FM     : {hunter.fm_id}")
    print(f"  cost envelope  : ~${envelope['estimated_cost_usd']:.4f}")
    print()

    print(THIN)
    print("Step 1 -- Generate model candidates")
    print(THIN)
    generations: dict[str, tuple] = {}
    for family in families:
        contract, _oracle, _inputs = get_task(family)
        try:
            gen = target.generate(contract.contract)
            generations[family] = (gen, contract)
            print(f"  {family:<16} ok")
        except ProviderError as exc:
            print(f"  {family:<16} ERROR: {exc}")

    if not generations:
        return 1

    print(THIN)
    print("Step 2 -- Pass 1: Initial Discovery")
    print(THIN)
    
    per_fm = defaultdict(lambda: {"defect": 0, "clean": 0, "probe_defect": 0, "total": 0})
    records = []
    gate = AcceptanceGate(split="train")

    for family, (gen, contract) in generations.items():
        impl = _REFERENCE_IMPLS[family]
        for fm in initial_fms:
            assert_split(fm.fm_id, "train")
            for attack in fm.generate(family, count=args.per_family, seed=args.seed):
                expected = _expected(family, attack.input_value)
                if expected is None:
                    per_fm[fm.fm_id]["probe_defect"] += 1
                    per_fm[fm.fm_id]["total"] += 1
                    continue
                verdict = verify_candidate(gen.code, impl, attack.input_value, expected)
                stats = per_fm[fm.fm_id]
                stats["total"] += 1
                if verdict.verdict == UNVERIFIABLE:
                    stats["probe_defect"] += 1
                elif verdict.is_defect:
                    stats["defect"] += 1
                    rec = build_record(contract, attack, f"{gen.model_id}", verdict, _categorize(verdict, family))
                    rec.target["model_id"] = gen.model_id
                    records.append(rec)
                else:
                    stats["clean"] += 1

    print(THIN)
    print("Step 3 -- Pass 2: Strategic Extrapolation (Hunter)")
    print(THIN)

    for family, (gen, contract) in generations.items():
        impl = _REFERENCE_IMPLS[family]
        # Extract verified failures for this specific family to feed the Hunter
        family_failures = [
            {"input": r.attack.input_value, "expected": r.oracle_expected, "actual": r.target_actual}
            for r in records if r.task.task_id.startswith(family)
        ]
        
        print(f"  {family:<16} Hunter analyzing {len(family_failures)} failures...")
        for attack in hunter.generate(family, count=args.per_family, seed=args.seed, verified_failures=family_failures):
            expected = _expected(family, attack.input_value)
            if expected is None:
                per_fm[hunter.fm_id]["probe_defect"] += 1
                per_fm[hunter.fm_id]["total"] += 1
                continue
            verdict = verify_candidate(gen.code, impl, attack.input_value, expected)
            stats = per_fm[hunter.fm_id]
            stats["total"] += 1
            if verdict.verdict == UNVERIFIABLE:
                stats["probe_defect"] += 1
            elif verdict.is_defect:
                stats["defect"] += 1
                rec = build_record(contract, attack, f"{gen.model_id}", verdict, _categorize(verdict, family))
                rec.target["model_id"] = gen.model_id
                records.append(rec)
            else:
                stats["clean"] += 1

    print(f"\n  {'FM':<24} {'defects':>8} {'clean':>7} {'probe-def':>10} {'probes':>7} {'hit rate':>9}")
    for fm_id, s in sorted(per_fm.items()):
        hit = s["defect"] / max(1, s["total"])
        print(f"  {fm_id:<24} {s['defect']:>8} {s['clean']:>7} {s['probe_def']:>10} {s['total']:>7} {hit:>9.3f}")

    if records:
        out_path = ROOT / args.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps([r.to_dict() for r in records], indent=2, default=str), encoding="utf-8")
        print(f"\n  records written: {out_path.relative_to(ROOT)}")
    
    return 0

def _categorize(verdict, family: str) -> str:
    if verdict.error_type == "timeout": return "complexity_failure"
    if verdict.error_type in ("IndexError", "KeyError"): return "boundary_condition"
    if verdict.error_type in ("TypeError", "AttributeError"): return "input_shape_assumption"
    return "algorithmic_logic"

if __name__ == "__main__":
    raise SystemExit(main())
