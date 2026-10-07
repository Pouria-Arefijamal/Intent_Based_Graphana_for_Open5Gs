"""Loads catalog.yaml: recipes (vetted PromQL) and the static metric allow-list."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")


@dataclass(frozen=True)
class Recipe:
    id: str
    title: str
    unit: str
    description: str
    promql: str
    kind: str = "timeseries"

    def public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "unit": self.unit,
            "description": self.description,
            "promql": self.promql,
        }


@dataclass(frozen=True)
class Catalog:
    recipes: dict[str, Recipe]
    metrics: tuple[dict, ...]

    @property
    def metric_names(self) -> frozenset[str]:
        return frozenset(m["name"] for m in self.metrics)


def load_catalog(path: Path | str | None = None) -> Catalog:
    raw = yaml.safe_load(Path(path or CATALOG_PATH).read_text(encoding="utf-8"))
    recipes: dict[str, Recipe] = {}
    for item in raw["recipes"]:
        recipe = Recipe(
            id=item["id"],
            title=item["title"],
            unit=item["unit"],
            description=item["description"],
            promql=item["promql"],
            kind=item.get("kind", "timeseries"),
        )
        if recipe.id in recipes:
            raise ValueError(f"duplicate recipe id {recipe.id}")
        recipes[recipe.id] = recipe
    metrics = tuple({"name": m["name"], "description": m["description"]} for m in raw["metrics"])
    return Catalog(recipes=recipes, metrics=metrics)


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    return load_catalog()
