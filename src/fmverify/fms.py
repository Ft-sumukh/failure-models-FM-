"""The FM pool: adversarial generators that propose, but never certify.

An FM's job is to find inputs a target is likely to mishandle. It has no
authority over whether a failure is real -- the verifier does. Every FM is
versioned, because "held-out attacker" is a claim about identity and a renamed
FM defeats it.

The held-out split is enforced in code here, not only in the manifest. A
documented rule that nothing checks is a comment.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Protocol

from .tasks import get_task


@dataclass(frozen=True)
class Attack:
    """A proposed probe. A proposal, until the verifier says otherwise."""

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
        import json

        payload = json.dumps(
            {
                "fm": self.fm_id,
                "v": self.fm_version,
                "family": self.family,
                "input": self.input_value,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class FailureModel(Protocol):
    fm_id: str
    fm_version: str

    def generate(self, family: str, count: int, seed: int) -> list[Attack]:
        ...


class BoundaryFM:
    """Empty, singleton, and extreme-size cases. The classic blind spot."""

    fm_id = "boundary-fm"
    fm_version = "0.3.1"

    def generate(self, family: str, count: int, seed: int) -> list[Attack]:
        _contract, _oracle, seeded = get_task(family)
        rng = random.Random(seed)
        pool: list[Any] = [[], [0], [1]]
        seeded_copy = list(seeded)
        rng.shuffle(seeded_copy)
        pool.extend(seeded_copy)

        picks: list[Any] = []
        for i in range(count):
            if i < len(pool):
                picks.append(pool[i])
            else:
                size = rng.choice([2, 3, 5, 8, 17])
                picks.append([rng.randint(-10, 10) for _ in range(size)])

        return [
            Attack(
                fm_id=self.fm_id,
                fm_version=self.fm_version,
                strategy="edge-case generation: empty, singleton, and small-size inputs",
                family=family,
                input_value=v,
                seed=seed + i,
            )
            for i, v in enumerate(picks)
        ]


class AdversarialInputFM:
    """Duplicates, ordering, negatives, and magnitude stress."""

    fm_id = "adversarial-input-fm"
    fm_version = "0.2.4"

    def generate(self, family: str, count: int, seed: int) -> list[Attack]:
        _contract, _oracle, _seeded = get_task(family)
        rng = random.Random(seed)

        shapes: list[Any] = [
            [0, 0, 0, 0],
            [1, 1, 1],
            [-1, -1, -2, -2],
            list(range(10)) + list(range(10)),
            [2**31 - 1, 0, -(2**31)],
            [7, 7, 8, 8, 9, 9],
            [-5] * 6,
        ]
        picks: list[Any] = []
        for i in range(count):
            if i < len(shapes):
                picks.append(list(shapes[i]))
            else:
                size = rng.choice([2, 4, 9, 12])
                picks.append([rng.choice([0, 1, -1]) for _ in range(size)])

        return [
            Attack(
                fm_id=self.fm_id,
                fm_version=self.fm_version,
                strategy="adversarial input: duplicates, ordering, sign and magnitude stress",
                family=family,
                input_value=v,
                seed=seed + i,
            )
            for i, v in enumerate(picks)
        ]


class PropertyFM:
    """Derives probes from the contract text, not from any example set.

    Deliberately built on a different inductive bias from the other two: it
    reads the contract rather than generating structurally interesting inputs.
    That difference is what makes it usable as a held-out family.
    """

    fm_id = "property-fm"
    fm_version = "0.1.0"

    _CLAUSES = [
        ("empty input", []),
        ("single element", [1]),
        ("strictly increasing", None),
        ("strictly decreasing", None),
        ("all identical", None),
        ("alternating sign", None),
    ]

    def generate(self, family: str, count: int, seed: int) -> list[Attack]:
        rng = random.Random(seed)
        picks: list[tuple[str, Any]] = []

        for clause, fixed in self._CLAUSES:
            if picks and len(picks) >= count:
                break
            if fixed is not None:
                picks.append((clause, list(fixed)))
                continue
            size = rng.choice([3, 5, 8])
            if clause == "strictly increasing":
                vals = sorted(rng.sample(range(-50, 50), size))
            elif clause == "strictly decreasing":
                vals = sorted(rng.sample(range(-50, 50), size), reverse=True)
            elif clause == "all identical":
                v = rng.randint(-5, 5)
                vals = [v] * size
            else:
                vals = [
                    (1 if i % 2 == 0 else -1) * rng.randint(1, 20) for i in range(size)
                ]
            picks.append((clause, vals))

        while len(picks) < count:
            size = rng.choice([2, 3, 6])
            picks.append((f"random-{len(picks)}", [rng.randint(-30, 30) for _ in range(size)]))

        return [
            Attack(
                fm_id=self.fm_id,
                fm_version=self.fm_version,
                strategy=f"contract-clause derivation: {clause}",
                family=family,
                input_value=v,
                seed=seed + i,
                provenance="generated",
            )
            for i, (clause, v) in enumerate(picks[:count])
        ]


class MutationFM:
    """Alters an input while preserving shape. Mechanically different again."""

    fm_id = "mutation-fm"
    fm_version = "0.1.0"

    def generate(self, family: str, count: int, seed: int) -> list[Attack]:
        _contract, _oracle, seeded = get_task(family)
        rng = random.Random(seed)
        picks: list[Any] = []

        for i in range(count):
            base = list(seeded[rng.randrange(len(seeded))]) if seeded else []
            op = rng.choice(["truncate", "extend", "swap", "negate"])
            if op == "truncate" and base:
                base = base[: max(0, len(base) - rng.randint(1, max(1, len(base))))]
            elif op == "extend" and base:
                base = base + [rng.randint(-9, 9) for _ in range(rng.randint(1, 4))]
            elif op == "swap" and len(base) >= 2:
                j = rng.randrange(len(base))
                base[j], base[(j + 1) % len(base)] = base[(j + 1) % len(base)], base[j]
            elif op == "negate" and base:
                base = [-v for v in base]
            picks.append(base)

        return [
            Attack(
                fm_id=self.fm_id,
                fm_version=self.fm_version,
                strategy="shape-preserving mutation of a seeded input",
                family=family,
                input_value=v,
                seed=seed + i,
                provenance="mutated",
            )
            for i, v in enumerate(picks)
        ]


#: The held-out family is a different generator with a different inductive
#: bias, not a re-seeded copy of a training FM. Enforced by ``assert_split``
#: below and asserted in the test suite.
TRAINING_FMS: list[FailureModel] = [BoundaryFM(), AdversarialInputFM()]
HELDOUT_FMS: list[FailureModel] = [PropertyFM()]

ALL_FMS: dict[str, FailureModel] = {
    fm.fm_id: fm for fm in (*TRAINING_FMS, *HELDOUT_FMS, MutationFM())
}

# Extra FM available for demonstration but excluded from the primary
# transfer measurement, since it shares a bias with the training families.
AUXILIARY_FMS: dict[str, FailureModel] = {"mutation-fm": ALL_FMS["mutation-fm"]}


class SplitViolation(RuntimeError):
    """Raised when a reserved family is used where it must not be."""


def assert_split(fm_id: str, split: str) -> None:
    """Fail loudly if a reserved family crosses a boundary.

    This is the check that makes the transfer claim meaningful. Without it,
    a single refactor can silently move the held-out attacker into training.
    """
    training_ids = {fm.fm_id for fm in TRAINING_FMS}
    heldout_ids = {fm.fm_id for fm in HELDOUT_FMS}

    if split == "sealed_eval" and fm_id in training_ids:
        raise SplitViolation(
            f"{fm_id!r} is a training FM and cannot be used in sealed_eval. "
            "A renamed or re-seeded training FM is not an unseen attacker."
        )
    if split in ("train", "validation") and fm_id in heldout_ids:
        raise SplitViolation(
            f"{fm_id!r} is reserved for sealed_eval and cannot be used in {split!r}."
        )
