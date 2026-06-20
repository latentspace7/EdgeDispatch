# EdgeDispatch — Architecture Reference

> **Audience:** Principal Product Engineers, Principal AI Engineers, and
> Principal Technical Business Analysts. Read time: under 10 minutes.
>
> A practical onboarding and reference guide for the EdgeDispatch prototype,
> a two-tier, MCP-aware hybrid LLM orchestration system. For the full operator
> runbook, see [`README.md`](./README.md). This document covers *what the
> system is, how it is built, and where to make changes*.

---

## Contents

1. [Repository purpose](#1-repository-purpose)
2. [System overview](#2-system-overview)
3. [Directory and file structure](#3-directory-and-file-structure)
4. [Frontend architecture](#4-frontend-architecture)
5. [Backend architecture](#5-backend-architecture)
6. [End-to-end application flow](#6-end-to-end-application-flow)
7. [Authentication and security](#7-authentication-and-security)
8. [Configuration and environment](#8-configuration-and-environment)
9. [AI and data flow](#9-ai-and-data-flow)
10. [External integrations](#10-external-integrations)
11. [Development workflow](#11-development-workflow)
12. [Testing and quality](#12-testing-and-quality)
13. [Deployment and runtime](#13-deployment-and-runtime)
14. [Key design decisions](#14-key-design-decisions)
15. [Troubleshooting and operational notes](#15-troubleshooting-and-operational-notes)

---

## 1. Repository purpose

### What this project is

EdgeDispatch is a **proof-of-concept backend and frontend** for a hybrid LLM
orchestration architecture. It is the deliverable for *CSE5001 Thesis A* (La
Trobe University, 2026): *EdgeDispatch: Hybrid LLM Orchestration for
Local-First AI Workflows*.

The system answers enterprise questions by routing each query to one of two
tiers:

| Tier | Model | When it runs | Cloud cost |
|------|-------|--------------|------------|
| **Local (edge)** | A small language model (SLM) served by `llama.cpp` | Queries that need fewer tools than the configured threshold | **Zero** cloud tokens |
| **Cloud (frontier)** | OpenAI `gpt-4o` (default) | Queries that meet or exceed the tool threshold | Billed, but only on a **compact handoff document**, never the full tool manifest |

### The problem it solves

A monolithic LLM deployment sends every tool schema, the query, and all
retrieved evidence to the cloud model on every request. As tool catalogues
grow, schema tokens dominate the prompt and the per-query cost climbs
regardless of query difficulty.

EdgeDispatch pushes tool selection and evidence gathering to a local SLM on
consumer hardware. The cloud model is invoked only for complex synthesis, and
even then it receives only a structured **handoff document** `H = {Q, Ŝ(Q),
rationale, E}` rather than the full schema set. The backend computes a
per-query cost breakdown (thesis Equations 2.4–2.6) against user-configurable
API pricing and surfaces tool-selection and answer-correctness metrics per
query.

### Intended users and stakeholders

| Stakeholder | What they get from this repo |
|-------------|------------------------------|
| **AI Engineers** | A working two-agent pipeline (local resolve / local escalate / cloud synthesise), MCP tool integration, per-query cost accounting, and Phoenix traces. |
| **Product Engineers** | A React chat UI with per-query cost badges, a settings dialog for threshold and pricing, and an SSE streaming protocol. |
| **Technical Business Analysts** | A transparent dispatch decision (heuristic + rationale), `GET /api/evaluations` for Tool F1 and a 6-point answer rubric, and a cost-savings figure per query. |
| **Thesis examiners** | A reproducible prototype aligned to thesis Sections 2.2, 4.5–4.7, with known divergences documented in [§14](#14-key-design-decisions). |

### High-level purpose

Demonstrate, end-to-end, that **conditional handoff from a local SLM to a
cloud frontier model (with the cloud model never seeing the full tool
manifest) produces measurable cost savings while preserving answer quality**,
and expose the dials (threshold, pricing) and the metrics (Tool F1, rubric,
cost) needed to evaluate that claim.

---

## 2. System overview

### Plain-English architecture

A user types a question in a React chat UI. The frontend posts it to the
FastAPI backend, which streams back Server-Sent Events. Before any LLM runs,
a Python **heuristic** (`MCPRouter.analyze_query`) estimates how many distinct
MCP tools the query needs and compares that against a configurable threshold.

- **Below threshold (D=0, local):** a *resolve-variant* local agent (SLM via
  `llama.cpp`) calls MCP tools and writes the final answer directly. Zero
  tokens reach the cloud.
- **At or above threshold (D=1, escalate):** an *escalate-variant* local agent
  calls MCP tools to gather evidence, then calls an `escalate_query` function
  that packages a handoff document. A cloud synthesis agent (OpenAI) receives
  **only** that document and produces the final answer.

After the run, the backend computes a cost breakdown (monolithic baseline vs
EdgeDispatch actual), evaluates tool selection and answer quality, logs the
evaluation, and emits everything in the SSE `done` event. The frontend renders
the answer token-by-token and shows a cost badge beneath it.

### How the frontend and backend fit together

```
┌────────────────────────────────────────────┐
│  Browser — React 19 + TS + Tailwind (:5173)│
│  Chat UI, cost badges, settings dialog     │
│  Conversations persisted in localStorage   │
└──────────────────┬─────────────────────────┘
                   │  SSE over POST /api/chat
                   │  (Vite proxies /api → :8000)
                   ▼
┌────────────────────────────────────────────┐
│  FastAPI backend (:8000)                   │
│  /api/chat · /api/conversations            │
│  /api/settings · /api/evaluations · health │
└──────────────────┬─────────────────────────┘
                   │
         EdgeDispatchOrchestrator.process_query()
                   │
      ┌────────────┴───────────────┐
      │  MCPRouter.analyze_query() │  ← authoritative dispatch
      │  heuristic → D ∈ {0,1}     │
      └────────────┬───────────────┘
             D=0   │   D=1
        ┌──────────┘   └──────────┐
        ▼                         ▼
  resolve agent            escalate agent
  (llama.cpp)              (llama.cpp)
  MCP tools → answer       MCP tools → escalate_query
  C_ED = 0                 → handoff H
                                   │
                                   ▼
                          cloud synthesiser
                          (OpenAI gpt-4o)
                          receives ONLY H
                          → final answer
                                   │
              MCP tools (3 stub servers, stdio):
              document_store · relational_db · policy_wiki
```

### Main runtime flow

1. **Frontend** posts `{ query, conversation_id, tool_threshold }` to
   `/api/chat`.
2. **Backend** emits an SSE `status` event ("Analyzing…"), then a `routing`
   status with the estimated tool count and route.
3. **Orchestrator** runs the selected agent variant (and the cloud synthesiser
   if escalated), then emits `token` events (chunked from the final answer).
4. **Backend** emits a `done` event carrying `was_escalated`, `tool_count`,
   `threshold`, `cost` (full breakdown), and `evaluation` (Tool F1 + rubric).
5. **Frontend** attaches the metadata to the assistant message and renders a
   `CostBadge`.

### AI-specific components

| Component | Location | Role |
|-----------|----------|------|
| Dispatch heuristic | `server/mcp_router.py` | Estimates tool count; selects route D ∈ {0,1}; builds handoff documents. |
| Local resolve agent | `server/agent_definition.py` `create_local_resolve_agent` | D=0 path: MCP tools + direct answer, no escalation tool. |
| Local escalate agent | `server/agent_definition.py` `create_local_escalate_agent` | D=1 path: MCP tools + `escalate_query` function tool. |
| Cloud synthesis agent | `server/agent_definition.py` `create_high_end_agent` | No tools; receives only the handoff prompt. |
| `escalate_query` tool | `agent_definition.py` `make_escalation_function` | Packages `H = {Q, Ŝ(Q), rationale, E}` into the run context. |
| Cost model | `server/cost.py` | Thesis Eq 2.4–2.6 with input+output pricing extension. |
| Evaluator | `server/observability.py` `ArizeEvaluator` | Tool F1, 6-point answer rubric, Phoenix instrumentation. |
| Tracing hooks | `server/observability.py` `EdgeDispatchHooks` | Per-turn token usage, tool-call records, agent lifecycle. |

---

## 3. Directory and file structure

```
thesis_code/
├── pyproject.toml              # Python project + dependencies (uv-managed)
├── uv.lock                     # Locked dependency versions (committed)
├── README.md                   # Operator runbook (setup, commands, troubleshooting)
├── architecture.md             # This file
├── .gitignore                  # Root ignores (see §8)
│
├── server/                     # FastAPI backend (Python 3.11+)
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, lifespan, MCP server lifecycle, CORS, /api/health
│   ├── config.py               # All env-var-overridable constants + MCP_SERVER_CONFIGS
│   ├── cost.py                 # CostModel, CostBreakdown, TokenUsage (thesis Eq 2.4–2.6)
│   ├── mcp_router.py           # MCPRouter heuristic, ToolManifestEntry, HandoffDocument
│   ├── agent_definition.py     # Three agents + EdgeDispatchOrchestrator + escalate_query
│   ├── observability.py        # EdgeDispatchHooks + ArizeEvaluator (Phoenix, rubric, F1)
│   ├── models/
│   │   └── schemas.py          # Pydantic request/response models (ChatRequest, Settings, …)
│   └── routes/
│       ├── __init__.py
│       ├── chat.py             # /api/chat (SSE), /api/conversations, /api/evaluations
│       └── settings.py         # /api/settings (GET/PUT) + in-memory settings store
│
├── mcp_server_docs/            # Stub MCP server: document store (search_documents, get_document)
│   ├── __init__.py
│   └── __main__.py
├── mcp_server_db/              # Stub MCP server: relational DB (db_lookup, db_search)
│   ├── __init__.py
│   └── __main__.py
├── mcp_server_wiki/            # Stub MCP server: policy wiki (wiki_search, wiki_get_page)
│   ├── __init__.py
│   └── __main__.py
│
└── frontend/                   # React 19 + TypeScript + Tailwind + Vite
    ├── package.json            # Scripts: dev, build, lint, preview
    ├── vite.config.ts          # Port 5173, `/api` proxy → :8000, `@` alias
    ├── tailwind.config.js      # Custom palette (edge-cyan, edge-violet, …)
    ├── tsconfig*.json
    ├── eslint.config.js
    ├── index.html
    └── src/
        ├── main.tsx            # React entry point
        ├── App.tsx             # Top-level state, layout, conversation orchestration
        ├── index.css           # Tailwind directives + theme tokens
        ├── lib/
        │   ├── types.ts        # Message, Conversation, Settings, CostBreakdown, StreamDone
        │   ├── api.ts          # fetchSettings, updateSettings, streamChat (SSE parser)
        │   └── utils.ts        # cn(), generateId(), formatTimestamp(), truncate()
        ├── hooks/
        │   ├── useChat.ts          # SSE state machine: messages, streaming, status
        │   └── useLocalStorage.ts  # Persisted state helper
        └── components/
            ├── Sidebar.tsx         # Conversation list, new chat, settings entry
            ├── ChatInterface.tsx   # Header, message list, empty state, input
            ├── ChatInput.tsx       # Auto-growing textarea, send/cancel
            ├── MessageBubble.tsx   # Avatar, content, timestamp, CostBadge
            └── SettingsDialog.tsx  # Threshold slider, pricing inputs, model info
```

### Where things live

| Concern | Location |
|---------|----------|
| Configuration & env vars | `server/config.py` |
| Shared types (frontend) | `frontend/src/lib/types.ts` |
| Shared models (backend) | `server/models/schemas.py` |
| Constants (thesis Table 2.1) | `server/cost.py` (`SCHEMA_TOKENS_PER_TOOL`, `INSTRUCTION_TOKENS`) |
| Build artefacts | `frontend/dist/` (gitignored), `.venv/` (gitignored) |
| Tests | **None present.** See [§12](#12-testing-and-quality). |
| Local model weights | `./models/` (gitignored, user-downloaded) |
| Phoenix SQLite | `~/.phoenix/` (outside the repo) |

> **Note:** `backend_spec.md`, `frontend_spec.md`, `EdgeDispatch_Thesis_A.md`,
> and `memory.md` exist in the working tree but are **gitignored**: they are
> private design documents, not part of the committed codebase.

---

## 4. Frontend architecture

### Organisation

A single-page React 19 application with **no router**: there is exactly one
view (the chat). State is split between `useLocalStorage` (persisted across
reload) and `useState` (ephemeral UI state).

### Key files and responsibilities

| File | Responsibility |
|------|----------------|
| `App.tsx` | Owns conversations, active id, threshold, pricing; wires `useChat`; renders `Sidebar` + `ChatInterface` + `SettingsDialog`. |
| `hooks/useChat.ts` | SSE state machine. Appends user + assistant messages, streams tokens into the assistant bubble, attaches `cost`/`wasEscalated`/`toolCount` on `done`, supports cancel via `AbortController`. |
| `hooks/useLocalStorage.ts` | Generic JSON persistence hook used for conversations, active id, threshold, and pricing. |
| `lib/api.ts` | `fetchSettings`, `updateSettings`, `deleteConversation`, and `streamChat` (a manual SSE reader that parses `event:`/`data:` blocks). Also `mapSettings`/`mapDone` for snake_case↔camelCase. |
| `lib/types.ts` | `Message`, `Conversation`, `Settings`, `StreamStatus`, `CostBreakdown`, `StreamDone`. |
| `components/MessageBubble.tsx` | Renders a message and its `CostBadge` (cyan `⌂ Local resolution` or violet `☁ Cloud synthesis`, with `c_ed`, `c_mono`, and `saved ΔC`). |
| `components/SettingsDialog.tsx` | Threshold slider (1–10), input/output $/M pricing inputs, model info cards; saves via `PUT /api/settings`. |
| `components/Sidebar.tsx` | Collapsible conversation list with delete-on-hover, "New conversation" button, settings entry. |
| `components/ChatInput.tsx` | Auto-growing textarea; Enter sends, Shift+Enter newlines, button toggles send/cancel. |

### State management

No external state library. State flows top-down from `App.tsx` via props, and
the `useChat` hook encapsulates the streaming lifecycle. Persistence is
explicit through `useLocalStorage`:

| Key | Stored value |
|-----|--------------|
| `edgedispatch-conversations` | `Conversation[]` |
| `edgedispatch-active-id` | `string \| null` |
| `edgedispatch-threshold` | `number` |
| `edgedispatch-price-input` | `number` |
| `edgedispatch-price-output` | `number` |

### Communication with the backend

- **REST:** `GET /api/settings`, `PUT /api/settings`, `GET /api/conversations`,
  `DELETE /api/conversations/{id}`, all via `fetch`.
- **Streaming:** `POST /api/chat` returns an SSE stream consumed manually in
  `streamChat` (`api.ts:71`). The `fetch` response body is read chunk-by-chunk,
  buffered, and split on double-newlines to recover `event:`/`data:` pairs.
- **Proxy:** Vite dev server proxies `/api/*` to `http://localhost:8000`
  (`vite.config.ts:13`). In production builds the same origin serves both, or a
  reverse proxy fronts the backend.

### Important UI flows

1. **Send a message** → `App.handleSend` → `useChat.sendMessage` →
   `streamChat` → SSE events update the assistant bubble; on `done` the cost
   badge is attached.
2. **Change threshold/pricing** → `SettingsDialog.handleSave` →
   `updateSettings` PUT → backend `_settings` store → mirrored to
   localStorage; takes effect on the next query.
3. **Switch conversation** → `Sidebar.onSelect` → `loadMessages` replaces the
   visible messages; the active id is persisted.

---

## 5. Backend architecture

### Organisation

A single FastAPI application in `server/main.py` with two routers under
`server/routes/`. The orchestrator is constructed once at startup and injected
into the chat routes via `chat.set_orchestrator()`.

### Request lifecycle

1. **Startup** (`lifespan` in `main.py`):
   - Build three `MCPServerStdio` from `MCP_SERVER_CONFIGS` (`config.py:76`).
   - `await server.connect()` for each; failures are logged and skipped.
   - Construct `EdgeDispatchOrchestrator` (which builds the manifest, both
     local-agent variants, the cloud agent, the cost model, the evaluator;
     instruments the OpenAI client; and **auto-launches Phoenix**).
   - Publish `mcp_server_count` to the settings store.
2. **Request** hits a route handler; validation is via Pydantic models
   (`server/models/schemas.py`).
3. **For `/api/chat`:** the handler yields SSE events from an async generator;
   the orchestrator runs the pipeline; the final answer is chunked into ~20
   `token` events followed by a `done` event.
4. **Shutdown:** `orchestrator.close()` releases providers; each MCP server is
   cleaned up (catching `BaseException` so `CancelledError` does not propagate).

### Routes

| Method | Path | Handler | Purpose |
|--------|------|---------|---------|
| `POST` | `/api/chat` | `chat.chat` | SSE stream: `status` → `routing` → `token`* → `done` (or `error`). |
| `GET` | `/api/conversations` | `chat.list_conversations` | List conversations, newest first. |
| `GET` | `/api/conversations/{id}` | `chat.get_conversation_by_id` | Fetch one conversation. |
| `DELETE` | `/api/conversations/{id}` | `chat.delete_conversation` | Delete one conversation. |
| `GET` | `/api/settings` | `settings.get_settings` | Threshold, pricing, model names, MCP count, Phoenix endpoint. |
| `PUT` | `/api/settings` | `settings.update_settings` | Partial update of threshold and/or pricing (validated ≥0/≥1). |
| `GET` | `/api/evaluations` | `chat.get_evaluations` | Aggregate summary + per-query evaluation records. |
| `GET` | `/api/health` | `main.health_check` | `{status, version, orchestrator_ready}`. |

### Services, middleware, and utilities

| Concern | Where | Notes |
|---------|-------|-------|
| CORS | `main.py:118` | Allows `localhost:5173`, `localhost:3000`, `127.0.0.1:5173`. |
| Request validation | Pydantic models in `models/schemas.py` | FastAPI auto-validates; 422 on schema mismatch. |
| Error handling | Route handlers + orchestrator try/except | Local-agent failures return an error result; cloud failures return an error string in the answer; SSE `error` event for unexpected exceptions. |
| Logging | `logging.basicConfig` in `main.py:31` | Level via `EDGE_LOG_LEVEL`; format `HH:MM:SS [LEVEL] name: msg`. |
| In-memory state | `_conversations` in `chat.py:31`, `_settings` in `settings.py:25`, `_evaluation_records` in `observability.py` | **Lost on restart.** Frontend conversations persist via localStorage. |
| Settings sync | `Orchestrator._sync_config_from_settings` | Pulls threshold + pricing from the settings store before every query so UI edits take effect immediately. |
| Caching | None beyond `MCPServerStdio(cache_tools_list=True)` | MCP tool lists are cached per server. |
| Background jobs | Phoenix runs in a background thread (`launch_app(run_in_thread=True)`) | No queue, no scheduler. |

### Validation, error handling, and logging

- Pydantic validates request bodies; FastAPI returns 422 on type errors.
- `update_settings` enforces `tool_threshold >= 1` and prices `>= 0` with
  explicit `HTTPException(400)`.
- The orchestrator wraps each agent run in `try/except`; failures do not crash
  the server. They surface as error text in the answer or as an SSE `error`
  event.
- The MCP cleanup path catches `BaseException` (with re-raise for
  `KeyboardInterrupt`/`SystemExit`) so asyncio `CancelledError` during shutdown
  does not break the loop.

---

## 6. End-to-end application flow

A typical request, layer by layer:

### Layer 1 — Browser

1. User types in `ChatInput`; Enter triggers `handleSend`.
2. `App.handleSend` auto-creates a conversation if none is active, then calls
   `useChat.sendMessage(content, convId, threshold)`.
3. `useChat` appends a user message and an empty assistant message, sets
   `isStreaming=true`, and calls `streamChat` (`api.ts:71`).
4. `streamChat` issues `POST /api/chat` with `{ query, conversation_id,
   tool_threshold }` and starts reading the SSE stream.

### Layer 2 — FastAPI route (`routes/chat.py`)

5. `chat()` validates the body, resolves `conversation_id` (generates a UUID
   if absent), stores the user message, and returns `EventSourceResponse`.
6. The async generator emits a `status` event ("Analyzing…"), then calls
   `_orchestrator.router.analyze_query` to pre-compute the route and emits a
   `routing` status event with the estimated tool count.
7. `await _orchestrator.process_query(query)` runs the full pipeline (Layer 3).
8. The final answer is split into ~20 word-chunks; each is emitted as a
   `token` event with a 30 ms delay (simulated streaming).
9. A `done` event is emitted with `was_escalated`, `tool_count`, `threshold`,
   `cost`, and `evaluation`.
10. The assistant message is stored in `_conversations`.

### Layer 3 — Orchestrator (`agent_definition.py`)

11. **Reset** `EdgeDispatchHooks` for this query (prevents cross-query
    token accumulation).
12. **Sync** threshold + pricing from the settings store (picks up UI edits).
13. **Dispatch** — `MCPRouter.analyze_query` estimates `N` (distinct tools
    needed) and selects `D ∈ {0,1}`.
14. **Run local agent** via `openai-agents` `Runner.run`:
    - D=0 → resolve variant (MCP tools, no escalation tool).
    - D=1 → escalate variant (MCP tools + `escalate_query`).
15. **If D=1:** `_prepare_handoff` extracts the handoff prompt from the run
    context (or builds a fallback handoff if the SLM did not call
    `escalate_query`, logging a non-compliance warning).
16. **Run cloud synthesiser** with the handoff prompt as input (no tools, no
    schemas).
17. **Compute cost** via `CostModel.compute` (thesis Eq 2.4–2.6) at current
    pricing, using observed token usage from the hooks.
18. **Evaluate** — `ArizeEvaluator.evaluate_tool_selection` (Tool F1) and
    `evaluate_answer_correctness` (6-point rubric).
19. **Log** the evaluation record and assemble trace data.
20. Return an `OrchestratorResult` with `final_answer`, `was_escalated`,
    `tool_count`, `cost`, and `evaluation`.

### Layer 4 — Back to the browser

21. `streamChat` parses each SSE block, dispatching to `onToken`, `onStatus`,
    `onDone`, or `onError`.
22. `useChat` appends tokens to the assistant message; on `done` it attaches
    `cost`, `wasEscalated`, and `toolCount`.
23. `MessageBubble` renders the `CostBadge` beneath the answer.
24. `App`'s `useEffect` syncs the updated messages back into the persisted
    conversations list.

---

## 7. Authentication and security

> **This repository does not implement authentication, authorisation, or
> multi-tenancy.** It is a single-user local-development prototype.

### Current security posture

| Concern | Current state |
|---------|---------------|
| Authentication | **None.** All endpoints are open. |
| Authorisation | **None.** Any caller can read/update settings, list conversations, send queries. |
| CORS | Whitelist of three localhost origins (`main.py:118`). Not a security control; only prevents casual browser cross-origin use. |
| API key storage | The OpenAI key is read from `OPENAI_API_KEY` / `EDGE_HIGH_END_API_KEY` env vars. It is never logged, never sent to the frontend, and `.env*` is gitignored. |
| Input validation | Pydantic validates request bodies; `update_settings` enforces non-negative pricing and `threshold ≥ 1`. Query strings are not sanitised beyond `.strip()` and a non-empty check. |
| Secrets in git | None. `.gitignore` excludes `.env*`, `*.pdf`, and the private design docs. |
| Rate limiting | None. |
| TLS | None. Plain HTTP on localhost. |

### Assumptions

- The backend runs on a trusted machine; the only client is the local
  browser dev server.
- The OpenAI key is supplied via environment; the operator is responsible for
  not committing it.
- The local `llama.cpp` server has no auth (its default); the backend
  authenticates with a placeholder `not-needed` key.

### Before any production use

Add an auth layer (e.g. FastAPI dependency with a bearer token), tighten CORS
to the real frontend origin, add rate limiting, sanitise long-form query input,
and front both services with TLS. The in-memory stores must also be replaced
(see [§14](#14-key-design-decisions)).

---

## 8. Configuration and environment

All configuration lives in `server/config.py` as module-level constants read
from environment variables with sensible defaults. There is no `.env` loader;
export variables in your shell or your shell profile.

### Required environment variables

Only one is **required** for full functionality:

| Variable | Required? | Purpose |
|----------|-----------|---------|
| `OPENAI_API_KEY` | **Yes** (for the cloud tier) | OpenAI API key. Falls back to `EDGE_HIGH_END_API_KEY`. Without it, escalated queries fail with an auth error; local queries still work. |

### Optional environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EDGE_LOCAL_BASE_URL` | `http://localhost:8080/v1` | `llama.cpp` server URL (OpenAI-compatible). |
| `EDGE_LOCAL_MODEL_NAME` | `local-model` | Model name sent in API requests. |
| `EDGE_LOCAL_API_KEY` | `not-needed` | Ignored by `llama.cpp`; kept for SDK compatibility. |
| `EDGE_HIGH_END_MODEL_NAME` | `gpt-4o` | Cloud model name. |
| `EDGE_HIGH_END_BASE_URL` | `https://api.openai.com/v1` | Cloud API endpoint (override for Azure etc.). |
| `EDGE_HIGH_END_API_KEY` | `$OPENAI_API_KEY` | Cloud API key. |
| `EDGE_TOOL_THRESHOLD` | `2` | Max MCP tool calls before escalation. |
| `EDGE_MAX_TURNS_LOCAL` | `10` | Local agent loop cap. |
| `EDGE_MAX_TURNS_HIGH_END` | `5` | Cloud agent loop cap. |
| `EDGE_TEMPERATURE_LOCAL` | `0.0` | Local sampling temperature. |
| `EDGE_TEMPERATURE_HIGH_END` | `0.3` | Cloud sampling temperature. |
| `EDGE_PRICE_INPUT_PER_MTOK` | `5.0` | Default input $/M tokens (UI-editable). |
| `EDGE_PRICE_OUTPUT_PER_MTOK` | `15.0` | Default output $/M tokens (UI-editable). |
| `ARIZE_PHOENIX_ENDPOINT` | `http://localhost:6006` | Phoenix collector URL; host/port parsed. |
| `ARIZE_API_KEY` | `""` | Arize cloud key (unused in local mode). |
| `ARIZE_PROJECT_NAME` | `EdgeDispatch` | Phoenix project name. |
| `EDGE_LOG_LEVEL` | `INFO` | Python logging level. |

### Local development configuration

- **Python:** managed by `uv`; `uv sync` creates `.venv/` from `uv.lock`.
  Requires Python 3.11+.
- **Node:** `frontend/package.json`; `npm install` creates `node_modules/`.
  Requires Node 18+.
- **Vite:** dev server on `:5173` with `/api` proxy to `:8000`
  (`vite.config.ts`).
- **MCP servers:** configured statically in `MCP_SERVER_CONFIGS`
  (`config.py:76`). To swap in real servers, edit that list or pass
  `MCPServer` instances to `EdgeDispatchOrchestrator(mcp_servers=...)`.

### Build, runtime, and deployment configuration

| Phase | Configuration |
|-------|---------------|
| **Build (frontend)** | `npm run build` → `tsc -b && vite build` → `frontend/dist/`. |
| **Build (backend)** | No build step; `uv run` executes from source. `pyproject.toml` declares `hatchling` as the build backend for wheel packaging. |
| **Runtime** | `uv run uvicorn server.main:app --reload --host 0.0.0.0 --port 8000` from the repo root (so `python -m mcp_server_*` resolves). |
| **Deployment** | None defined. No Dockerfile, no CI, no IaC. See [§13](#13-deployment-and-runtime). |

---

## 9. AI and data flow

### The dispatch decision (thesis Eq 2.1)

`MCPRouter.analyze_query` (`mcp_router.py:301`) is a **deterministic Python
heuristic**:

1. For each tool in the compact manifest, count keyword matches against the
   query (tool affinities + archetype keywords in `ARCHETYPE_KEYWORDS`).
2. Count multi-part query signals (` and `, `?`, regex for compound questions).
3. `estimated = max(matched_tools, distinct_sources, 1)`, bumped up for
   multi-part questions, capped at the manifest size.
4. `D = 1 if estimated >= threshold else 0`.

The heuristic (not the SLM) is authoritative. This diverges from the thesis
(see [§14](#14-key-design-decisions)) but makes dispatch deterministic and
decouples routing correctness from SLM compliance.

### Agent prompts

Three instruction templates in `agent_definition.py`:

- **`LOCAL_AGENT_RESOLVE_INSTRUCTIONS`** tells the SLM the query was routed
  local, gives it the compact tool manifest, and explicitly states it cannot
  escalate (no `escalate_query` tool is attached).
- **`LOCAL_AGENT_ESCALATE_INSTRUCTIONS`** tells the SLM the query requires
  cloud synthesis, gives it the manifest, and instructs it to gather evidence
  then call `escalate_query` with `{query, selected_tools, rationale,
  evidence_json}`.
- **`HIGH_END_AGENT_INSTRUCTIONS`** tells the cloud model it is a pure
  synthesiser, must use only the provided evidence, must not fabricate, and has
  no MCP tools.

The `tool_manifest` is interpolated into the local agents' instructions at
agent-construction time (`create_local_resolve_agent`, etc.).

### Model calls

- **Local:** `OpenAIProvider` pointing at `EDGE_LOCAL_BASE_URL`
  (`llama.cpp`, OpenAI-compatible). Model name `EDGE_LOCAL_MODEL_NAME`,
  temperature `0.0`. The `openai-agents` SDK `Runner.run` drives the agent
  loop (tool calls → LLM → tool calls … up to `MAX_TURNS_LOCAL`).
- **Cloud:** `OpenAIProvider` pointing at `EDGE_HIGH_END_BASE_URL`. Model
  `gpt-4o`, temperature `0.3`. Receives only the handoff prompt as input.

### Data flow per query

```
User query Q
    │
    ▼
MCPRouter.analyze_query(Q, manifest)
    │
    ├── estimated_tool_count N
    ├── required_tools Ŝ(Q)
    └── route_decision D ∈ {local, escalated}
    │
    ▼
Runner.run(starting_agent, input=Q, context=run_context)
    │
    ├── Local agent invokes MCP tools (search_documents, db_lookup, …)
    │   └── evidence E collected
    │
    └── [D=1 only] escalate_query() packages H = {Q, Ŝ(Q), rationale, E}
            and stores it in run_context
    │
    ▼ [D=1 only]
Runner.run(high_end_agent, input=H.to_prompt())
    │
    └── final_answer A
    │
    ▼
CostModel.compute(route, Q, E, handoff_text, cloud_usage, local_usage)
    ├── C_mono = (|S| + |Q| + |E| + |I|) priced at p_in + p_out
    ├── C_ED   = D · (|H| + |I|) priced (0 when D=0)
    └── ΔC     = C_mono − C_ED
    │
    ▼
ArizeEvaluator.evaluate_tool_selection + evaluate_answer_correctness
    │
    ▼
OrchestratorResult { final_answer, was_escalated, cost, evaluation, … }
```

### Inputs and outputs

- **Input (API):** `{ query: str, conversation_id?: str, tool_threshold?: int }`.
- **Output (SSE `done`):** `{ conversation_id, was_escalated, tool_count,
  threshold, cost: CostBreakdown, evaluation: { tool_selection, answer_correctness } }`.
- **Side outputs:** OpenInference spans to Phoenix; an evaluation record in
  `_evaluation_records` (surfaced via `GET /api/evaluations`).

### Cost model details (thesis Eq 2.4–2.6)

Constants from thesis Table 2.1: `SCHEMA_TOKENS_PER_TOOL = 240`,
`INSTRUCTION_TOKENS = 200`. Token estimation is `len(text) // 4` (the thesis
heuristic). The model extends the thesis by pricing output tokens separately:

```
C = (input_tokens · p_in + output_tokens · p_out) / 1_000_000
```

When `D=0`, `C_ED = 0` and `ΔC = C_mono` (full saving). When `D=1`, `C_ED`
uses the actual handoff size and observed cloud output tokens; `ΔC` is the
saving versus sending every schema to the cloud.

---

## 10. External integrations

| Integration | Where used | Why |
|-------------|------------|-----|
| **OpenAI API** (`openai` SDK) | `agent_definition.py` cloud agent; instrumented by `observability.py` | The cloud frontier model for synthesis (D=1). |
| **`llama.cpp` server** | `agent_definition.py` local agents via `OpenAIProvider` | Serves the local SLM with an OpenAI-compatible API on `:8080`. User-installed and user-started. |
| **`openai-agents` SDK** | `agent_definition.py`, `observability.py` | Agent abstraction, `Runner.run` loop, `RunHooks`, `function_tool`, `MCPServer` plumbing. |
| **MCP (Model Context Protocol)** | `server/main.py`, `mcp_server_*` | Tool exposure to the local agents. Three stub servers over stdio. |
| **`mcp.server.fastmcp.FastMCP`** | `mcp_server_*/*/__main__.py` | The library that turns a Python module into an MCP server speaking JSON-RPC over stdio. |
| **Arize Phoenix** | `server/observability.py` | Local trace store + UI. Auto-launched in a background thread. Stores data in a self-contained SQLite DB at `~/.phoenix/`. |
| **OpenInference** (`openinference-instrumentation-openai`) | `observability.py` `instrument_openai` | Wraps the OpenAI client so every cloud call becomes a Phoenix span. |
| **Hugging Face CLI** | README only (model download) | Fetches the GGUF model weights for `llama.cpp`. Not invoked by the app. |
| **FastAPI + `sse-starlette`** | `server/main.py`, `server/routes/chat.py` | HTTP server and SSE streaming. |
| **Vite + React + Tailwind** | `frontend/` | Frontend dev server, build, and styling. |

No databases, queues, caches, cloud storage, or third-party SaaS (beyond
OpenAI) are integrated. Phoenix's SQLite is the only persistent store and it
lives outside the repo.

---

## 11. Development workflow

### First-time setup

```bash
# 1. Python deps (from repo root)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# 2. Frontend deps
cd frontend && npm install && cd ..

# 3. Secrets + model config
export OPENAI_API_KEY="sk-..."
export EDGE_LOCAL_BASE_URL="http://localhost:8080/v1"
export EDGE_LOCAL_MODEL_NAME="qwen2.5-1.5b-instruct-q4_k_m"
```

### Common commands

| Task | Command | Where |
|------|---------|-------|
| Start backend (dev) | `uv run uvicorn server.main:app --reload --host 0.0.0.0 --port 8000` | Repo root |
| Start frontend (dev) | `npm run dev` | `frontend/` |
| Build frontend | `npm run build` | `frontend/` |
| Preview built frontend | `npm run preview` | `frontend/` |
| Lint frontend | `npm run lint` | `frontend/` |
| Typecheck frontend | `npx tsc --noEmit` | `frontend/` |
| Lint backend | `uv run ruff check .` | Repo root |
| Run a single MCP stub (sanity) | `uv run python -m mcp_server_db` | Repo root |
| Smoke-test the heuristic | See the inline script in `README.md` → "Try it without models" | Repo root |
| Run tests | `uv run pytest` (none exist yet) | Repo root |

### Where to make changes safely

| Change | File(s) to edit | Gotchas |
|--------|-----------------|---------|
| Dispatch heuristic / keyword tables | `server/mcp_router.py` (`ARCHETYPE_KEYWORDS`, `_estimate_distinct_tools`) | Affects routing for every query; sanity-test with the README script. |
| Tool threshold default | `server/config.py` (`DEFAULT_TOOL_THRESHOLD`) or env `EDGE_TOOL_THRESHOLD` | UI overrides persist in localStorage. |
| Pricing default | `server/config.py` (`DEFAULT_PRICE_INPUT_PER_MTOK` / `..._OUTPUT_...`) | UI edits mirror to the in-memory store and localStorage. |
| Agent instructions / prompts | `server/agent_definition.py` (`*_INSTRUCTIONS` constants) | The manifest is interpolated at agent construction; rebuilds require a backend restart. |
| Add an MCP server | Add a stub under `mcp_server_<name>/` and an entry in `MCP_SERVER_CONFIGS` (`config.py`) | The subprocess cwd is the repo root; the package must be importable as `python -m mcp_server_<name>`. |
| New API endpoint | Add a route in `server/routes/` and register it in `main.py` | Inject the orchestrator via `chat.set_orchestrator` or a similar pattern; do not import the orchestrator at module load. |
| Frontend types | `frontend/src/lib/types.ts` | Keep camelCase here; snake_case mapping belongs in `api.ts`. |
| Frontend SSE handling | `frontend/src/lib/api.ts` (`streamChat`) and `hooks/useChat.ts` | New `event` types need a branch in both files. |
| Theme / colours | `frontend/tailwind.config.js` (`edge-*` palette) | `index.css` references the same tokens. |

### Making changes — general guidance

- **Backend:** restart `uvicorn` (or rely on `--reload`). The orchestrator is
  constructed once at startup, so changes to agent definitions, the manifest,
  or MCP configs require a full restart.
- **Frontend:** Vite hot-reloads on save. Changes to `useLocalStorage` keys
  require clearing localStorage to see the new shape.
- **Settings:** threshold/pricing changes via the UI persist in both the
  backend store and localStorage. To reset, clear localStorage and restart the
  backend.

---

## 12. Testing and quality

### Current approach

**There is no automated test suite.** `pyproject.toml` lists `pytest` and
`ruff` as dev dependencies, but no test files, `conftest.py`, or `tests/`
directory exist. The frontend has `eslint` configured
(`eslint.config.js`) and TypeScript typechecking via `tsc -b`.

### Quality gates available

| Layer | Tool | Command | Status |
|-------|------|---------|--------|
| Backend syntax | `py_compile` / `python -c "import server.main"` | manual | Used during build verification. |
| Backend lint | `ruff` | `uv run ruff check .` | Available; not wired into a pre-commit. |
| Backend tests | `pytest` | `uv run pytest` | **No tests exist.** |
| Frontend types | `tsc` | `npx tsc --noEmit` | Available. |
| Frontend lint | `eslint` | `npm run lint` | Available. |
| Frontend tests | none | — | None configured (no Vitest/Jest). |
| End-to-end | manual | `curl /api/health`, `/api/settings`, send a chat message | Documented in README. |

### Manual verification patterns

- **No-model smoke test:** the routing heuristic and cost model can be
  exercised without any LLM running (see README → "Try it without models").
  Useful for validating dispatch changes.
- **Live smoke test:** `curl /api/health` and `curl /api/settings` (check
  `mcp_server_count == 3`); send the thesis example query and verify the cost
  badge appears.
- **Phoenix traces:** open `http://localhost:6006` to inspect OpenInference
  spans for cloud calls.

### Known gaps

- No unit tests for `MCPRouter.analyze_query`, `CostModel.compute`, or the
  `ArizeEvaluator` rubric, which are the highest-value targets for a first
  test pass.
- No integration test for the SSE streaming path.
- No frontend component tests.
- No CI pipeline. Lint and typecheck must be run manually.

---

## 13. Deployment and runtime

### Build artefacts

- **Frontend:** `npm run build` produces static assets in `frontend/dist/`
  (gitignored). Serve them with any static host or a reverse proxy in front of
  FastAPI.
- **Backend:** no build step. `uv run` executes from source against the
  project venv. For packaging, `pyproject.toml` declares `hatchling` with
  `packages = ["server"]`.

### Expected runtime environment

| Component | Default | Notes |
|-----------|---------|-------|
| Backend | `localhost:8000` | `uvicorn` with `--reload` for dev. |
| Frontend (dev) | `localhost:5173` | Vite dev server; proxies `/api` to `:8000`. |
| Frontend (prod) | Same origin as backend (or a static host + reverse proxy). | Not configured in this repo. |
| Local SLM | `localhost:8080` | `llama.cpp` server, OpenAI-compatible. User-operated. |
| Phoenix UI | `localhost:6006` | Auto-launched by the orchestrator. SQLite at `~/.phoenix/`. |

### Hosting and operational considerations

- **No containerisation.** There is no Dockerfile or `docker-compose.yml`.
  The expected environment is a developer machine (macOS or Linux).
- **State is in-memory.** Conversations (`chat.py:31`), settings
  (`settings.py:25`), and evaluation records (`observability.py`) are all
  Python dicts/lists. A backend restart wipes them. The frontend mitigates
  this for conversations by persisting to localStorage, but server-side
  history is ephemeral.
- **Phoenix data persists** at `~/.phoenix/` (outside the repo), so traces
  survive backend restarts.
- **MCP subprocesses** are spawned by the backend and must resolve
  `python -m mcp_server_*` from the repo root, so run the backend from the repo
  root, not from inside `server/`.
- **Scaling:** the single-process, in-memory design is intentionally limited
  to one user. Horizontal scaling would require externalising state, the MCP
  servers, and the Phoenix collector.

### Server start-up order

1. `llama.cpp` server (`:8080`) — optional but needed for any local route.
2. Backend (`:8000`) — auto-launches Phoenix (`:6006`) and the three MCP stubs.
3. Frontend (`:5173`) — proxies API calls to the backend.

---

## 14. Key design decisions

### 1. Heuristic-authoritative dispatch (diverges from thesis Eq 2.1)

The thesis makes the **SLM** authoritative for the dispatch decision. This
prototype makes the **Python heuristic** authoritative and implements it with
two local-agent variants (resolve / escalate). The heuristic picks which
variant runs, so the SLM cannot override the route.

The reason is determinism: evaluation needs reproducible routing, and SLM
function-calling compliance is unreliable on the off-the-shelf stand-in model.
The cost is losing the SLM's semantic understanding of query complexity.
Empirical comparison is deferred to Thesis B. If the escalate-variant SLM
fails to call `escalate_query`, the orchestrator builds a minimal handoff from
its raw output and logs a non-compliance warning (`agent_definition.py:676`).

### 2. Input + output pricing (extends the thesis)

The thesis cost model prices input tokens only. Real APIs bill input and
output separately, so `CostModel` extends Eq 2.4–2.6 with a per-million output
rate. Both rates are user-configurable in the UI. Without the output rate, the
per-query cost badge would understate real spend. The `C_mono` output estimate
is proxied from observed output (cloud when escalated, local when not), which
is an approximation.

### 3. Two local-agent variants instead of one with a conditional tool

A single agent with a conditional `escalate_query` tool would let the SLM
choose whether to escalate, re-introducing the SLM-authoritative path. Two
pre-configured variants make the heuristic's decision binding at the agent
construction level.

### 4. Stub MCP servers

Three in-memory stubs (`mcp_server_{docs,db,wiki}`) stand in for real
enterprise sources. They ship representative data (e.g. `REQ-2024-1052`, the
expedited-review policy) so the pipeline runs end-to-end without external
dependencies. This keeps the prototype self-contained and reproducible. The
trade-off is trivial tool behaviour (keyword search over a few hard-coded
records). Real MCP servers can be wired in via `MCP_SERVER_CONFIGS`.

### 5. Phoenix auto-launch

The orchestrator launches Phoenix in a background thread on construction
(`observability.py:369`). First run takes ~8 s for Alembic migrations to build
the SQLite schema at `~/.phoenix/`. This gives zero-config observability:
traces are captured without the operator starting a separate service. The
downside is startup latency and a port binding. Disable by removing the
`launch_phoenix()` call in `EdgeDispatchOrchestrator.__init__`, or point at an
existing Phoenix via `ARIZE_PHOENIX_ENDPOINT`.

### 6. In-memory state

Conversations, settings, and evaluation records are Python dicts. No database.
This minimises moving parts for a thesis prototype, but means no persistence
across restarts (except frontend localStorage), no concurrency beyond a single
user, and no audit trail.

### 7. Simulated token streaming

The backend waits for the full answer from the orchestrator, then chunks it
into ~20 `token` events with 30 ms delays (`chat.py:139`). It does not stream
the LLM output as it is produced, because `openai-agents` `Runner.run` returns
a completed result and true token streaming would require hooking the SDK's
streaming API. The UI shows streaming, but the first token arrives only after
the full pipeline completes.

### 8. Heuristic answer rubric

`ArizeEvaluator.evaluate_answer_correctness` uses string-matching heuristics
for the 6-point rubric (factual accuracy, evidence traceability, query
resolution, constraint discipline). The thesis prescribes a judge LLM or human
reviewers. Full evaluation is deferred to Thesis B.

---

## 15. Troubleshooting and operational notes

### Common issues and where to look

| Symptom | Likely cause | Where to look |
|---------|--------------|---------------|
| `ModuleNotFoundError: No module named 'agents'` | Not using the project venv | Run `uv sync`, prefix commands with `uv run`. |
| `ConnectionRefusedError` from `llama.cpp` | Local model not running or wrong URL | `curl $EDGE_LOCAL_BASE_URL/models`; check `EDGE_LOCAL_BASE_URL`. Backend still starts; local-routed queries will fail. |
| `mcp_server_count == 0` in `/api/settings` | MCP stubs failed to connect | Backend startup logs (`Failed to connect MCP server <name>`). Run `uv run python -m mcp_server_db` directly. Run backend from repo root. |
| `mcp_server_count < 3` | One stub failed; the others continued | Same logs; the manifest only contains tools from connected servers. |
| OpenAI auth error on escalated queries | `OPENAI_API_KEY` not set or invalid | `export OPENAI_API_KEY=...`; local queries still work. |
| Phoenix first-run warning: `server took too long to start` | Alembic migrations building `~/.phoenix/` (~8 s) | Non-fatal; spans still collected. Next start is fast. Pre-warm with `uv run python -c "import phoenix as px; px.launch_app(); input('press Enter')"` (see README). |
| Port 6006 in use | Phoenix or another process holds it | `lsof -i :6006` then `kill <pid>`, or set `ARIZE_PHOENIX_ENDPOINT=http://localhost:6007`. |
| CORS error in browser | Frontend origin not in the allow-list | Add the origin to `allow_origins` in `server/main.py:118` and restart. |
| TypeScript build errors | TS 5.7+ deprecation warnings | Ensure `tsconfig.app.json` has `"ignoreDeprecations": "6.0"`. |
| `huggingface-cli: command not found` | HF CLI not installed | `pip install huggingface_hub`. |
| Cost badge shows `saved $0.00` on an escalated query | Cloud output tokens or handoff size swamped the schema saving | Inspect `cost.c_ed` vs `cost.c_mono` in the `done` payload; check pricing in Settings. |
| Query routed local but the answer is an error | Local SLM failed (often function-calling non-compliance) | Backend logs (`Local agent failed: ...`); the orchestrator returns an error result, not a crash. |

### Debugging guidance

- **Routing decisions:** `MCPRouter._decision_history` records every
  `analyze_query` result; inspect via a debugger or add a temporary endpoint.
- **Token usage:** `EdgeDispatchHooks.get_token_usage()` aggregates per-turn
  input/output tokens; `get_summary()` returns the full trace summary.
- **Cloud calls:** every OpenAI call is an OpenInference span in Phoenix
  (`http://localhost:6006`). Filter by the `EdgeDispatch Pipeline` workflow
  name.
- **Evaluation records:** `GET /api/evaluations` returns the aggregate summary
  and per-query records (Tool F1, rubric scores, route, tool count).
- **Handoff compliance:** search the backend logs for
  `did not call escalate_query`, which indicates the SLM ignored the
  dispatcher's D=1 decision and the fallback handoff was used.
- **Settings drift:** the orchestrator syncs from the settings store at the
  start of every query (`_sync_config_from_settings`), so UI edits take effect
  on the next query without a restart. Env-var defaults only apply at first
  boot.

### Where to look when something breaks

| Layer | First stop |
|-------|-----------|
| Frontend render / state | `App.tsx`, `useChat.ts`, browser console + DevTools Application → localStorage. |
| API contract / SSE parsing | `frontend/src/lib/api.ts` (`streamChat`, `mapSettings`, `mapDone`) and `server/routes/chat.py`. |
| Routing logic | `server/mcp_router.py` (`analyze_query`, `ARCHETYPE_KEYWORDS`). |
| Agent behaviour | `server/agent_definition.py` (instructions, `make_escalation_function`, `_prepare_handoff`). |
| Cost numbers | `server/cost.py` (`CostModel.compute`, `SCHEMA_TOKENS_PER_TOOL`, `INSTRUCTION_TOKENS`). |
| Metrics | `server/observability.py` (`ArizeEvaluator`, `EdgeDispatchHooks`). |
| MCP tool exposure | `server/main.py` (`_build_mcp_servers`), `server/config.py` (`MCP_SERVER_CONFIGS`), and the `mcp_server_*` packages. |
| Observability UI | `http://localhost:6006` (Phoenix). |

---

*End of document. For setup steps and command examples, see
[`README.md`](./README.md).*
