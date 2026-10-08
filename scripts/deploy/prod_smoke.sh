#!/usr/bin/env bash
# Anonymous production smoke through the public HTTPS origin: TLS, Caddy
# routing (Next.js vs FastAPI), API and parse-worker health, auth boundaries,
# hidden dev surfaces, Steam sign-in redirect and security headers. curl only;
# no session needed.
#
#   bash scripts/deploy/prod_smoke.sh https://coach.example.com
#
# Prints PASS/FAIL per check, then the manual Steam sign-in checklist.
# Exits non-zero when any check fails.
set -uo pipefail

BASE="${1:-}"
if [[ ! "$BASE" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?/?$ ]]; then
  echo "usage: $0 https://<domain>" >&2
  exit 2
fi
BASE="${BASE%/}"
HOST="${BASE#https://}"
ZERO_DEMO="00000000-0000-0000-0000-000000000000"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILURES=0
STATUS=""

pass() { printf 'PASS  %s\n' "$1"; }
fail() {
  printf 'FAIL  %s -- %s\n' "$1" "$2"
  FAILURES=$((FAILURES + 1))
}

# request METHOD URL [curl args...]: sets STATUS; headers/body land in $TMP.
request() {
  local method="$1" url="$2"
  shift 2
  : >"$TMP/headers"
  : >"$TMP/body"
  STATUS="$(curl -sS --max-time 20 --request "$method" --dump-header "$TMP/headers" \
    --output "$TMP/body" --write-out '%{http_code}' "$@" "$url" 2>"$TMP/stderr")" || STATUS="000"
}

# header NAME: last value of a response header (case-insensitive), CR stripped.
header() {
  grep -i "^$1:" "$TMP/headers" | tail -n 1 | cut -d: -f2- | sed -e 's/^[[:space:]]*//' -e 's/\r$//'
}

curl_error() { tr -d '\r' <"$TMP/stderr" | head -c 200; }

expect_status() { # NAME EXPECTED...: pass when STATUS is one of EXPECTED
  local name="$1" code
  shift
  for code in "$@"; do
    if [[ "$STATUS" == "$code" ]]; then
      pass "$name"
      return 0
    fi
  done
  fail "$name" "HTTP $STATUS, expected $* $(curl_error)"
  return 1
}

echo "Production smoke against $BASE"

# 1. TLS: curl verifies the chain and host name (no -k anywhere in this script).
if verify="$(curl -sS --max-time 20 -o /dev/null -w '%{ssl_verify_result}' "$BASE/health" 2>"$TMP/stderr")" \
  && [[ "$verify" == "0" ]]; then
  pass "TLS certificate is valid for $HOST"
else
  fail "TLS certificate is valid for $HOST" "$(curl_error)"
fi

# 2. Plain HTTP redirects to HTTPS (Caddy automatic HTTPS).
redirect="$(curl -sS --max-time 20 -o /dev/null -w '%{http_code} %{redirect_url}' "http://$HOST/dashboard" 2>"$TMP/stderr" || true)"
if [[ "$redirect" == 30[178]" https://$HOST/"* ]]; then
  pass "http:// redirects to https://"
else
  fail "http:// redirects to https://" "got '${redirect}' $(curl_error)"
fi

# 3. Health through Caddy.
request GET "$BASE/health"
health_body="$(tr -d ' \t\r\n' <"$TMP/body")"
if [[ "$STATUS" == "200" && "$health_body" == '{"status":"ok"}' ]]; then
  pass "GET /health -> 200 {\"status\":\"ok\"}"
else
  fail "GET /health -> 200 {\"status\":\"ok\"}" "HTTP $STATUS body '${health_body:0:120}' $(curl_error)"
fi

# 3b. The parse worker's heartbeat, through Caddy (/health leaves the worker out
# on purpose: caddy and frontend start only once /health is green). A 404 here
# means Caddy sent the path to Next.js; a 503 means no live worker heartbeat.
request GET "$BASE/health/worker"
if [[ "$STATUS" == "200" ]]; then
  pass "GET /health/worker -> 200 (parse worker alive)"
else
  fail "GET /health/worker -> 200 (parse worker alive)" \
    "HTTP $STATUS content-type '$(header content-type)' $(curl_error); check: dc logs --tail 60 worker"
fi

# 4. Dashboard is server-rendered HTML with the edge security headers.
request GET "$BASE/dashboard"
if [[ "$STATUS" == "200" && "$(header content-type)" == text/html* ]]; then
  pass "GET /dashboard -> 200 HTML"
else
  fail "GET /dashboard -> 200 HTML" "HTTP $STATUS content-type '$(header content-type)' $(curl_error)"
fi
hsts="$(header strict-transport-security)"
if [[ "$hsts" == *max-age=31536000* ]]; then
  pass "HSTS on /dashboard ($hsts)"
else
  fail "HSTS on /dashboard" "strict-transport-security '$hsts'"
fi
if [[ "$(header x-content-type-options)" == nosniff \
  && "$(header x-frame-options)" == DENY \
  && "$(header referrer-policy)" == strict-origin-when-cross-origin ]]; then
  pass "nosniff / X-Frame-Options / Referrer-Policy on /dashboard"
else
  fail "nosniff / X-Frame-Options / Referrer-Policy on /dashboard" \
    "got '$(header x-content-type-options)' '$(header x-frame-options)' '$(header referrer-policy)'"
fi

# 5. GET /demos/{id} is the Next.js review page, not the API.
request GET "$BASE/demos/$ZERO_DEMO"
if [[ ("$STATUS" == "200" || "$STATUS" == "404") && "$(header content-type)" == text/html* ]]; then
  pass "GET /demos/{id} -> Next.js HTML (HTTP $STATUS)"
else
  fail "GET /demos/{id} -> Next.js HTML" "HTTP $STATUS content-type '$(header content-type)'"
fi

# 5b. The privacy page is public Next.js HTML (no session needed).
request GET "$BASE/privacy"
if [[ "$STATUS" == "200" && "$(header content-type)" == text/html* ]]; then
  pass "GET /privacy -> 200 HTML"
else
  fail "GET /privacy -> 200 HTML" "HTTP $STATUS content-type '$(header content-type)' $(curl_error)"
fi
# Other players in an uploaded .dem can only ask for a removal through this contact.
if grep -q '本站没有公开联系方式' "$TMP/body"; then
  fail "/privacy shows a contact" "built without NEXT_PUBLIC_PRIVACY_CONTACT; set it and redeploy"
else
  pass "/privacy shows a contact"
fi

# 6. API routes reject anonymous callers (proves they reached FastAPI).
request GET "$BASE/demos"
expect_status "anonymous GET /demos -> 401" 401
request GET "$BASE/demos/$ZERO_DEMO/status"
expect_status "anonymous GET /demos/{id}/status -> 401" 401
request PATCH "$BASE/demos/x" -H 'Content-Type: application/json' --data '{}'
if [[ "$STATUS" == "401" && "$(header content-type)" == application/json* ]]; then
  pass "anonymous PATCH /demos/{id} -> 401 from the API"
else
  fail "anonymous PATCH /demos/{id} -> 401 from the API" "HTTP $STATUS content-type '$(header content-type)'"
fi
request POST "$BASE/uploads/demo" --data ''
expect_status "anonymous POST /uploads/demo -> 401" 401
# Chunked upload sessions: creating and reading need the session cookie.
request POST "$BASE/uploads/sessions" -H "Origin: $BASE" -H 'Content-Type: application/json' \
  --data '{"filename":"smoke.dem","size":1}'
if [[ "$STATUS" == "401" && "$(header content-type)" == application/json* ]]; then
  pass "anonymous POST /uploads/sessions -> 401 from the API"
else
  fail "anonymous POST /uploads/sessions -> 401 from the API" "HTTP $STATUS content-type '$(header content-type)'"
fi
request GET "$BASE/uploads/sessions/current"
expect_status "anonymous GET /uploads/sessions/current -> 401" 401
# A part PUT is authorized by its X-Upload-Token alone (exempt from the cookie
# check, never a cookie fallback): without a token it is the API's JSON 404.
# A 401 here means the exemption is missing; a 403 means the Origin check
# refused the site's own origin.
request PUT "$BASE/uploads/sessions/00000000000000000000000000000000/parts/0" \
  -H "Origin: $BASE" -H 'Content-Type: application/octet-stream' --data ''
if [[ "$STATUS" == "404" && "$(header content-type)" == application/json* ]]; then
  pass "PUT upload part without a token -> 404 from the API"
else
  fail "PUT upload part without a token -> 404 from the API" "HTTP $STATUS content-type '$(header content-type)'"
fi
request GET "$BASE/auth/me"
expect_status "anonymous GET /auth/me -> 401" 401
# Deletion routes: JSON 401 proves Caddy sent them to FastAPI, not Next.js
# (the Next.js account page lives at /account, not /auth/account).
request DELETE "$BASE/auth/account" -H 'Content-Type: application/json' --data '{"confirm":"delete-my-account"}'
if [[ "$STATUS" == "401" && "$(header content-type)" == application/json* ]]; then
  pass "anonymous DELETE /auth/account -> 401 from the API"
else
  fail "anonymous DELETE /auth/account -> 401 from the API" "HTTP $STATUS content-type '$(header content-type)'"
fi
request DELETE "$BASE/demos/$ZERO_DEMO"
if [[ "$STATUS" == "401" && "$(header content-type)" == application/json* ]]; then
  pass "anonymous DELETE /demos/{id} -> 401 from the API"
else
  fail "anonymous DELETE /demos/{id} -> 401 from the API" "HTTP $STATUS content-type '$(header content-type)'"
fi

# 7. Development surfaces stay hidden in production.
request GET "$BASE/docs"
expect_status "GET /docs -> 404" 404
request GET "$BASE/openapi.json"
expect_status "GET /openapi.json -> 404" 404

# 8. Steam sign-in starts an OpenID redirect with a __Host- state cookie.
request GET "$BASE/auth/steam/login"
location="$(header location)"
state_cookie="$(grep -i '^set-cookie:[[:space:]]*__Host-' "$TMP/headers" | tail -n 1 | tr -d '\r')"
if [[ ("$STATUS" == "302" || "$STATUS" == "303") && "$location" == https://steamcommunity.com/openid/login* ]]; then
  pass "GET /auth/steam/login -> $STATUS to steamcommunity.com/openid/login"
else
  fail "GET /auth/steam/login -> redirect to Steam OpenID" "HTTP $STATUS location '${location:0:120}' $(curl_error)"
fi
if [[ -n "$state_cookie" ]] \
  && grep -qi ';[[:space:]]*secure' <<<"$state_cookie" \
  && grep -qi ';[[:space:]]*httponly' <<<"$state_cookie"; then
  pass "Steam state cookie is __Host-, Secure, HttpOnly"
else
  fail "Steam state cookie is __Host-, Secure, HttpOnly" "set-cookie '${state_cookie:0:160}'"
fi

echo
if ((FAILURES > 0)); then
  echo "RESULT: $FAILURES check(s) FAILED"
else
  echo "RESULT: all automated checks passed"
fi

cat <<EOF

Manual checklist (real browser, real Steam accounts) -- $BASE
  [ ] An invited Steam account signs in via "通过 Steam 登录" and lands on /dashboard.
  [ ] A Steam account NOT in STEAM_LOGIN_ALLOWLIST ends on /auth/callback?error=not_invited.
  [ ] Upload a real .dem on /dashboard; it moves to parsed/completed.
  [ ] Upload again and switch the network off mid-way: it pauses, then resumes by itself.
  [ ] Reload mid-upload: the dashboard offers 选择同一个文件继续; re-picking the file resumes.
  [ ] Cancel an upload: it leaves the list and no new match appears.
  [ ] 'dc logs api' shows one compact line per finished upload session (bytes, seconds), no file name.
  [ ] Open the review (/demos/<id>): replay plays, rounds switch, map and coaching cards load.
  [ ] Sign out; /dashboard asks you to sign in again.
  [ ] Signed out, /privacy opens with your region/contact and the Valve disclaimer in the footer.
  [ ] Delete the uploaded test match from the row menu; it disappears and today's upload count stays.
EOF

((FAILURES == 0))
