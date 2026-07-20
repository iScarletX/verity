"""OWASP Agentic Skills Top 10 (AST10) — controlled category identifiers.

Source: OWASP AST10 (Public Review). Categories are stable ids used by
Verity Skill rules to declare which threat class they cover. See the
reuse decision table for context. Only the categories currently in scope
for Verity are enumerated here; adding a new one is a deliberate change.

We do NOT claim 10-of-10 coverage; the `coverage_matrix` function below
returns the honest picture (which categories have deterministic rules and
which are still gaps).
"""

from __future__ import annotations

from typing import Dict, List, Sequence


OWASP_AST10: Dict[str, str] = {
    "OWASP-AST01": "Malicious Skill code / dangerous runtime behaviour",
    "OWASP-AST02": "Skill supply-chain risk",
    "OWASP-AST03": "Excessive / over-broad Skill authorisation",
    "OWASP-AST04": "Insecure Skill metadata",
    "OWASP-AST05": "Untrusted external instructions",
    "OWASP-AST06": "Weak Skill isolation",
    "OWASP-AST07": "Skill update drift / integrity",
    "OWASP-AST08": "Insufficient Skill scanning capability",
    "OWASP-AST09": "Lack of Skill governance",
    "OWASP-AST10": "Cross-platform Skill reuse risk",
}


def coverage_matrix(rules: Sequence) -> Dict[str, Dict]:
    """Given the current skill rule set, return per-category status:

        { "OWASP-AST01": {"title": ..., "rules": ["skill.x", ...], "status": "partial" | "none" } , ... }

    ``status`` is "partial" when at least one rule maps to the category and
    "none" otherwise. We deliberately never emit "full": no reasonable
    definition of "full coverage" of an OWASP category exists.
    """
    matrix: Dict[str, Dict] = {}
    for code, title in OWASP_AST10.items():
        matched = [r.ruleId for r in rules
                   if code in getattr(r, "owaspAst10", [])]
        matrix[code] = {
            "title": title,
            "rules": matched,
            "status": "partial" if matched else "none",
        }
    return matrix
