#!/bin/sh
# Smoke test for a running demo stack (see docs/demo.md). Run it from this directory after
# `docker compose up --wait`. It reads the same .env as compose.yml, and uses COMPOSE
# (default "docker compose") to talk to the stack.
#
# It proves five things: the loopback port serves the demo; the stack-unique alias on the
# edge network routes to jobseekr and not to another container answering to web; nothing
# but the web service joined the edge network; db and scheduler have no route out; and a
# visitor can start a sandbox and leave it again.
set -eu

COMPOSE=${COMPOSE:-docker compose}
DOCKER=${DOCKER:-docker}
set -a
. ./.env
set +a
PORT=${DEMO_SMOKE_PORT:-8095}
MARKER="Everything in it is fictional"
HEADERS=$(mktemp)
BODY=$(mktemp)
trap 'rm -f "$HEADERS" "$BODY"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# Requests carry the public host and X-Forwarded-Proto the way the proxy or tunnel would,
# so ALLOWED_HOSTS and the HTTPS redirect are exercised too.
fetch_alias() {
  $DOCKER run --rm --network "$DEMO_EDGE_NETWORK" curlimages/curl:8.11.1 \
    --silent --location --max-time 10 -H "Host: $DEMO_HOSTNAME" -H "X-Forwarded-Proto: https" "$@"
}

echo "loopback port serves the Try page"
curl --fail --silent --location --max-time 10 -H "Host: $DEMO_HOSTNAME" -H "X-Forwarded-Proto: https" \
  "http://127.0.0.1:$PORT/" | grep -q "$MARKER" || fail "no demo notice on 127.0.0.1:$PORT"
curl --fail --silent --max-time 10 "http://127.0.0.1:$PORT/healthz" | grep -qx ok || fail "/healthz"

echo "a visitor can start a sandbox and leave it"
# The cookies are Secure, so curl would not send them back over plain HTTP. The script
# keeps them itself and sends them in a Cookie header, as the browser would over HTTPS.
visit() {
  path=$1; shift
  curl --silent --max-time 20 -D "$HEADERS" -o "$BODY" -H "Host: $DEMO_HOSTNAME" -H "X-Forwarded-Proto: https" \
    -H "Origin: https://$DEMO_HOSTNAME" -H "Referer: https://$DEMO_HOSTNAME/" ${COOKIES:+-H "Cookie: $COOKIES"} \
    "$@" "http://127.0.0.1:$PORT$path"
  fresh=$(sed -n 's/^[Ss]et-[Cc]ookie: \([^=]*=[^;]*\);.*/\1/p' "$HEADERS")
  for pair in $fresh; do
    COOKIES=$(printf '%s\n' "$COOKIES" | tr ';' '\n' | sed 's/^ *//' | grep -v "^${pair%%=*}=" | paste -sd ';' - | sed 's/;/; /g')
    COOKIES=${COOKIES:+$COOKIES; }$pair
  done
  status=$(sed -n '1s/^HTTP[^ ]* \([0-9]*\).*/\1/p' "$HEADERS")
}
csrf() { sed -n 's/.*name="csrfmiddlewaretoken" value="\([^"]*\)".*/\1/p' "$BODY" | head -n 1; }

COOKIES=""
visit /try/
token=$(csrf)
[ -n "$token" ] || fail "no CSRF token on /try/"
visit /try/ --data-urlencode "csrfmiddlewaretoken=$token"
[ "$status" = 302 ] || fail "starting a sandbox answered $status, not 302"
visit /
grep -q "Leave the demo" "$BODY" || fail "the sandbox did not sign the visitor in"
visit /documents/
doc=$(sed -n 's|.*href="\(/documents/[0-9]*/file/\)".*|\1|p' "$BODY" | head -n 1)
[ -n "$doc" ] || fail "the sandbox lists no sample document"
visit "$doc"
[ "$status" = 200 ] || fail "a sample document answered $status, not 200"
head -c 4 "$BODY" | grep -q '%PDF' || fail "a sample document is not a PDF"
visit /
token=$(csrf)
[ -n "$token" ] || fail "no CSRF token on the leave form"
visit /try/leave/ --data-urlencode "csrfmiddlewaretoken=$token"
[ "$status" = 302 ] || fail "leaving the demo answered $status, not 302"

echo "the alias on the edge network routes to jobseekr"
for _ in 1 2 3 4 5 6 7 8; do
  fetch_alias "http://$DEMO_EDGE_ALIAS:8000/" | grep -q "$MARKER" \
    || fail "$DEMO_EDGE_ALIAS:8000 did not answer with the demo"
done

echo "the bare name web on the edge network does not route to jobseekr"
for _ in 1 2 3 4 5 6 7 8; do
  if fetch_alias "http://web:8000/" | grep -q "$MARKER"; then
    fail "web on the edge network reached jobseekr"
  fi
done

echo "only the web service joined the edge network"
joined=$($DOCKER network inspect "$DEMO_EDGE_NETWORK" --format '{{range .Containers}}{{.Name}} {{end}}')
for service in db scheduler; do
  id=$($COMPOSE ps -q "$service")
  [ -n "$id" ] || fail "$service is not running"
  name=$($DOCKER inspect --format '{{.Name}}' "$id" | sed 's|^/||')
  case " $joined" in *" $name "*) fail "$service is on the edge network" ;; esac
done

echo "db and scheduler have no route out"
$COMPOSE exec -T scheduler python -c "import urllib.request; urllib.request.urlopen('http://192.0.2.1', timeout=5)" \
  >/dev/null 2>&1 && fail "scheduler reached the outside"
$COMPOSE exec -T scheduler python -c "import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)" \
  >/dev/null 2>&1 && fail "scheduler reached the internet"
$COMPOSE exec -T db sh -c 'wget -q -T 5 -O /dev/null http://1.1.1.1' >/dev/null 2>&1 \
  && fail "db reached the internet"

echo "the demo image has its own name"
$DOCKER image inspect showcase-jobseekr-app:latest >/dev/null || fail "image showcase-jobseekr-app:latest missing"

echo "ok"
