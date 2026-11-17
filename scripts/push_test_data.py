#!/usr/bin/env python3
"""Send test scenarios to /api/evaluate and compare the result with "expected".

Start the dashboard first (python3 web/local_server.py), then:
  python3 scripts/push_test_data.py --all
  python3 scripts/push_test_data.py --scenario cold_snap --set weather.temp_c=15
  python3 scripts/push_test_data.py --base https://<deployment>.vercel.app --all

--set KEY=VALUE overrides one field (dotted keys reach nested fields; VALUE is
parsed as JSON when it can be, so 15, null, [0, 60] and true keep their type).
With --set, only the overall status and rules are printed, not compared.
Exits with 1 when a request fails or a result differs from the expectation.
"""
import argparse
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = ROOT / "config" / "scenarios.json"


def load_scenarios():
    with SCENARIOS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_overrides(items):
    overrides = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--set expects KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        try:
            overrides[key] = json.loads(value)
        except json.JSONDecodeError:
            overrides[key] = value
    return overrides


def mismatches(expected, rules):
    statuses = {rule["id"]: rule["status"] for rule in rules.get("results", [])}
    found = []
    for key, value in expected.items():
        actual = rules.get("overall_status") if key == "overall_status" else statuses.get(key)
        if actual != value:
            found.append(f"{key}: expected {value}, got {actual}")
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run test scenarios against /api/evaluate")
    parser.add_argument("--base", default="http://localhost:8765", help="dashboard URL")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="run every scenario")
    group.add_argument("--scenario", default="baseline_optimal")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)

    scenarios = load_scenarios()
    names = list(scenarios) if args.all else [args.scenario]
    overrides = parse_overrides(args.set)
    failures = 0
    for name in names:
        try:
            response = requests.post(f"{args.base}/api/evaluate",
                                     json={"scenario": name, "overrides": overrides}, timeout=30)
        except requests.RequestException as exc:
            print(f"{name}: cannot reach {args.base} ({exc})")
            return 1
        if not response.ok:
            print(f"{name}: ERROR {response.status_code} {response.text[:200]}")
            failures += 1
            continue
        rules = response.json().get("rules", {})
        statuses = {rule["id"]: rule["status"] for rule in rules.get("results", [])}
        print(f"{name} | overall={rules.get('overall_status')} | stage={rules.get('stage')} | {statuses}")
        if overrides or name not in scenarios:
            continue
        for problem in mismatches(scenarios[name].get("expected", {}), rules):
            print(f"  mismatch: {problem}")
            failures += 1
    print("OK" if not failures else f"{failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
