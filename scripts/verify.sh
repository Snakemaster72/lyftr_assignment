#!/usr/bin/env bash
# End-to-end contract checks against a running backend.
#   BASE_URL=https://my-backend.example ./scripts/verify.sh
# Needs curl and jq. Prints PASS/FAIL per check; exits non-zero if any check fails.
# Failover checks need ENABLE_FAILURE_SIMULATION=true on the backend.

set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
BASE_URL="${BASE_URL%/}"
RUN_ID="$(date +%s)-$RANDOM"   # unique per run, so re-runs never hit an old cache entry
PASS=0
FAIL=0

for tool in curl jq; do
  command -v "$tool" >/dev/null || { echo "verify.sh needs $tool"; exit 2; }
done

if [ -t 1 ]; then GREEN=$'\e[32m'; RED=$'\e[31m'; DIM=$'\e[2m'; RESET=$'\e[0m'; else GREEN=""; RED=""; DIM=""; RESET=""; fi

check() { # check <name> <condition-exit-code> [detail]
  if [ "$2" -eq 0 ]; then
    PASS=$((PASS + 1)); printf '%sPASS%s  %s %s\n' "$GREEN" "$RESET" "$1" "${DIM}${3:-}${RESET}"
  else
    FAIL=$((FAIL + 1)); printf '%sFAIL%s  %s %s\n' "$RED" "$RESET" "$1" "${3:-}"
  fi
}

# request <method> <path> [json-body] [extra curl args...] -> sets STATUS and BODY
request() {
  local method="$1" path="$2" data="${3:-}"
  shift 3 2>/dev/null || shift $#
  local out
  if [ -n "$data" ]; then
    out="$(curl -sS -m 90 -X "$method" "$BASE_URL$path" -H 'Content-Type: application/json' --data-binary "$data" "$@" -w $'\n%{http_code}')"
  else
    out="$(curl -sS -m 90 -X "$method" "$BASE_URL$path" "$@" -w $'\n%{http_code}')"
  fi
  STATUS="${out##*$'\n'}"
  BODY="${out%$'\n'*}"
}

chat_body() { jq -cn --arg s "$1" --arg m "$2" '{session_id: $s, message: $m}'; }
jqb() { jq -r "$1" <<<"$BODY" 2>/dev/null; }
history_count() {
  request GET "/sessions/$1/messages"
  if [ "$STATUS" = 404 ]; then echo 0; else jqb '.messages | length'; fi
}

echo "Verifying $BASE_URL (run $RUN_ID)"
echo

# --- health -------------------------------------------------------------------
request GET /health
jq -e '.status == "ok" and (.keys_configured | type == "number" and . >= 2) and .cache_enabled == true' <<<"$BODY" >/dev/null 2>&1
check "GET /health -> 200 {status ok, keys_configured>=2, cache_enabled true}" $(( $? == 0 && STATUS == 200 ? 0 : 1 )) "$BODY"
SIM_ENABLED="$(jqb '.failure_simulation // "unknown"')"

# --- live call ----------------------------------------------------------------
S1="verify-$RUN_ID-a"; S2="verify-$RUN_ID-b"
FIRST="Reply with one short sentence about the ocean. nonce $RUN_ID"
request POST /chat "$(chat_body "$S1" "$FIRST")"
LIVE_BODY="$BODY"
jq -e --arg s "$S1" '
  .session_id == $s and (.reply | type == "string" and length > 0)
  and .metadata.resolution_source == "LIVE_INFERENCE"
  and (.metadata.model | type == "string" and length > 0)
  and (.metadata.latency_ms | type == "number" and floor == .)
  and (.metadata.input_tokens | type == "number" and . > 0)
  and (.metadata.output_tokens | type == "number" and . > 0)
  and (.metadata.key_id | type == "string" and startswith("key_"))
  and (.metadata.fallback_attempts | type == "number")' <<<"$BODY" >/dev/null 2>&1
check "POST /chat live -> LIVE_INFERENCE with full metadata" $(( $? == 0 && STATUS == 200 ? 0 : 1 )) "$(jqb '.metadata | tostring')"
LIVE_REPLY="$(jqb .reply)"; LIVE_LATENCY="$(jqb .metadata.latency_ms)"
LIVE_IN="$(jqb .metadata.input_tokens)"; LIVE_OUT="$(jqb .metadata.output_tokens)"

# --- cache hit (same first message, new session) -----------------------------
sleep 1  # the cache write is asynchronous
request POST /chat "$(chat_body "$S2" "$FIRST")"
jq -e '.metadata.resolution_source == "LLM_CACHE" and .metadata.key_id == null and .metadata.fallback_attempts == 0' <<<"$BODY" >/dev/null 2>&1
check "same first message in new session -> LLM_CACHE, key_id null, fallback 0" $(( $? == 0 && STATUS == 200 ? 0 : 1 )) "$(jqb '.metadata | tostring')"
[ "$(jqb .reply)" = "$LIVE_REPLY" ]
check "cache hit returns the identical reply" $?
[ "$(jqb .metadata.input_tokens)" = "$LIVE_IN" ] && [ "$(jqb .metadata.output_tokens)" = "$LIVE_OUT" ]
check "cache hit reports the stored token usage" $? "in=$(jqb .metadata.input_tokens) out=$(jqb .metadata.output_tokens)"
CACHE_LATENCY="$(jqb .metadata.latency_ms)"
[ -n "$CACHE_LATENCY" ] && [ "$CACHE_LATENCY" -lt "$LIVE_LATENCY" ] 2>/dev/null
check "cache hit is faster than the live call" $? "cache=${CACHE_LATENCY}ms live=${LIVE_LATENCY}ms"

request POST /chat "$(chat_body "verify-$RUN_ID-c" "A different question entirely: name a colour. nonce $RUN_ID")"
[ "$STATUS" = 200 ] && [ "$(jqb .metadata.resolution_source)" = "LIVE_INFERENCE" ]
check "a different message is not served from cache" $? "$(jqb .metadata.resolution_source)"

# --- multi-turn recall ---------------------------------------------------------
SM="verify-$RUN_ID-memory"; NAME="Zorblax$RANDOM"
request POST /chat "$(chat_body "$SM" "Hi! My name is $NAME. Please remember it.")"
request POST /chat "$(chat_body "$SM" "What is my name? Answer with just the name.")"
grep -qi "$NAME" <<<"$(jqb .reply)"
check "multi-turn: the bot remembers the name from turn 1" $? "$(jqb .reply | head -c 120)"

# --- history endpoint --------------------------------------------------------
request GET "/sessions/$SM/messages"
jq -e --arg s "$SM" --arg n "$NAME" '
  .session_id == $s and (.messages | length == 4)
  and ([.messages[].role] == ["user","assistant","user","assistant"])
  and (.messages[0].content | contains($n))
  and (.messages[1].metadata.resolution_source | type == "string")
  and (.messages[0] | has("metadata") | not)
  and all(.messages[]; .role != "system")' <<<"$BODY" >/dev/null 2>&1
check "GET /sessions/{id}/messages -> 4 messages, oldest first, metadata on assistant" $(( $? == 0 && STATUS == 200 ? 0 : 1 ))
request GET "/sessions/verify-$RUN_ID-nope/messages"
[ "$STATUS" = 404 ] && [ "$(jqb .detail)" = "session not found" ]
check "unknown session -> 404 {detail: session not found}" $? "$STATUS $BODY"

# --- validation (422) ----------------------------------------------------------
SV="verify-$RUN_ID-invalid"
LONG="$(head -c 4001 </dev/zero | tr '\0' 'x')"
expect_422() { # expect_422 <name> <raw-body>
  request POST /chat "$2"
  [ "$STATUS" = 422 ] && jq -e 'has("detail")' <<<"$BODY" >/dev/null 2>&1
  check "422: $1" $? "$STATUS"
}
expect_422 "missing message" "{\"session_id\":\"$SV\"}"
expect_422 "missing session_id" '{"message":"hi"}'
expect_422 "empty message" "{\"session_id\":\"$SV\",\"message\":\"\"}"
expect_422 "whitespace-only message" "{\"session_id\":\"$SV\",\"message\":\"   \"}"
expect_422 "message over 4000 chars" "{\"session_id\":\"$SV\",\"message\":\"$LONG\"}"
expect_422 "empty session_id" '{"session_id":"","message":"hi"}'
expect_422 "malformed JSON" '{"session_id": "x", "message": '
[ "$(history_count "$SV")" = 0 ]
check "422 requests write nothing to history" $?

# --- failover (X-Simulate-Failure) ----------------------------------------------
if [ "$SIM_ENABLED" = "false" ]; then
  echo "${DIM}note: /health says failure_simulation=false; the next checks need ENABLE_FAILURE_SIMULATION=true${RESET}"
fi
SF="verify-$RUN_ID-failover"
for status in 429 500; do
  request POST /chat "$(chat_body "$SF" "Failover test $status: say hi. nonce $RUN_ID")" -H "X-Simulate-Failure: key_1=$status"
  [ "$STATUS" = 200 ] && [ "$(jqb .metadata.key_id)" = "key_2" ] && [ -n "$(jqb .reply)" ]
  check "X-Simulate-Failure: key_1=$status -> 200 answered by key_2" $? "$STATUS $(jqb '.metadata | tostring')"
done

BEFORE="$(history_count "$SF")"
request POST /chat "$(chat_body "$SF" "This should fail. nonce $RUN_ID")" -H "X-Simulate-Failure: *=429"
[ "$STATUS" = 503 ] && [ "$(jqb .detail)" = "all providers unavailable" ]
check "X-Simulate-Failure: *=429 -> 503 {detail: all providers unavailable}" $? "$STATUS $BODY"
[ "$(history_count "$SF")" = "$BEFORE" ]
check "*=429 adds nothing to history" $? "before=$BEFORE after=$(history_count "$SF")"

request POST /chat "$(chat_body "$SF" "After the simulation. nonce $RUN_ID")"
[ "$STATUS" = 200 ] && [ "$(jqb .metadata.key_id)" = "key_1" ]
check "simulation does not leak: next request answered by key_1" $? "$(jqb .metadata.key_id)"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
