"""Finance and markets are framed as a debate about the system or the news (fair? overhyped? who loses?),
never as buy / sell / hold advice (config/prompts/ai_pass.md, D-017). This is the engine's second line: a pick in
a finance category whose sides, hook or reason read as advice is not picked, whatever the AI pass wrote."""

from __future__ import annotations

import re
from functools import lru_cache

from .config import FilterConfig
from .facts import Facts


@lru_cache(maxsize=8)
def _pattern(patterns: tuple[str, ...]) -> re.Pattern | None:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None


def advice_problem(category: str, facts: Facts | None, cfg: FilterConfig, headline: str = "") -> str:
    """'' when the wording is fine; otherwise a plain sentence saying which text reads as advice. The headline is
    checked in every category (an advice headline such as "Should you buy stocks now or wait?" can sit in any
    category); the AI's sides, hook and reason only in the finance categories."""
    block = cfg["framing"]
    pattern = _pattern(tuple(block["advice_patterns"]))
    if pattern is None:
        return ""
    match = pattern.search(headline or "")
    if match:
        return f'its headline reads as investment advice ("{match.group(0).strip()}")'
    if facts is None or category not in block["advice_categories"]:
        return ""
    for name in ("side_a", "side_b", "hook", "why_care"):
        match = pattern.search(getattr(facts, name))
        if match:
            return f'its {name.replace("_", " ")} reads as investment advice ("{match.group(0).strip()}")'
    return ""
