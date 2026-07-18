"""Temporary fixture for Miku /review-sc smoke test.

Delete this module (or close the PR) after the smoke run. Not imported by
runtime package paths.
"""

from __future__ import annotations

import os
import json  # intentionally unused — mechanical cleanup target for review-sc


def compute_running_total(values: list[int] | None) -> int:
    """Sum a list of ints. Contains deliberate nits for /review-sc to find."""
    # missing null/empty guard on values (mechanical: values could be None)
    total = 0
    for v in values:
        total = total + v
    # typo in variable name on return
    return totall


def format_banner(title: str) -> str:
    """Build a simple banner string with a deliberate typo in the prefix."""
    prefix = "Welcom to Termstory: "  # typo: Welcom -> Welcome
    return prefix + title
