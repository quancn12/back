"""F2–F4: exact autoregressive joint enumeration and Dirichlet fitting."""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, field


def assignments(needs: int):
    if not 1 <= needs <= 4:
        raise ValueError("J must be in [1,4]")
    return tuple((c, d) for c in itertools.product(range(2), repeat=needs)
                 for d in itertools.product(range(3), repeat=needs))


def row_key(features, parents):
    return json.dumps([sorted(features.items()), list(parents)], separators=(",", ":"))


def posterior_row(counts, alpha):
    if len(counts) != len(alpha) or any(not math.isfinite(a) or a <= 0 for a in alpha):
        raise ValueError("Invalid Dirichlet prior")
    if any(not math.isfinite(n) or n < 0 for n in counts):
        raise ValueError("Invalid counts")
    total = math.fsum(n + a for n, a in zip(counts, alpha))
    return tuple((n + a) / total for n, a in zip(counts, alpha))


@dataclass
class C01Model:
    needs: int
    # Explicit pooled priors for each variable; absent context rows use these.
    priors: dict[str, list[float]]
    counts: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    def validate(self):
        assignments(self.needs)
        for kind, size in (("C", 2), ("D", 3)):
            for j in range(self.needs):
                name = f"{kind}{j}"
                alpha = self.priors[name]
                if len(alpha) != size:
                    raise ValueError("CPT cardinality mismatch")
                posterior_row([0] * size, alpha)
                for counts in self.counts.get(name, {}).values():
                    posterior_row(counts, alpha)

    def probability(self, name, value, features, parents):
        alpha = self.priors[name]
        counts = self.counts.get(name, {}).get(row_key(features, parents), [0] * len(alpha))
        return posterior_row(counts, alpha)[value]

    def joint(self, features):
        self.validate()
        probabilities = []
        for c, d in assignments(self.needs):
            factors = [self.probability(f"C{j}", c[j], features, c[:j]) for j in range(self.needs)]
            factors += [self.probability(f"D{j}", d[j], features, c + d[:j]) for j in range(self.needs)]
            probabilities.append(math.prod(factors))
        if not math.isclose(math.fsum(probabilities), 1, abs_tol=1e-10):
            raise ValueError("Joint not normalized")
        return tuple(probabilities)


def marginals(joint, needs):
    return tuple(math.fsum(p for p, (_, d) in zip(joint, assignments(needs)) if d[j] == 1)
                 for j in range(needs))


def fit_c01(rows, needs, priors):
    """Caller supplies eligible TRAIN checkpoints only. Missing labels are skipped."""
    model = C01Model(needs, priors)
    model.validate()
    seen = set()
    for row in rows:
        if row["split"] != "train" or row["trip_day_id"] in seen:
            raise ValueError("TRAIN only; one preselected checkpoint per trip-day")
        seen.add(row["trip_day_id"])
        c, d, x = tuple(row["C"]), tuple(row["D"]), row["features"]
        if len(c) != needs or len(d) != needs:
            raise ValueError("Label shape mismatch")
        for kind, vals, size in (("C", c, 2), ("D", d, 3)):
            for j in range(needs):
                parents = c[:j] if kind == "C" else c + d[:j]
                if vals[j] is None or None in parents:
                    continue
                if any(v not in (0, 1) for v in c if v is not None) or any(v not in (0, 1, 2) for v in d if v is not None):
                    raise ValueError("Invalid labels")
                name = f"{kind}{j}"
                counts = model.counts.setdefault(name, {}).setdefault(row_key(x, parents), [0] * size)
                counts[vals[j]] += 1
    return model
