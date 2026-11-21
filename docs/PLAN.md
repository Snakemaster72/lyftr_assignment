# PLAN — Lyftr AI take-home: LLM chatbot with key failover + response caching

Source of truth: `docs/Lyftr_AI_Full_Stack_Assignment.pdf`. Where the build brief and the PDF disagree, the PDF wins.

## 1. Architecture

```
Browser (React SPA, nginx :3000)
   │  fetch JSON  (+ optional X-Simulate-Failure header)
   ▼
FastAPI (:8000)
   ├─ GET  /health                       → DB SELECT 1 + key count
   ├─ POST /chat                         → validate → load session + history (SQLite)
   │                                       → build [system]+history+user
   │                                       → set ContextVar(sim plan) → litellm.Router.acompletion
   │                                       → map result → persist user+assistant in ONE txn
   └─ GET  /sessions/{id}/messages       → history (no system prompt) or 404
        │
        ▼
litellm.Router  model_name="chat"
   ├─ deployment key_1 (order=1)  ─┐  num_retries=0, disable_cooldowns=True
   ├─ deployment key_2 (order=2)  ─┤  cache_responses=True, litellm.cache=Cache(local|disk)
   └─ SimulationHook(CustomLogger).async_pre_call_deployment_hook → raises 429/500/503 per ContextVar plan
        │
        ▼
Provider (Groq by default; any LiteLLM model string; LLM_API_BASE for the fake server in tests)

SQLite file on Docker named volume `app-data` → /data/app.db (WAL)
```

Resolution: `response._hidden_params["cache_hit"] is True` → `LLM_CACHE`, `key_id=null`, `fallback_attempts=0`.
Otherwise `LIVE_INFERENCE`, `key_id=_hidden_params["model_id"]`, `fallback_attempts=additional_headers["x-litellm-attempted-fallbacks"]` (default 0).

## 2. File layout

```
/backend
  app/
    __init__.py
    main.py          app factory, lifespan (schema, router, cache warm-up), exception handlers, CORS, request-id middleware
    config.py        pydantic-settings Settings; key/model parsing; fail fast if <2 keys
    db.py            sqlite3 connection helper, schema, WAL, repo functions (get/upsert session, list/insert messages)
    llm.py           Router build, Cache setup, SimulationHook, header parsing, completion → ChatResult, warm-up
    schemas.py       ChatRequest (strip validators), ChatResponse, Metadata, History models
    logging.py       one-line structured (JSON) logger, no keys/content
    routes/
      health.py  chat.py  sessions.py
  tests/
    conftest.py      fake OpenAI-compatible server fixture (random port), app/client fixtures with tmp DB
    test_health.py test_chat.py test_validation.py test_cache.py test_failover.py test_history.py test_simulation_parse.py
  requirements.txt   (exact pins; litellm==1.110.2)
  requirements-dev.txt
  pyproject.toml     (ruff + pytest config)
  Dockerfile         multi-stage (venv builder → python:3.12-slim, non-root)
/frontend
  src/ main.tsx App.tsx api.ts types.ts components/{MessageList,AssistantMessage,Composer,SystemPrompt,FailoverPanel,ErrorBoundary}.tsx styles.css
  public/config.js   (runtime override placeholder)
  docker-entrypoint.d/40-config.sh  writes /config.js from API_BASE_URL env at container start
  nginx.conf         listen 3000, SPA fallback, no-cache on config.js
  Dockerfile         node build → nginx:alpine
/scripts/verify.sh
/.github/workflows/ci.yml
docker-compose.yml  Makefile  .env.example  .gitignore  README.md
fly.toml (backend)  frontend/vercel.json (if needed)
```

## 3. Key decisions (short)

- **Router per key**, shared `model_name="chat"`, `model_info.id=key_N`, `order=N`, `num_retries=0`, `disable_cooldowns=True` → deterministic "try in order, this request only".
- **Simulation** via `CustomLogger.async_pre_call_deployment_hook` reading a `ContextVar` set by `/chat`; raises real LiteLLM exception classes so the Router fails over exactly as on a provider error. Header honoured only when `ENABLE_FAILURE_SIMULATION=true`; present+enabled ⇒ `cache={"no-cache": True}` (bypass cache read).
- **Empty provider content** → must count as a failure for that key so the Router moves on. The exact LiteLLM hook that can do this inside the Router (so it shows up in `attempted-fallbacks`) still needs checking on 1.110.2 in M1. Fallback if no hook works: treat empty content as 503 for that request. Either way it gets a test and a note in the README.
- **Cache**: `litellm.cache = Cache(type=CACHE_TYPE)`, Router `cache_responses=True`. Key = model + messages (LiteLLM's key; API key not included → shared across keys, matching spec). Startup warm-up exercises the hit path.
- **DB**: stdlib `sqlite3`, one connection per request (via `asyncio.to_thread`), WAL, `CREATE TABLE IF NOT EXISTS`, user+assistant inserted in one transaction only after success.
- **Model**: Groq; current model list checked at M1 (not from memory). `LLM_TIMEOUT_SECONDS` per call so a hanging key fails over.

## 4. Contract checklist

### API — general
- [x] Exactly 3 paths: `GET /health`, `POST /chat`, `GET /sessions/{session_id}/messages`
- [x] `/docs`, `/redoc`, `/openapi.json` disabled unless `ENABLE_DOCS=true`
- [x] All bodies JSON; every error JSON with `detail`; no stack traces / HTML (global 500 handler → `{"detail":"internal server error"}`; JSON 404/405)
- [x] `X-Request-ID` response header; one-line structured log per request (request id, session id, source, key_id, fallback_attempts, latency) — no keys, no message content

### GET /health
- [x] 200 `{"status":"ok","keys_configured":N,"cache_enabled":true}` (+ extras: model, version)
- [x] Really checks DB (`SELECT 1`) and ≥1 key loaded; else 503 JSON

### POST /chat — request
- [x] `session_id` string, required, 1–128 chars, not whitespace-only → else 422
- [x] `message` string, required, 1–4000 chars **after strip** → else 422 (empty, whitespace-only, 4001)
- [x] `system_prompt` optional; >4000 → 422; stored per session, used for this and later turns; default `DEFAULT_SYSTEM_PROMPT`
- [x] Malformed JSON → 422 (FastAPI default)
- [x] Unknown `session_id` starts a new session

### POST /chat — response 200
- [x] `session_id` (echo)
- [x] `reply` non-empty string
- [x] `metadata.resolution_source` exactly `LIVE_INFERENCE` | `LLM_CACHE`
- [x] `metadata.model` LiteLLM model string of the answering deployment (cache: model that produced cached reply)
- [x] `metadata.latency_ms` int, `perf_counter` over whole handler
- [x] `metadata.input_tokens` int (this request only)
- [x] `metadata.output_tokens` int (this request only)
- [x] `metadata.key_id` `key_1`/`key_2`/… ; `null` on cache; never the key itself
- [x] `metadata.fallback_attempts` int; `0` on cache

### POST /chat — errors & rules
- [x] All keys failed → 503 `{"detail":"all providers unavailable"}`
- [x] 422/503 → nothing written to history
- [x] user + assistant rows written in one transaction after success; assistant `metadata` = full metadata JSON
- [x] Failover on 429, quota, 5xx (real and simulated); also 401 on one key
- [x] LLM messages = `[system] + stored turns (oldest first) + new user msg` (optional `HISTORY_MAX_MESSAGES` cap)
- [x] Cache key = model + full message list; same first message in new session → `LLM_CACHE` 2nd time; different message never cached
- [x] Cache hit: identical reply, stored usage tokens, `key_id=null`, `fallback_attempts=0`, faster than live (warm-up at startup)
- [x] Two equal-length first messages in new sessions → ~same input_tokens

### X-Simulate-Failure
- [x] Format `key_id=status[,key_id=status]`, `*` = all; statuses 429/500/502/503
- [x] Specific entry overrides `*`; unknown key ids ignored; malformed → 422 with clear detail
- [x] Honoured only when `ENABLE_FAILURE_SIMULATION=true`; ignored otherwise
- [x] This request only (ContextVar); does not leak into next request
- [x] When active, cache read bypassed
- [x] `key_1=429` → 200 key_2; `key_1=500` → 200 key_2; `*=429` → 503 + nothing in history
- [x] Simulated request returns a real answer, never an error wrapped in 200

### GET /sessions/{session_id}/messages
- [x] `{"session_id":..., "messages":[...]}` oldest first
- [x] user rows `{role, content}`; assistant rows `{role, content, metadata}`
- [x] No system prompt
- [x] Unknown session → 404 `{"detail":"session not found"}`

### Storage
- [x] SQLite only; `messages` table exactly as PDF (+ index `(session_id, id)`); `sessions` table (`session_id` PK, `system_prompt`, `created_at`, `updated_at`)
- [x] Schema created on startup; `created_at` ISO-8601 server time
- [x] `DATABASE_URL=sqlite:////data/app.db` → `/data/app.db`; parent dir created; WAL
- [ ] File on Docker named volume at `/data`; survives restarts

### Env vars (all in `.env.example`, placeholders only)
- [x] `LLM_API_KEYS` (≥2, fail fast otherwise; ids key_1.. in order)
- [x] `DATABASE_URL`
- [x] `ALLOWED_ORIGINS` (CORS; allow `X-Simulate-Failure` + `Content-Type` headers; expose `X-Request-ID`)
- [x] `ENABLE_FAILURE_SIMULATION`
- [x] Ours: `LLM_MODEL`, `LLM_MODELS`, `LLM_API_BASE`, `DEFAULT_SYSTEM_PROMPT`, `CACHE_TYPE`, `CACHE_DIR`, `LLM_TIMEOUT_SECONDS`, `HISTORY_MAX_MESSAGES`, `ENABLE_DOCS`, `LOG_LEVEL`
- [x] Frontend: `VITE_API_BASE_URL` (build) + runtime `API_BASE_URL` → `/config.js`; fallback `http://localhost:8000`

### Frontend (data-testid)
- [x] `chat-input` (textarea; empty after send; Enter sends, Shift+Enter newline)
- [x] `send-button`
- [x] `assistant-message` container per reply containing reply text + all meta-*
- [x] `meta-source` — exactly `LIVE_INFERENCE` / `LLM_CACHE` (value only)
- [x] `meta-model` — model value only
- [x] `meta-latency` — `"812 ms"`
- [x] `meta-input-tokens` — integer only
- [x] `meta-output-tokens` — integer only
- [x] `system-prompt-input` (collapsible, sent when non-empty)
- [x] New `session_id` (`crypto.randomUUID()`) per page load, memory only; "New chat" button
- [x] 422/503/network/timeout → readable message in chat; list-style 422 detail formatted; root error boundary
- [x] Thinking state; key_id + fallback_attempts badges (outside meta-*); colour for source
- [x] Failover demo panel (none / key_1=429 / key_1=500 / *=429 / custom) with note

### Docker / Makefile
- [ ] `backend/Dockerfile` multi-stage, slim runtime, non-root, uvicorn :8000
- [ ] `frontend/Dockerfile` multi-stage, nginx:alpine :3000, SPA fallback
- [ ] `docker-compose.yml` at root: backend 8000, frontend 3000, named volume `app-data` → `/data`, `env_file: .env`, backend healthcheck, frontend depends_on healthy
- [ ] Makefile: `up` (copies .env.example→.env with warning), `down`, `logs`, `test`, `verify`, `clean`
- [ ] Clean clone: `make up` → both URLs → `make down` → `make up` → history persists; image sizes in README

### Repo / deliverables
- [ ] Structure: `/backend`, `/frontend`, `docker-compose.yml`, `Makefile`, `.env.example`, `README.md`
- [x] Backend tests (fake upstream server) — all PDF behaviours (see §5)
- [ ] `scripts/verify.sh`
- [ ] CI: ruff + pytest; frontend typecheck + build; compose build; tests inside built backend image
- [ ] No secrets in git history
- [ ] README headings in order: 1 Live URLs · 2 Run locally (Docker + without) · 3 Design decisions (Mermaid; key fallback, caching, SQLite) · 4 Verification (curl + UI + verify.sh output) · 5 Setup Used (placeholder) · 6 Bonus features
- [ ] Deployment: backend (persistent /data, no sleep, `ENABLE_FAILURE_SIMULATION=true`), frontend with `VITE_API_BASE_URL`; CORS allows frontend origin

## 5. Test matrix (M2)

health shape · happy-path shape/types · multi-turn (2nd upstream payload contains turn 1) · session isolation · history order/shape/404 · 422: missing, empty, whitespace, 4001, malformed JSON, bad session_id, long system_prompt, malformed sim header · 422 writes nothing · cache hit (new session, same first msg): LLM_CACHE, same reply, key_id null, fallback 0, same tokens, faster · different msg not cached · key_1=429 / key_1=500 → key_2, fallback 1 · *=429 → 503 + no history · sim ignored when disabled · sim doesn't leak · specific overrides `*` · real upstream 429 on key_1 fails over · real 401 on key_1 fails over · empty content fails over · system prompt persisted & not in history · 500 handler returns JSON.

## 6. Milestones

- [x] M1 Backend core
- [x] M2 Backend tests green
- [x] M3 Frontend
- [ ] M4 Docker + Makefile (`make up` from clean clone)
- [ ] M5 verify.sh, CI, README
- [ ] M6 Deployment configs + instructions
- [ ] Bonuses (only after all green): disk cache on /data, mixed providers via `LLM_MODELS`

## 7. Verified on litellm 1.110.2 (M1 notes)

- Groq: `llama-3.1-8b-instant` / `llama-3.3-70b-versatile` were shut down 2026-08-16; default model is `groq/openai/gpt-oss-20b` (production, Groq's recommended replacement).
- Empty content → `litellm.post_call_rules` returning False raises `APIResponseValidationError` inside the deployment call → Router fails over; empty reply not cached; all-empty → 503.
- `response.model` is the provider's name, not the LiteLLM string → `model` is looked up from the deployment by `model_id`.
- Cache-hit path imports `litellm.proxy.*` → needs `orjson` and `soundfile` (+numpy, cffi). Pinned in requirements.
- In-memory cache defaults to 200 items / 600 s TTL → replaced with configurable `CACHE_MAX_ITEMS` / `CACHE_TTL_SECONDS`.
- First cache hit ~330 ms cold → ~2 ms after the lifespan warm-up.
- 502 simulation uses `litellm.BadGatewayError` (status 502).
- `x-litellm-attempted-fallbacks` reports 1 even when 2 keys failed (3-key pool) → `fallback_attempts` = deployments seen by our pre-call hook − 1 (header only as fallback). Locked in by `test_three_keys_fail_over_in_order`.
