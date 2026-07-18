"""Smoke A: unused imports + NameError typo. Delete after test."""
from __future__ import annotations
import os
import json  # unused on purpose

def add_scores(xs: list[int] | None) -> int:
    total = 0
    for x in xs:  # None blows up
        total += x
    return totall  # typo
