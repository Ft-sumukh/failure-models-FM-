"""Run real FMs against a real code model.

This is Track A's actual discovery step, with a live target instead of a
hand-written one. It answers a narrow question that nothing so far answers:

    what does a real code model actually get wrong on these contracts, and
    do the FMs find it?

It is deliberately not the transfer experiment. No model is fine-tuned here.
What this produces is (a) a verified failure corpus with real provenance, and
(b) the false-alarm rate of the FMs against a target that is not
deliberately broken -- which is the number that matters most, because every
result so far used targets whose weaknesses were known in advance.

Usage::

    python scripts/run_live_probe.py --model qwen/qwen3-coder
    python scripts/run_live_probe.py --model <id> --families list-dedupe --per-family 6
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

import fmverify.hard_tasks  # noqa: E402,F401  -- registers the harder families

from fmverify.fms import AUXILIARY_FMS, TRAINING_FMS, assert_split  # noqa: E402
from fmverify.llm_target import LLMTarget, ProviderError, probe_cost_estimate  # noqa: E402
from fmverify.records import AcceptanceGate, build_record  # noqa: E402
from fmverify.tasks import _REFERENCE_IMPLS, get_task  # noqa: E402
from fmverify.verifier import UNVERIFIABLE, verify_candidate  # noqa: E402

BAR = "=" * 74
THIN = "-" * 74


def _expected(family: str, args):
    """The trusted expected value for one probe, or None if the probe is broken.

    Arity-aware. A family whose contract takes (list, int) is called with
    unpacked arguments; a single-argument family is called with the value
    itself.

    Returns None rather than raising when the probe does not match the
    contract's arity. That is a broken probe, not a target defect, and the
    verifier must be the one to say so -- an oracle that raises here would
    crash a paid run after the generations were already billed.
    """
    impl = _REFERENCE_IMPLS[family]
    n = len(inspect.signature(impl).parameters)
    try:
        return impl(*args) if n > 1 else impl(args)
    except TypeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen/qwen3-coder", help="OpenRouter model id")
    ap.add_argument(
        "--families",
        default="clamp-list,rotate-list,chunk-sum",
        help="comma-separated task families",
    )
    ap.add_argument("--per-family", type=int, default=4, help="probes per family per FM")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default="research/data/corpus/live_probe_records.json")
    ap.add_argument(
        "--include-aux",
        action="store_true",
        help="also run the auxiliary mutation FM (not part of the primary measurement)",
    )
    args = ap.parse_args()

    families = [f.strip() for f in args.families.split(",") if f.strip()]
    try:
        target = LLMTarget(args.model, temperature=args.temperature)
    except ProviderError as exc:
        print(f"FATAL: {exc}")
        print("Set OPENROUTER_API_KEY in the environment.")
        return 2

    fms = list(TRAINING_FMS)
    if args.include_aux:
        fms += list(AUXILIARY_FMS.values())

    total_probes = len(families) * len(fms) * args.per_family
    envelope = probe_cost_estimate(total_probes)

    print(BAR)
    print(f"LIVE PROBE -- {args.model}")
    print(BAR)
    print(f"  families      : {', '.join(families)}")
    print(f"  FMs           : {', '.join(fm.fm_id for fm in fms)}")
    print(f"  probes        : {total_probes} ({len(families)} fam x {len(fms)} FM x {args.per_family})")
    print(f"  cost envelope : ~${envelope['estimated_cost_usd']:.4f} "
          f"({envelope['total_calls']} calls)")
    print()

    # One generation per family. The FMs probe the same code, which is the
    # right design: a single sample per contract keeps cost bounded and the
    # FM comparison is still apples-to-apples across FM families.
    print(THIN)
    print("Step 1 -- generate one candidate per family from the live model")
    print(THIN)
    generations: dict[str, tuple] = {}
    for family in families:
        contract, _oracle, _inputs = get_task(family)
        try:
            gen = target.generate(contract.contract)
        except ProviderError as exc:
            print(f"  {family:<16} PROVIDER ERROR: {exc}")
            continue
        generations[family] = (gen, contract)
        first_line = next(
            (l for l in gen.code.splitlines() if l.strip().startswith("def ")), "<no def>"
        )
        print(f"  {family:<16} ok  {gen.latency_ms:>6}ms  {gen.output_tokens:>4} tok  {first_line[:40]}")
    print()

    if not generations:
        print("No generations succeeded. Nothing to probe.")
        return 1

    print(THIN)
    print("Step 2 -- FMs attack each candidate; the verifier decides")
    print(THIN)
    print()

    per_fm = defaultdict(lambda: {"defect": 0, "clean": 0, "probe_defect": 0, "total": 0})
    records = []
    gate = AcceptanceGate(split="train")
    printed_source = False

    for family, (gen, contract) in generations.items():
        impl = _REFERENCE_IMPLS[family]
        if not printed_source:
            src = gen.code.splitlines()
            head = src[0] if src else "<empty>"
            print(f"  candidate for {family}: {head[:60]}")
            print(f"  {len(src)} lines generated")
            print()
            printed_source = True

        for fm in fms:
            assert_split(fm.fm_id, "train")
            for attack in fm.generate(family, count=args.per_family, seed=args.seed):
                expected = _expected(family, attack.input_value)
                if expected is None:
                    # The FM produced a probe whose shape does not match the
                    # contract. Counted as a broken probe, never as a target
                    # defect -- attributing it to the model would be wrong.
                    per_fm[fm.fm_id]["probe_defect"] += 1
                    per_fm[fm.fm_id]["total"] += 1
                    continue
                verdict = verify_candidate(
                    gen.code, impl, attack.input_value, expected,
                )
                stats = per_fm[fm.fm_id]
                stats["total"] += 1

                if verdict.verdict == UNVERIFIABLE:
                    stats["probe_defect"] += 1
                elif verdict.is_defect:
                    stats["defect"] += 1
                    rec = build_record(
                        contract, attack, f"{gen.model_id}", verdict,
                        _categorize(verdict, family),
                    )
                    rec.target["model_id"] = gen.model_id
                    rec.target["prompt_hash"] = gen.prompt_hash
                    admit, reason = gate.evaluate(rec, impl)
                    rec.dataset["eligible_for_training"] = admit
                    if not admit:
                        rec.dataset["exclusion_reason"] = reason
                    records.append(rec)
                else:
                    stats["clean"] += 1

    print(f"  {'FM':<24} {'defects':>8} {'clean':>7} {'probe-def':>10} {'probes':>7} {'hit rate':>9}")
    for fm_id, s in sorted(per_fm.items()):
        hit = s["defect"] / max(1, s["total"])
        print(
            f"  {fm_id:<24} {s['defect']:>8} {s['clean']:>7} "
            f"{s['probe_defect']:>10} {s['total']:>7} {hit:>9.3f}"
        )
    print()

    total_defects = sum(s["defect"] for s in per_fm.values())
    total_probes_run = sum(s["total"] for s in per_fm.values())
    if total_probes_run:
        print(f"  aggregate hit rate: {total_defects}/{total_probes_run} "
              f"= {total_defects / total_probes_run:.3f}")
    print()

    if records:
        print(THIN)
        print("Step 3 -- verified failure records")
        print(THIN)
        admitted = [r for r in records if r.dataset["eligible_for_training"]]
        print(f"  verified failures : {len(records)}")
        print(f"  admitted by gate  : {len(admitted)}")
        by_cat = defaultdict(int)
        for r in records:
            by_cat[r.analysis["primary_category"]] += 1
        for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
            print(f"    {cat:<32} {n}")
        print()
        for r in records[:3]:
            print(f"  example: {r.attack['fm_id']} on {r.task['task_id']}")
            print(f"    category: {r.analysis['primary_category']}")
            print(f"    reason  : {r.analysis['summary'][:100]}")
            print(f"    input   : {json.dumps(r.attack.get('input_ref'))} "
                  f"(seed {r.attack.get('seed')})")
        print()

        out_path = ROOT / args.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps([r.to_dict() for r in records], indent=2, default=str),
            encoding="utf-8",
        )
        print(f"  records written: {out_path.relative_to(ROOT)}")
    else:
        print("  No verified failures. Either the model is correct on these")
        print("  contracts, or the FMs are not reaching the weak cases. With")
        print("  one generation per family and few probes, both are plausible.")
        print("  Raise --per-family before concluding anything.")
    print()

    print(f"  provider stats: {target.stats.summary()}")
    print()
    print(BAR)
    print("No transfer result is claimed. Nothing was fine-tuned. This run")
    print("establishes only what the model does now and whether the FMs")
    print("can see it.")
    print(BAR)
    return 0


def _categorize(verdict, family: str) -> str:
    """Map a verdict onto the taxonomy, conservatively.

    A timeout or a resource failure is a complexity failure. Anything else is
    recorded as input_shape_assumption only when the input was non-empty;
    guessing more precisely from a verdict alone would be the kind of
    confident label that poisons training data.
    """
    if verdict.error_type == "timeout":
        return "complexity_failure"
    if verdict.error_type in ("IndexError", "KeyError"):
        return "boundary_condition"
    if verdict.error_type in ("TypeError", "AttributeError"):
        return "input_shape_assumption"
    return "algorithmic_logic"


if __name__ == "__main__":
    raise SystemExit(main())
