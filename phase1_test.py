#!/usr/bin/env python3
"""Phase 1 test fixture for /review-sc + prompt extraction verification."""
import os, sys, json, time  # time, json unused

def greet(name):
    """Greet someone by name."""
    if name == None:  # use is None
        name = "World"
    print(f"Hello, {name}")  # missing type annotation

def calculate_total(items):
    total = 0
    for item in items:
        total += item["price"]  # no KeyError handling
    return total

if __name__ == "__main__":
    items = [{"price": 10}, {"price": 20}]
    print(calculate_total(items))
