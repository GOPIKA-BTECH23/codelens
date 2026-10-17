"""Which finding categories count as "the same kind of issue" as a label (evals/categories.json).

Models name issues freely (`sqli`, `sql-injection`, `injection`), so exact string equality
would under-count real hits. An open-ended fuzzy match would over-count. The compromise is a
small, hand-reviewed table: a finding is compatible with a label when its category is
  * the label's family name or one of that family's aliases, or
  * an umbrella term (e.g. `injection`) that lists the label's family.
Everything else is incompatible, including categories the table does not know; the report
lists those so a missing alias is visible instead of silently costing recall.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

CATEGORIES_FILE = Path(__file__).resolve().parents[1] / "categories.json"


def normalise(category: str) -> str:
    """The same normalisation the Finding schema applies (lowercase kebab-case)."""
    return "-".join(category.lower().replace("_", " ").split())


class CategoryTableError(ValueError):
    pass


@dataclass(frozen=True)
class CategoryTable:
    families: dict[str, frozenset[str]]  # family -> every accepted name (itself included)
    umbrellas: dict[str, frozenset[str]]  # umbrella term -> families it covers
    sha256: str  # of the file, recorded with every result (matching depends on it)

    def family_of(self, category: str) -> str | None:
        name = normalise(category)
        return next((fam for fam, names in self.families.items() if name in names), None)

    def is_known(self, category: str) -> bool:
        return self.family_of(category) is not None or normalise(category) in self.umbrellas

    def compatible(self, predicted: str, label: str) -> bool:
        if self.family_of(predicted) == label:
            return True
        return label in self.umbrellas.get(normalise(predicted), frozenset())


def load_categories(path: Path = CATEGORIES_FILE) -> CategoryTable:
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    data = json.loads(raw)
    families: dict[str, frozenset[str]] = {}
    seen: dict[str, str] = {}
    for family, aliases in data["families"].items():
        names = frozenset({family, *(normalise(a) for a in aliases)})
        for name in names:
            if name in seen:
                raise CategoryTableError(f"'{name}' is listed under {seen[name]} and {family}")
            seen[name] = family
        families[family] = names
    umbrellas: dict[str, frozenset[str]] = {}
    for term, members in data.get("umbrellas", {}).items():
        unknown = [m for m in members if m not in families]
        if unknown or term in seen:
            raise CategoryTableError(f"umbrella '{term}' is invalid (unknown: {unknown})")
        umbrellas[term] = frozenset(members)
    return CategoryTable(families, umbrellas, hashlib.sha256(raw).hexdigest()[:12])
