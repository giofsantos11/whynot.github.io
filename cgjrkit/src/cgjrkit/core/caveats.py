"""Caveats attached to data, results and charts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Severity = Literal["info", "warn", "block"]


@dataclass(frozen=True)
class Caveat:
    """A data or method caveat.

    ``block`` means the output must not be used as is (for example mixing
    2017 and 2021 PPP series); ``warn`` should be footnoted; ``info`` is
    context for the analyst.
    """

    code: str
    severity: Severity
    message: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def worst(caveats: list[Caveat]) -> Severity | None:
    """Return the most severe level present, or None."""
    order = {"info": 0, "warn": 1, "block": 2}
    if not caveats:
        return None
    return max(caveats, key=lambda c: order[c.severity]).severity
