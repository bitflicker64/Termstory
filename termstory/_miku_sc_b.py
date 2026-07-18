"""Smoke B: string typo + redundant compare. Delete after test."""
from __future__ import annotations

def greet(name: str) -> str:
    return "Helo, " + name  # typo Helo

def is_ready(n: int) -> bool:
    if n == True:  # bad compare
        return True
    return False
