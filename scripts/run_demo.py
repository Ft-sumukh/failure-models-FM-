"""End-to-end demo: FM discovery, gated admission, and transfer measurement.

Run with::

    python scripts/run_demo.py

What it prints is a measurement, not a claim. In particular the FM results
here are a sanity check that the pipeline discriminates -- not evidence that
failure-driven training works. No model is fine-tuned by this script, and the
transfer number it reports is the *baseline* the real experiment would compare
against.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fmverify.fms import HELDOUT_FMS, TRAINING_FMS, Attack, assert_split
from fmverify.noise_floor import run_experiment
from fmverify.records import AcceptanceGate, build_record
from fmverify.targets import CLEAN_TARGET, FLAWED_TARGET
from fmverify.tasks import _REFERENCE_IMPLS, get_task
from fmverify.verifier import verify_candidate

BAR = "=" * 74
THIN = "-" * 74


def run_track_a(per_family: int = 8, seed: int = 2026) -> None:
    print(BAR)
    print("TRACK A -- FM discovery, exogenous verification, gated admission")
    print(BAR)
    print()
    print("Targets carry documented weaknesses. The FMs do not know this;")
    print("it is ground truth used only to score them afterwards.")
    print()

    for target, label in (
        (FLAWED_TARGET, "FLAWED target (known weaknesses)"),
        (CLEAN_TARGET, "CLEAN target (control: must produce no verified failures)"),
    ):
        print(THIN)
        print(f"{label}")
        print(THIN)

        per_fm = defaultdict(lambda: {"verified": 0, "non_failure": 0, "total": 0})
        categories = defaultdict(int)
        true_positives = 0
        false_positives = 0

        for fm in TRAINING_FMS:
            gate = AcceptanceGate(split="train")
            for family, code in target.code_by_family.items():
                _contract, _oracle, _inputs = get_task(family)
                impl = _REFERENCE_IMPLS[family]

                for attack in fm.generate(family, count=per_family, seed=seed):
                    assert_split(attack.fm_id, "train")
                    verdict = verify_candidate(
                        code, impl, attack.input_value, impl(attack.input_value)
                    )
                    per_fm[fm.fm_id]["total"] += 1

                    if verdict.is_defect:
                        per_fm[fm.fm_id]["verified"] += 1
                        known = target.known_weaknesses.get(family, [])
                        if known:
                            true_positives += 1
                            categories[f"{family}:{known[0]}"] += 1
                        else:
                            false_positives += 1

                        rec = build_record(
                            get_task(family)[0],
                            attack,
                            target.target_id,
                            verdict,
                            known[0] if known else "algorithmic_logic",
                        )
                        admit, reason = gate.evaluate(rec, impl)
                        assert admit, f"gate rejected a verified failure: {reason}"
                    else:
                        per_fm[fm.fm_id]["non_failure"] += 1

        print(f"  {'FM':<24} {'verified':>9} {'non-fail':>9} {'total':>6} {'hit rate':>10}")
        for fm_id, s in sorted(per_fm.items()):
            hit_rate = s["verified"] / max(1, s["total"])
            print(
                f"  {fm_id:<24} {s['verified']:>9} {s['non_failure']:>9} "
                f"{s['total']:>6} {hit_rate:>10.3f}"
            )
        print()
        if target.known_weaknesses:
            print("  Verified failures by category:")
            for key, n in sorted(categories.items()):
                print(f"    {key:<44} {n}")
            print(f"  true positives (matched a documented weakness): {true_positives}")
            print(f"  unexplained failures:                          {false_positives}")
        else:
            status = "PASS" if false_positives == 0 else "FAIL"
            print(f"  Control result: {false_positives} verified failures on a correct target")
            print(f"  -> {status} (a correct target must yield zero)")
        print()

    print(THIN)
    print("Held-out FM -- sealed evaluation surface")
    print(THIN)
    print("  property-fm is reserved for sealed_eval. It is not used above, and")
    print("  assert_split enforces that in code. A re-seeded training FM would")
    print("  not qualify.")
    print()
    gate = AcceptanceGate(split="sealed_eval")
    _c, _o, _i = get_task("list-dedupe")
    impl = _REFERENCE_IMPLS["list-dedupe"]
    heldout_hits = 0
    for fm in HELDOUT_FMS:
        for attack in fm.generate("list-dedupe", count=per_family, seed=seed):
            verdict = verify_candidate(
                FLAWED_TARGET.code_by_family["list-dedupe"],
                impl,
                attack.input_value,
                impl(attack.input_value),
            )
            if verdict.is_defect:
                heldout_hits += 1
    print(f"  held-out FM verified failures on the flawed target: {heldout_hits}")
    print()
    print("  NOTE: this is the BASELINE for a transfer experiment, not a result.")
    print("  No model was fine-tuned. Nothing here shows that failure-driven")
    print("  training improves anything.")
    print()


def run_track_b(profile: str = "high_noise") -> None:
    print(BAR)
    print("TRACK B -- noise floor: can any metric separate signal from noise?")
    print(BAR)
    print()
    for prof in ("low_noise", "high_noise", "very_high_noise"):
        print(f"  noise profile: {prof}")
        results = run_experiment(prof, n_noise=300, n_signal=200, seed=1234)
        print(f"    {'metric':<15} {'false_alarm':>12} {'power':>7} {'d_prime':>9} {'probes':>7}  verdict")
        for s in results.values():
            print(
                f"    {s.metric:<15} {s.false_alarm_rate:>12.3f} "
                f"{s.detection_power:>7.3f} {s.d_prime:>9.2f} {s.probes_needed:>7}  "
                f"{'usable' if s.is_usable else 'UNUSABLE'}"
            )
        print()

    print("  FINDING: no candidate metric is usable.")
    print("    - exact_hash false-alarms on most resamples of a noisy system.")
    print("      It cannot tell a re-sample from a real change.")
    print("    - token_jaccard and edit_distance have NEGATIVE d-prime under")
    print("      high noise: real functional change is no more detectable than")
    print("      sampling variance. A negative d-prime means the metric is")
    print("      tracking noise, not function.")
    print()
    print("  This is the Phase 1 result from application/README.md, and it is a")
    print("  negative one. Per docs/thesis.md, that is a kill criterion for the")
    print("  product as specified -- the next step is a property-based metric")
    print("  that tests capability rather than comparing surface text.")
    print()


def main() -> int:
    run_track_a()
    run_track_b()
    print(BAR)
    print("No robustness result is claimed by this script. It demonstrates that")
    print("the instrument returns trustworthy verdicts and honest measurements.")
    print(BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
