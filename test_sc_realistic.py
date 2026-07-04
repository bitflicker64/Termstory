#!/usr/bin/env python3
"""Module with realistic issues including items[0] patterns."""
import os, sys, json  # json is unused

def get_first(items):
    return items[0]  # no bounds check

def process(data):
    if data == None:  # should use is None
        return None
    result = {}
    for i in range(len(data)):  # pythonic: for d in data
        result[i] = data[i] * 2
    return result

if __name__ == "__main__":
    items = [1, 2, 3]
    print(get_first(items))
