#!/usr/bin/env python3
"""Command-translation self-test harness for cmd_adapter.py.

Data-driven regression net over tests/adapter_fixtures.json. The dominant risk
for translation rules is *shadowing* / ordering: a broader rule silently
capturing input meant for a more specific one (e.g. bare `cat` swallowing
`cat | grep`, `echo literal` swallowing `echo $VAR`, `ls` vs `ls simple` vs
`ls bare`). This harness asserts, for every fixture:

  1. routing  -- the input matches the intended rule (expect_rule), or falls
     back when expect_rule is null (anti-shadowing net)
  2. output   -- exact translated string per shell (outputs.{pwsh,cmd,bash})
  3. coverage -- every rule in config/adapters.json has at least one fixture,
     so a newly added translation rule cannot land without a test

Exit 0 = all green, 1 = any failure. No third-party deps.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

from cmd_adapter import CommandTranslator  # noqa: E402

FIXTURES = os.path.join(HERE, "adapter_fixtures.json")


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def ok(self) -> None:
        self.passed += 1

    def fail(self, msg: str) -> None:
        self.failed += 1
        self.failures.append(msg)


def test_coverage(fx: dict, tr: CommandTranslator, r: Results) -> None:
    """Every rule in adapters.json must be exercised by at least one fixture."""
    registered = set(tr.list_rules())
    exercised = {c["expect_rule"] for c in fx["cases"] if c.get("expect_rule")}
    exercised |= {c["expect_rule"] for c in fx.get("routing_only", []) if c.get("expect_rule")}
    missing = registered - exercised
    if missing:
        r.fail(f"COVERAGE: rules without fixtures: {sorted(missing)}")
    else:
        r.ok()


def test_cases(fx: dict, tr: CommandTranslator, r: Results) -> None:
    for c in fx["cases"]:
        cmd = c["in"]
        # routing (default shell pwsh is fine; matched_rule is shell-independent)
        res = tr.translate(cmd, shell="pwsh")
        if res["matched_rule"] != c["expect_rule"]:
            r.fail(f"ROUTING {cmd!r}: expected rule {c['expect_rule']!r} got {res['matched_rule']!r}")
        elif res["fallback"] != c.get("expect_fallback", False):
            r.fail(f"FALLBACK {cmd!r}: expected fallback={c.get('expect_fallback', False)} got {res['fallback']}")
        else:
            r.ok()

        # per-shell exact output
        for shell, expected in c.get("outputs", {}).items():
            got = tr.translate(cmd, shell=shell)["translated"]
            if got == expected:
                r.ok()
            else:
                r.fail(f"OUTPUT {cmd!r} [{shell}]:\n      expected: {expected!r}\n      got:      {got!r}")


def test_routing_only(fx: dict, tr: CommandTranslator, r: Results) -> None:
    for c in fx.get("routing_only", []):
        res = tr.translate(c["in"], shell="pwsh")
        if res["matched_rule"] == c["expect_rule"]:
            r.ok()
        else:
            r.fail(f"ROUTING {c['in']!r}: expected {c['expect_rule']!r} got {res['matched_rule']!r} "
                   f"({c.get('_why', '')})")


def main() -> int:
    with open(FIXTURES, encoding="utf-8") as f:
        fx = json.load(f)

    tr = CommandTranslator()
    r = Results()

    test_coverage(fx, tr, r)
    test_cases(fx, tr, r)
    test_routing_only(fx, tr, r)

    print(f"Adapter rule harness: {r.passed} passed, {r.failed} failed "
          f"({len(tr.list_rules())} rules registered)")
    if r.failures:
        print("\nFailures:")
        for msg in r.failures:
            print(f"  - {msg}")
        return 1
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    raise SystemExit(main())
