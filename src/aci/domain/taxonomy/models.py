"""Faceted taxonomy schema (plan §9).

Facets are independent dimensions (not one tree); a hierarchy may exist inside
a single facet value ("software-engineering/debugging"). Versions carry facets
as JSONB; this module is the single validation vocabulary.
"""

import re

from pydantic import BaseModel

KNOWN_FACETS: frozenset[str] = frozenset(
    {
        "domain",
        "task_type",
        "lifecycle_phase",
        "technology",
        "concern",
        "artifact_type",
        "risk_class",
    }
)

_FACET_VALUE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)?$")


class FacetSchema(BaseModel):
    """Validation vocabulary for capability facets."""

    known_facets: frozenset[str] = KNOWN_FACETS

    def check(self, facets: dict[str, list[str]]) -> None:
        for name, values in facets.items():
            if name not in self.known_facets:
                raise ValueError(
                    f"unknown facet {name!r}; known facets: {sorted(self.known_facets)}"
                )
            if not isinstance(values, list) or not values:
                raise ValueError(f"facet {name!r} must be a non-empty list")
            for value in values:
                if not isinstance(value, str) or not _FACET_VALUE.match(value):
                    raise ValueError(
                        f"invalid facet value {value!r} for {name!r}; "
                        "expected slug like 'root-cause-analysis' or 'a/b'"
                    )


def validate_facets(facets: dict[str, list[str]]) -> None:
    FacetSchema().check(facets)
