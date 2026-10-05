"""Control: can the FMs detect a REAL failure in a real model's output?

A live probe returning zero verified failures is ambiguous. It means either
the model is correct on these contracts, or the FMs are not reaching the weak
cases. Those are very different conclusions and only one of them is a finding.

This script resolves the ambiguity by mutating the model's own code into a
known-buggy variant and re-running the identical probe set. If the FMs find
the injected defect, then a zero hit rate on the original is evidence the
model is correct. If they do not, the zero was the instrument's fault and
every live run so far means nothing.

Method: the mutation is a small set of mechanical, well-understood
transformations. Each one is a bug that appears constantly in real code, and
each is applied to the model's own output rather than to a synthetic
candidate, so the measurement is against genuine generated code.

Usage::

    python scripts/run_live_control.py --model qwen/qwen3-coder
"""

from __future__ import annotations

import argparse
import inspect
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fmverify.hard_tasks  # noqa: E402,F401

from fmverify.fms import TRAINING_FMS, assert_split  # noqa: E402
from fmverify.llm_target import LLMTarget, ProviderError  # noqa: E402
from fmverify.tasks import _REFERENCE_IMPLS, get_task  # noqa: E402
from fmverify.verifier import UNVERIFIABLE, verify_candidate  # noqa: E402

BAR = "=" * 74
THIN = "-" * 74


def _expected(family: str, args):
    impl = _REFERENCE_IMPLS[family]
    n = len(inspect.signature(impl).parameters)
    try:
        return impl(*args) if n > 1 else impl(args)
    except TypeError:
        return None


#: Mechanical mutations, each a real bug class. Applied to generated code.
MUTATIONS = {
    "off_by_one": (
        r"for (\w+) in range\(0, len\((\w+)\)",
        r"for \1 in range(0, max(0, len(\2) - 1))",
    ),
    "drop_last": (
        r"return (\w+)\n",
        r"return \1[:-1] if \1 else \1\n",
    ),
    "ignore_param": (
        r"def solution\(([^)]+)\):",
        lambda m: "def solution(" + ", ".join(m.group(1).split(",")[:-1]) + "):"
        if "," in m.group(1)
        else m.group(0),
    ),
    "swap_bounds": (
        r"\bmax\(([^,]+), (\w+)\)",
        r"max(\1, \2)  # mutated",
    ),
}


def apply_mutation(code: str, name: str) -> str | None:
    """Apply one mutation. Returns None if the pattern did not match."""
    if name == "swap_bounds":
        # Special case: reverse the clamp order, a real and common bug.
        new = re.sub(
            r"min\(max\((\w+), (\w+)\), (\w+)\)",
            r"min(max(\1, \3), \2)",
            code,
        )
        return new if new != code else None
    pattern, repl = MUTATIONS.get(name, (None, None))
    if pattern is None:
        return None
    new = re.sub(pattern, repl, code, count=1)
    return new if new != code else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen/qwen3-coder")
    ap.add_argument(
        "--families", default="clamp-list,rotate-list,chunk-sum",
    )
    ap.add_argument("--per-family", type=int, default=7)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    families = [f.strip() for f in args.families.split(",") if f.strip()]

    try:
        target = LLMTarget(args.model)
    except ProviderError as exc:
        print(f"FATAL: {exc}")
        return 2

    print(BAR)
    print(f"LIVE CONTROL -- can the FMs find a real defect? [{args.model}]")
    print(BAR)
    print()
    print("For each family: generate, verify the original, then inject a known")
    print("bug and re-probe. Detection on the mutant is what licenses reading")
    print("a zero on the original as 'the model is correct'.")
    print()

    originals: dict[str, str] = {}
    for family in families:
        contract, _o, _i = get_task(family)
        try:
            gen = target.generate(contract.contract)
        except ProviderError as exc:
            print(f"  {family:<14} PROVIDER ERROR: {exc}")
            continue
        originals[family] = gen.code
        print(f"  {family:<14} generated ({gen.output_tokens} tok, {gen.latency_ms}ms)")
    print()

    if not originals:
        print("No generations succeeded.")
        return 1

    print(THIN)
    print("Result")
    print(THIN)
    print()
    header = f"  {'family':<14} {'variant':<16} {'defects':>8} {'probes':>7} {'detected':>9}"
    print(header)

    detected_any = 0
    for family, code in originals.items():
        impl = _REFERENCE_IMPLS[family]

        # Baseline: the model's own code.
        base_hits = 0
        base_probes = 0
        for fm in TRAINING_FMS:
            for attack in fm.generate(family, count=args.per_family, seed=args.seed):
                exp = _expected(family, attack.input_value)
                if exp is None:
                    continue
                v = verify_candidate(code, impl, attack.input_value, exp)
                if v.verdict != UNVERIFIABLE:
                    base_probes += 1
                    if v.is_defect:
                        base_hits += 1
        print(
            f"  {family:<14} {'original':<16} {base_hits:>8} {base_probes:>7} "
            f"{'yes' if base_hits else 'no':>9}"
        )

        # Mutants: at least one must be caught for the family to be meaningful.
        caught = 0
        tried = 0
        for mut in MUTATIONS:
            mutated = apply_mutation(code, mut)
            if mutated is None:
                continue
            tried += 1
            hits = 0
            probes = 0
            for fm in TRAINING_FMS:
                for attack in fm.generate(
                    family, count=args.per_family, seed=args.seed
                ):
                    exp = _expected(family, attack.input_value)
                    if exp is None:
                        continue
                    v = verify_candidate(mutated, impl, attack.input_value, exp)
                    if v.verdict != UNVERIFIABLE:
                        probes += 1
                        if v.is_defect:
                            hits += 1
            ok = hits > 0
            caught += int(ok)
            print(
                f"  {'':<14} {mut:<16} {hits:>8} {probes:>7} "
                f"{'YES' if ok else 'no':>9}"
            )
        if caught:
            detected_any += 1
        elif tried:
            print(f"  {'':<14} {'-- none of the':<16}")
    print()

    print(THIN)
    print("Reading")
    print(THIN)
    print()
    total_fams = len(originals)
    print(f"  families where a mutant was detected: {detected_any}/{total_fams}")
    print()
    if detected_any:
        print("  The instrument finds real defects in real generated code, so a")
        print("  zero hit rate on an unmutated candidate is evidence the model is")
        print("  correct on those contracts -- not evidence the FMs are blind.")
    else:
        print("  NO MUTANT WAS DETECTED. Every live run so far is uninformative:")
        print("  a zero hit rate here means the instrument is failing, not that")
        print("  the model is correct. Fix the FMs before drawing conclusions.")
    print()
    print(f"  provider stats: {target.stats.summary()}")
    print(BAR)
    return 0 if detected_any else 1


if __name__ == "__main__":
    raise SystemExit(main())
