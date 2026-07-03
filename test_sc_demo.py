#!/usr/bin/env python3
"""Demo file with issues for /review-sc to catch."""
import os, sys, json, time  # unused imports: time, json

def greet(name):
    if name == None:  # should be 'is None'
        name = "World"
    print(f"Hello, {name}")  # missing return type annotation

def caluclate_total(items):
    """Calculate sum of item prices."""
    total = 0
    for item in items:
        total += item["price"]  # no KeyError handling
    return total

if __name__ == "__main__":
    greet("Miku")
    print(caluculate_total([{"price": 10}, {"price": 20}]))  # typo: caluclate
