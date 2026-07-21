#!/usr/bin/env python3
"""Test entry point for windows-agent-compat.

Runs every test module in tests/ and aggregates results. Currently:
  - test_recovery_rules.py : data-driven recovery-rule regression + anti-interference net
  - test_adapter_rules.py  : data-driven command-translation regression + anti-shadowing net

Add new test modules here as the suite grows. Exit 0 = all green, 1 = any failure.
"""
from __future__ import annotations

import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TEST_MODULES = [
    "test_recovery_rules",
    "test_adapter_rules",
]


def main() -> int:
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    overall = 0
    for name in TEST_MODULES:
        mod = importlib.import_module(name)
        print(f"=== {name} ===")
        rc = mod.main()
        overall |= rc
        print()
    print("ALL GREEN" if overall == 0 else "FAILURES PRESENT")
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
