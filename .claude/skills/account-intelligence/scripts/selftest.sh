#!/usr/bin/env bash
# Self-test for this skill: its own tests (tests/app/test_skills.py) and the platform skill's selftest.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"; [[ -x "$PY" ]] || PY=python3
cd "$ROOT"
fail=0
"$PY" -m pytest tests/app/test_skills.py -q >/dev/null || { echo "FAIL: tests/app/test_skills.py"; fail=1; }
bash .claude/skills/account-intelligence-platform/scripts/selftest.sh >/dev/null || { echo "FAIL: platform selftest"; fail=1; }
[ $fail -eq 0 ] && echo "selftest OK" || { echo "selftest FAILED"; exit 1; }
