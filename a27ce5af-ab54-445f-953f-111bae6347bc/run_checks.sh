#!/usr/bin/env bash
#
# AgriCloud-CI pre-flight quality gate.
#
#   1. flake8      - style and obvious bug check on the Python backend
#   2. pytest      - unit and endpoint tests (no network, no Postgres needed)
#   3. credentials - fail if an API key, password or database URL is hardcoded
#
# Prints a clear PASS/FAIL line per check and exits non-zero if anything fails.
# GitHub Actions runs exactly this script before deploying (see ci.yml).
#
# Usage:  bash run_checks.sh
#         PYTHON=/path/to/python bash run_checks.sh

set -uo pipefail
cd "$(dirname "$0")"

# Prefer an explicit interpreter, then the local virtualenv, then python3.
if [ -n "${PYTHON:-}" ]; then
  PYTHON_BIN="$PYTHON"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON_BIN=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

STATUS=0
PASS_LINE="------------------------------------------------------------"

report() { # $1 = check name, $2 = exit code
  if [ "$2" -eq 0 ]; then
    echo "PASS: $1"
  else
    echo "FAIL: $1"
    STATUS=1
  fi
}

echo "$PASS_LINE"
echo "AgriCloud-CI quality gate (interpreter: $PYTHON_BIN)"
echo "$PASS_LINE"

# ---------------------------------------------------------------------------
# 1. flake8
# ---------------------------------------------------------------------------
echo "[1/3] flake8 (style, unused imports)"
"$PYTHON_BIN" -m flake8 api tests
archive=$?
report "flake8" "$archive"

# ---------------------------------------------------------------------------
# 2. pytest
# ---------------------------------------------------------------------------
echo "[2/3] pytest (unit + endpoint tests)"
"$PYTHON_BIN" -m pytest
archive=$?
report "pytest" "$archive"

# ---------------------------------------------------------------------------
# 3. Credential scan
# ---------------------------------------------------------------------------
# Scans production source only (api/, public/, firmware/). Tests and config
# templates intentionally contain dummy values such as "test-device-key".
credential_scan() {
  local -a patterns=(
    "database_url_with_password|postgres(ql)?(\\+[a-z0-9]+)?://[^[:space:]/]+:[^@[:space:]]+@"
    "hardcoded_credential|(DEVICE_API_KEY|API_KEY|SECRET_KEY|POSTGRES_PASSWORD|DB_PASSWORD|PASSWORD|DATABASE_URL|WIFI_PASSWORD)[[:space:]]*=[[:space:]]*[\"'][^\"']{4,}[\"']"
    "openai_style_key|sk-[A-Za-z0-9]{16,}"
    "aws_access_key|AKIA[0-9A-Z]{16}"
  )
  local file entry label regex found=0

  while IFS= read -r file; do
    for entry in "${patterns[@]}"; do
      label="${entry%%|*}"
      regex="${entry#*|}"
      if grep -nIE -- "$regex" "$file" >/dev/null 2>&1; then
        echo "  BLOCKED [$label] in $file"
        # Print only the line numbers: never echo a possible secret to the logs.
        grep -nIE -- "$regex" "$file" | cut -d: -f1 | sed 's/^/    line /'
        found=1
      fi
    done
  done < <(find api public firmware -type f \
    \( -name '*.py' -o -name '*.js' -o -name '*.html' -o -name '*.ino' -o -name '*.json' \) \
    2>/dev/null)

  if [ "$found" -ne 0 ]; then
    echo "  Move every key/password/URL into an environment variable (.env locally,"
    echo "  Vercel Project Settings in production). See .env.example."
  fi
  return "$found"
}

echo "[3/3] credential scan (no hardcoded keys, passwords or database URLs)"
credential_scan
archive=$?
report "credential check" "$archive"

echo "$PASS_LINE"
if [ "$STATUS" -eq 0 ]; then
  echo "PASS: all checks passed"
else
  echo "FAIL: one or more checks failed"
fi
echo "$PASS_LINE"

exit "$STATUS"
