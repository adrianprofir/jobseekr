#!/usr/bin/env bash
# Browser regression checks for PostHog consent (requires chrome-devtools-axi and a running app).
# Usage: POSTHOG_PROJECT_KEY=phc_test ./scripts/verify_analytics_consent.sh http://127.0.0.1:8000/login/
set -euo pipefail

BASE_URL="${1:?pass the login page URL}"
SESSION="${CHROME_DEVTOOLS_AXI_SESSION:-jobseekr-analytics-verify}"

export CHROME_DEVTOOLS_AXI_SESSION="$SESSION"

chrome-devtools-axi start >/dev/null
chrome-devtools-axi open "$BASE_URL" >/dev/null
sleep 2

chrome-devtools-axi eval "() => { document.cookie='ap_analytics_consent=; Max-Age=0; Path=/'; localStorage.clear(); }" >/dev/null
chrome-devtools-axi open "$BASE_URL" >/dev/null
sleep 1

# Accept -> Decline -> Accept without reload
chrome-devtools-axi eval "() => document.querySelector('[data-analytics-action=accept]').click()" >/dev/null
sleep 2
chrome-devtools-axi eval "() => { document.getElementById('analytics-prefs').click(); document.querySelector('[data-analytics-action=decline]').click(); }" >/dev/null
sleep 1
chrome-devtools-axi eval "() => { document.getElementById('analytics-prefs').click(); document.querySelector('[data-analytics-action=accept]').click(); }" >/dev/null
sleep 2
OPTED_OUT=$(chrome-devtools-axi eval "() => window.posthog && posthog.has_opted_out_capturing()" 2>/dev/null | sed -n 's/^result: *//p' | tr -d '"')
if [[ "$OPTED_OUT" != "false" ]]; then
  echo "FAIL: expected has_opted_out_capturing false after re-accept, got $OPTED_OUT" >&2
  exit 1
fi

# Cookie already denied but SDK active: Decline must still opt out
chrome-devtools-axi eval "() => { posthog.opt_in_capturing(); document.cookie='ap_analytics_consent=denied; Path=/; Max-Age=15552000'; }" >/dev/null
chrome-devtools-axi eval "() => document.getElementById('analytics-prefs').click()" >/dev/null
chrome-devtools-axi eval "() => document.querySelector('[data-analytics-action=decline]').click()" >/dev/null
sleep 1
OPTED_OUT=$(chrome-devtools-axi eval "() => posthog.has_opted_out_capturing()" 2>/dev/null | sed -n 's/^result: *//p' | tr -d '"')
if [[ "$OPTED_OUT" != "true" ]]; then
  echo "FAIL: expected has_opted_out_capturing true after Decline with active SDK, got $OPTED_OUT" >&2
  exit 1
fi

echo "OK: analytics consent browser regressions passed"
chrome-devtools-axi stop >/dev/null || true
