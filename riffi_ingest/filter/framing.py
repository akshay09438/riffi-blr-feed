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


def advice_problem(category: str, facts: Facts | None, cfg: FilterConfig) -> str:
    """'' when the wording is fine; otherwise a plain sentence saying which text reads as advice."""
    block = cfg["framing"]
    if facts is None or category not in block["advice_categories"]:
        return ""
    pattern = _pattern(tuple(block["advice_patterns"]))
    if pattern is None:
        return ""
    for name in ("side_a", "side_b", "hook", "why_care"):
        match = pattern.search(getattr(facts, name))
        if match:
            return f'its {name.replace("_", " ")} reads as investment advice ("{match.group(0).strip()}")'
    return ""
