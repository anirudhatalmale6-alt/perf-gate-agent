"""Deterministic performance detectors for SQL files."""

from __future__ import annotations

import re
from typing import List

from .base import Finding


def analyze(path: str, source: str) -> List[Finding]:
    findings: List[Finding] = []
    for i, raw in enumerate(source.split("\n"), start=1):
        s = raw.strip()
        if not s or s.startswith("--"):
            continue
        low = s.lower()

        if re.search(r"select\s+\*", low):
            findings.append(Finding(
                "sql.select_star", "SELECT *", "NETWORK", "MEDIUM", path, i, s,
                "SELECT * reads and ships every column, prevents covering indexes and breaks "
                "when the schema changes.",
                "List only the columns you need.",
            ))

        # Leading wildcard defeats the index.
        if re.search(r"like\s+'%", low):
            findings.append(Finding(
                "sql.leading_wildcard", "LIKE with a leading wildcard", "IO_DB", "HIGH", path, i, s,
                "A leading % means the index can't be used - the engine full-scans the table.",
                "Anchor the pattern ('abc%'), or use a full-text / trigram index for "
                "contains-search.",
            ))

        # Function wrapped around a column in WHERE kills index usage.
        if re.search(r"where\s+\w*\(?\s*(upper|lower|year|month|date|cast|convert)\s*\(", low) \
                or re.search(r"(upper|lower|year|month|date)\s*\(\s*\w+\s*\)\s*=", low):
            findings.append(Finding(
                "sql.function_on_column", "Function applied to an indexed column", "IO_DB", "HIGH",
                path, i, s,
                "Wrapping a column in a function makes the predicate non-sargable, so the index "
                "on that column can't be used.",
                "Rewrite so the column is bare (e.g. date range instead of YEAR(col)=), or add a "
                "function-based/computed-column index.",
            ))

        # Cartesian join: comma-style join with no matching WHERE join predicate is risky.
        if re.search(r"\bfrom\s+\w+\s*,\s*\w+", low) and " join " not in low:
            findings.append(Finding(
                "sql.cartesian_join", "Comma join (possible cartesian product)", "ALGORITHMIC",
                "HIGH", path, i, s,
                "Comma-separated tables without a join predicate produce a cartesian product - "
                "rows multiply and the query explodes.",
                "Use explicit INNER/LEFT JOIN ... ON with the join keys.",
            ))

        # Correlated subquery hint.
        if re.search(r"where.*\(\s*select", low):
            findings.append(Finding(
                "sql.correlated_subquery", "Possible correlated subquery", "IO_DB", "MEDIUM",
                path, i, s,
                "A subquery re-evaluated per outer row runs N times; on large tables it dominates "
                "runtime.",
                "Rewrite as a JOIN or a single aggregated/derived table where possible.",
            ))
    return findings
