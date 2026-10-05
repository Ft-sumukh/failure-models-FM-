"""The FM pool: adversarial generators that propose, but never certify.

An FM's job is to find inputs a target is likely to mishandle. It has no
authority over whether a failure is real -- the verifier does. Every FM is
versioned, because "held-out attacker" is a claim about identity and a renamed
FM defeats it.

The held-out split is enforced in code here, not only in the manifest. A
documented rule that nothing checks is a comment.
"""

from __future__ import annotations

import inspect
import random
from dataclasses import dataclass
from typing import Any, Protocol

from .tasks import _REFERENCE_IMPLS, get_task


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


#: Families whose contract takes more than the list argument. An FM that
#: generates a bare list for these produces a probe that cannot even call the
#: function, which the verifier would report as a failure and the corpus would
#: record as a target defect. Both would be wrong.
MULTI_ARG_FAMILIES = {"clamp-list", "rotate-list", "chunk-sum"}


def _boundary_pool(family: str, seeded: list[Any]) -> list[Any]:
    """Edge-case inputs appropriate to the family's signature.

    For a multi-argument family every entry is a full positional-argument
    list. A bare list here produces a probe the oracle cannot even call, which
    is a broken probe -- and a first cut of this returned ``[[], [0], [1]]``
    for every family, silently invalidating a third of the hard-task probes.
    """
    if family not in MULTI_ARG_FAMILIES:
        return [[], [0], [1]]

    if family == "clamp-list":
        return [
            [[], 0, 10],            # empty list
            [[5], 5, 5],            # degenerate bounds, single element
            [[5], 0, 0],            # lo == hi
            [[-1, 0, 1], 1, -1],    # lo > hi: contract-specified precedence
            [[0, 10], 0, 10],       # both bounds already satisfied
            [[-100, 100], -10, 10], # both bounds violated
            [[3, 1, 4, 1, 5], 1, 4],  # duplicates and interior values
        ]
    if family == "rotate-list":
        return [
            [[], 1],                # empty list with a non-zero rotation
            [[], 0],                # empty list, zero rotation
            [[1, 2, 3], 0],         # zero rotation
            [[1, 2, 3], 3],         # exact multiple
            [[1, 2, 3], -1],        # negative rotation
            [[1], 5],               # single element, k > len
            [[1, 2, 3, 4, 5], 7],   # k well beyond length
        ]
    # chunk-sum
    return [
        [[], 1],                  # empty list
        [[], 3],
        [[1, 2], 5],              # single short chunk
        [[1, 2, 3], 3],           # exact division
        [[1, 2, 3, 4], 3],        # short tail
        [[1, 2, 3], 1],           # chunks of one
        [[1, 2, 3], 100],         # chunk larger than input
    ]


def _copy(value: Any) -> Any:
    """Deep-ish copy of a probe value.

    Probes nest arbitrarily (a list of arguments, one of which is itself a
    list of ints). A flat ``list(...)`` copy raises on a bare int element,
    which is exactly what the single-argument families store.
    """
    if isinstance(value, list):
        return [_copy(v) for v in value]
    return value


def _pool_for(family: str, seeded: list[Any], rng: random.Random) -> list[Any]:
    """The FM's probe pool, with seeded inputs appended and shuffled.

    Shared by every FM so that a family with arity > 1 never falls back to the
    single-argument ``[[], [0], [1]]`` pool.
    """
    pool: list[Any] = [_copy(p) for p in _boundary_pool(family, seeded)]
    for item in seeded:
        if family in MULTI_ARG_FAMILIES and len(item) == 1:
            # A one-element list for a multi-argument family is a broken
            # probe, not an edge case. Skipped rather than padded.
            continue
        pool.append(_copy(item))
    rng.shuffle(pool)
    return pool


def _random_probe(family: str, rng: random.Random) -> Any:
    if family == "clamp-list":
        size = rng.choice([0, 1, 2, 3, 5, 8])
        lo = rng.randint(-10, 10)
        hi = rng.randint(-10, 10)
        return [[rng.randint(-30, 30) for _ in range(size)], lo, hi]
    if family == "rotate-list":
        size = rng.choice([0, 1, 2, 3, 5, 7])
        k = rng.choice([-3, -1, 0, 1, 2, 3, 7, 13])
        return [[rng.randint(-20, 20) for _ in range(size)], k]
    if family == "chunk-sum":
        size = rng.choice([1, 2, 3, 4, 5, 7])
        n = rng.choice([0, 1, 2, 3, 4, 6, 7, 9])
        return [[rng.randint(-15, 15) for _ in range(n)], size]

    size = rng.choice([2, 3, 5, 8, 17])
    return [rng.randint(-10, 10) for _ in range(size)]


class BoundaryFM:
    """Empty, singleton, and extreme-size cases. The classic blind spot."""

    fm_id = "boundary-fm"
    fm_version = "0.3.1"

    def generate(self, family: str, count: int, seed: int) -> list[Attack]:
        _contract, _oracle, seeded = get_task(family)
        rng = random.Random(seed)
        pool = _pool_for(family, seeded, rng)

        picks: list[Any] = []
        for i in range(count):
            if i < len(pool):
                picks.append(pool[i])
            else:
                picks.append(_random_probe(family, rng))

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
        _contract, _oracle, seeded = get_task(family)
        rng = random.Random(seed)
        pool = _pool_for(family, seeded, rng)

        picks: list[Any] = []
        for i in range(count):
            if i < len(pool):
                picks.append(pool[i])
            else:
                size = rng.choice([2, 3, 5, 8])
                picks.append([rng.choice([0, 1, -1, 2, -2]) for _ in range(size)])

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
        _contract, _oracle, seeded = get_task(family)
        multi = family in MULTI_ARG_FAMILIES
        picks: list[tuple[str, Any]] = []

        # The clause list describes the *list argument*, not the full call.
        # For a multi-argument family each clause has to be paired with valid
        # values for the remaining parameters, or the probe cannot be called
        # and the verifier scores an uncallable probe as a model defect. This
        # FM is the held-out attacker, so an arity bug here would corrupt the
        # transfer measurement rather than just the corpus.
        scalars: list[Any] = []
        for item in seeded:
            if isinstance(item, list) and len(item) >= 2:
                scalars.extend(item[1:])
        if not scalars:
            scalars = [0, 1, 2, -1]

        def wrap(values: list[int]) -> Any:
            if not multi:
                return list(values)
            arity = len(inspect.signature(_REFERENCE_IMPLS[family]).parameters)
            tail = [scalars[(len(picks) + i) % len(scalars)] for i in range(arity - 1)]
            return [list(values)] + tail

        for clause, fixed in self._CLAUSES:
            if picks and len(picks) >= count:
                break
            if fixed is not None:
                picks.append((clause, wrap(list(fixed))))
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
            picks.append((clause, wrap(vals)))

        while len(picks) < count:
            size = rng.choice([2, 3, 6])
            vals = [rng.randint(-30, 30) for _ in range(size)]
            picks.append((f"random-{len(picks)}", wrap(vals)))

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
            if i < len(seeded):
                picks.append(_copy(seeded[i]))
            else:
                picks.append(_random_probe(family, rng))

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
