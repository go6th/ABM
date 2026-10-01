#!/usr/bin/env bash
# Self-test for the helper scripts. Run from the skill folder: bash scripts/selftest.sh
set -u
cd "$(dirname "$0")/.."
fail=0
python3 scripts/validate_assets.py || fail=1
python3 scripts/grading_reference.py --selftest >/dev/null || { echo "FAIL: grading_reference selftest"; fail=1; }
python3 scripts/lint_migrations.py assets/starter/migrations >/dev/null || { echo "FAIL: starter migrations should pass the lint"; fail=1; }
python3 scripts/lint_migrations.py scripts/fixtures/good.sql >/dev/null || { echo "FAIL: good.sql should pass"; fail=1; }
python3 scripts/lint_migrations.py scripts/fixtures/bad_public_reader.sql >/dev/null && { echo "FAIL: bad_public_reader.sql should fail"; fail=1; }
python3 scripts/lint_migrations.py scripts/fixtures/bad_no_force.sql >/dev/null && { echo "FAIL: bad_no_force.sql should fail"; fail=1; }
tmp=$(mktemp -d); printf 'Prepared by 6th agents.\n' > "$tmp/clean.md"; printf 'Source: Parallel search\n' > "$tmp/dirty.md"
python3 scripts/scan_client_output.py "$tmp/clean.md" >/dev/null || { echo "FAIL: clean.md should pass"; fail=1; }
python3 scripts/scan_client_output.py "$tmp/dirty.md" >/dev/null && { echo "FAIL: dirty.md should fail"; fail=1; }
rm -rf "$tmp"
[ $fail -eq 0 ] && echo "selftest OK" || { echo "selftest FAILED"; exit 1; }
