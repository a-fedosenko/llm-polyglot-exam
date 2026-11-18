"""Content specifications: what to say, and the checklist that grades it."""
from __future__ import annotations

import json
import pathlib

from pydantic import BaseModel

from polyglot.paths import DIALECT_SPECS as DIALECT_SPECS_PATH
from polyglot.paths import SPECS as SPECS_PATH


class Spec(BaseModel):
    id: str
    scenario: str
    facts: list[str]


def load_dialect_specs() -> list[Spec]:
    """Scenarios for the dialect battery, falling back to the capability specs.

    Falls back rather than failing: a fresh clone with no `dialect.json` should
    still run a battery, and the capability scenarios are a worse choice rather
    than an impossible one. `bus-rain-office` carries exactly one optional
    Serbian yat reflex, which is how a family came to be measured on a sentence
    containing almost nothing its varieties disagree about.
    """
    if DIALECT_SPECS_PATH.is_file():
        return load_specs(DIALECT_SPECS_PATH)
    return load_specs()


def load_specs(path: pathlib.Path | None = None) -> list[Spec]:
    raw = json.loads((path or SPECS_PATH).read_text(encoding="utf-8"))
    return [Spec.model_validate(s) for s in raw["specs"]]
