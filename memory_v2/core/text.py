"""Term sets and overlap, used by content resonance."""

from __future__ import annotations

import math
import re

_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def terms(text: str) -> set:
    text = (text or "").lower()
    out = set(_ASCII_WORD.findall(text))
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            out.add(run)
        for index in range(len(run) - 1):
            out.add(run[index:index + 2])
    return out


def idf(term_sets: list) -> dict:
    total = len(term_sets)
    frequency = {}
    for item in term_sets:
        for term in item:
            frequency[term] = frequency.get(term, 0) + 1
    return {term: math.log((total + 1.0) / (count + 0.5))
            for term, count in frequency.items()}


def match_score(query: set, target: set, weights: dict) -> float:
    return sum(weights.get(term, 1.0) for term in (query & target))
