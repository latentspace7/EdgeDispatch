# EdgeDispatch - Architecture Reference

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
7. [Configuration and environment](#7-configuration-and-environment)
8. [AI and data flow](#8-ai-and-data-flow)
9. [External integrations](#9-external-integrations)
10. [Development workflow](#10-development-workflow)
11. [Key design decisions](#11-key-design-decisions)

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
| **Local (edge)** | A small language model (SLM) served by `llama.cpp` | Queries where the model does not call `escalate_query` and actual MCP calls stay below the configured threshold | **Zero** cloud tokens |
| **Cloud (frontier)** | OpenAI `gpt-4o` (default) | Queries where the model calls `escalate_query` or actual MCP calls meet/exceed the threshold | Billed, but only on a **compact handoff document**, never the full tool manifest |

### The problem it solves

A monolithic LLM deployment sends every tool schema, the query, and all
retrieved evidence to the cloud model on every request. As tool catalogues
grow, schema tokens dominate the prompt and the per-query cost climbs
regardless of query difficulty.

EdgeDispatch pushes tool selection and evidence gathering to a local SLM on
consumer hardware. The cloud model is invoked only for complex synthesis, and
even then it receives only a structured **handoff document** `H = {Q, Ŝ(Q),
rationale, E}` rather than the full tool manifest. Each evidence item can carry
the invoked tool's compact schema, reason, and result. The backend computes a
per-query cost breakdown (thesis Equations 2.4-2.6) against user-configurable
API pricing and surfaces tool-selection and answer-correctness metrics per
query.

### High-level purpose

Demonstrate, end-to-end, that **conditional handoff from a local SLM to a
cloud frontier model (with the cloud model never seeing the full tool
manifest) produces measurable cost savings while preserving answer quality**,
and expose the dials (threshold, pricing) and the metrics (Tool F1, rubric,
cost) needed to evaluate that claim.

---

## 2. System overview

### Architecture

A user types a question in a React chat UI. The frontend posts it to the
FastAPI backend, which streams back Server-Sent Events. The local SLM receives
the compact MCP manifest, chooses which MCP tools to invoke, and either answers
locally or calls `escalate_query`.

- **Below threshold (D=0, local):** the local agent invoked fewer actual MCP
  calls than the UI threshold and writes the final answer directly. Zero tokens
  reach the cloud.
- **At or above threshold (D=1, escalate):** the local agent called
  `escalate_query`, or the backend enforced escalation because actual MCP calls
  reached the threshold. A cloud synthesis agent (OpenAI) receives **only** the
  handoff document and produces the final answer.

After the run, the backend computes a cost breakdown (monolithic baseline vs
EdgeDispatch actual), evaluates tool selection and answer quality, logs the
evaluation, and emits everything in the SSE `done` event. The frontend renders
the answer token-by-token and shows a cost badge beneath it.

### Main runtime flow

1. **Frontend** posts `{ query, conversation_id, tool_threshold }` to
   `/api/chat`.
2. **Backend** emits an SSE `status` event showing that local model dispatch is
   running and the current threshold.
3. **Orchestrator** runs the local agent and, when escalated, streams cloud
   synthesis deltas as `token` events.
4. **Backend** emits a `done` event carrying `was_escalated`, `tool_count`,
   `threshold`, `cost` (full breakdown), `evaluation` (Tool F1 + rubric), and
   optional `handoff` details.
5. **Frontend** attaches the metadata to the assistant message and renders a
   `CostBadge`.

### Important Components

| Component | Location | Role |
|-----------|----------|------|
| Manifest/schema helper | `server/mcp_router.py` | Builds compact tool manifests, schemas, and handoff documents. |
| Local dispatch agent | `server/agent_definition.py` `create_local_dispatch_agent` | Model-driven path: MCP tools + `escalate_query` function tool. |
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
├── .gitignore                  # Root ignores (see §7)
│
├── server/                     # FastAPI backend (Python 3.11+)
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, lifespan, MCP server lifecycle, CORS, /api/health
│   ├── config.py               # All env-var-overridable constants + MCP_SERVER_CONFIGS
│   ├── cost.py                 # CostModel, CostBreakdown, TokenUsage (thesis Eq 2.4–2.6)
│   ├── mcp_router.py           # MCPRouter manifest/schema helpers, ToolManifestEntry, HandoffDocument
│   ├── agent_definition.py     # Local dispatch agent + cloud agent + EdgeDispatchOrchestrator
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

---

## 4. Frontend architecture

### Organisation

A single-page React 19 application with **no router**: there is exactly one
view (the chat). State is split between `useLocalStorage` (persisted across
reload) and `useState` (ephemeral UI state).

React Compiler is enabled in `vite.config.ts`, so components should use plain
functions and direct calculations by default. Avoid manual `useCallback`,
`useMemo`, or `React.memo` unless the code needs their semantics beyond render
memoization.

### Key files and responsibilities

| File | Responsibility |
|------|----------------|
| `App.tsx` | Owns conversations, active id, threshold, pricing; wires `useChat`; renders `Sidebar` + `ChatInterface` + `SettingsDialog`. |
| `hooks/useChat.ts` | SSE state machine. Appends user + assistant messages, streams tokens into the assistant bubble, attaches `cost`/`wasEscalated`/`toolCount` on `done`, supports cancel via `AbortController`. |
| `hooks/useLocalStorage.ts` | Generic JSON persistence hook used for conversations, active id, threshold, and pricing. |
| `lib/api.ts` | `fetchSettings`, `updateSettings`, `deleteConversation`, and `streamChat` (a manual SSE reader that parses `event:`/`data:` blocks). Also `mapSettings`/`mapDone` for snake_case↔camelCase. |
| `lib/types.ts` | `Message`, `Conversation`, `Settings`, `StreamStatus`, `CostBreakdown`, `StreamDone`. |
| `components/MessageBubble.tsx` | Renders a message, its `CostBadge`, and optional handoff details (`Tools invoked`, rationale, evidence, prompt). |
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

---

## 6. End-to-end application flow

A typical request, layer by layer:

### Layer 1  - Browser

1. User types in `ChatInput`; Enter triggers `handleSend`.
2. `App.handleSend` auto-creates a conversation if none is active, then calls
   `useChat.sendMessage(content, convId, threshold)`.
3. `useChat` appends a user message and an empty assistant message, sets
   `isStreaming=true`, and calls `streamChat` (`api.ts:71`).
4. `streamChat` issues `POST /api/chat` with `{ query, conversation_id,
   tool_threshold }` and starts reading the SSE stream.

### Layer 2  - FastAPI route (`routes/chat.py`)

5. `chat()` validates the body, resolves `conversation_id` (generates a UUID
   if absent), stores the user message, and returns `EventSourceResponse`.
6. The async generator emits a `status` event, then emits a `routing` status
   showing that the local model is choosing tools and displaying the current
   threshold.
7. The route consumes `_orchestrator.process_query_stream(query)`, which runs
   the full pipeline (Layer 3) and yields answer events.
8. `token` events are forwarded from the orchestrator stream. Escalated cloud
   synthesis uses native `openai-agents` streaming deltas; local dispatch text
   is buffered until the route decision is known so a threshold-triggered cloud
   answer is not mixed with a local draft.
9. A `done` event is emitted with `was_escalated`, `tool_count`, `threshold`,
   `cost`, `evaluation`, and optional `handoff` details.
10. The assistant message is stored in `_conversations`.

### Layer 3  - Orchestrator (`agent_definition.py`)

11. **Reset** `EdgeDispatchHooks` for this query (prevents cross-query
    token accumulation).
12. **Sync** threshold + pricing from the settings store (picks up UI edits).
13. **Run local agent** via `openai-agents` `Runner.run_streamed` with MCP
    tools and `escalate_query` available. The local stream is consumed for
    lifecycle/tool events while routing is decided.
14. **Dispatch** - the orchestrator counts actual MCP tool calls and sets
    `D ∈ {0,1}` from either the model's `escalate_query` call or the UI
    threshold.
15. **If D=1:** `_prepare_handoff` extracts the model handoff from the run
    context or builds a threshold fallback handoff from captured tool records
    and the local model output.
16. **Run cloud synthesiser** with the handoff prompt as input (no MCP tools,
    no full manifest). Cloud answer deltas are forwarded as SSE `token` events
    as the model produces them.
17. **Compute cost** via `CostModel.compute` (thesis Eq 2.4–2.6) at current
    pricing, using observed token usage from the hooks.
18. **Evaluate**  - `ArizeEvaluator.evaluate_tool_selection` (Tool F1) and
    `evaluate_answer_correctness` (6-point rubric).
19. **Log** the evaluation record and assemble trace data.
20. Return an `OrchestratorResult` with `final_answer`, `was_escalated`,
    `tool_count`, `cost`, and `evaluation`.

### Layer 4  - Back to the browser

21. `streamChat` parses each SSE block, dispatching to `onToken`, `onStatus`,
    `onDone`, or `onError`.
22. `useChat` appends tokens to the assistant message; on `done` it attaches
    `cost`, `wasEscalated`, `toolCount`, and optional `handoff`.
23. `MessageBubble` renders the `CostBadge` and, when enabled in settings, the
    handoff details beneath the answer.
24. `App`'s `useEffect` syncs the updated messages back into the persisted
    conversations list.

---

## 7. Configuration and environment

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
| **Deployment** | None defined. No Dockerfile, no CI, no IaC. |

---

## 8. AI and data flow

### The dispatch decision (thesis Eq 2.1)

The local SLM is authoritative for tool choice. `MCPRouter` builds a connected
tool catalog and schema registry from the MCP servers available at startup, but
does not score the query with keywords. Per-query tool selection and handoff
schema inclusion are dynamic.

1. The local agent receives the compact connected-tool catalog and current UI
   threshold.
2. The model invokes only the MCP tools it decides are necessary.
3. Runtime hooks count actual MCP calls.
4. `D = 1` if the model calls `escalate_query` or actual MCP calls reach the
   threshold; otherwise `D = 0`.
5. Handoff evidence includes `tool`, `reason`, `schema`, and `result` for each
   invoked tool. The schema entries are pulled dynamically from the registry for
   the tools actually invoked, not preselected at startup.

### Agent prompts

Instruction templates in `agent_definition.py`:

- **`LOCAL_AGENT_ESCALATE_INSTRUCTIONS`** is the model-driven local-agent
  prompt. It gives the SLM the compact manifest, tells it to record reasons and
  schemas for invoked tools, and instructs it to call `escalate_query` when the
  actual MCP tool-call threshold is reached.
- **`HIGH_END_AGENT_INSTRUCTIONS`** tells the cloud model it is a pure
  synthesiser, must use only the provided evidence, must not fabricate, and has
  no MCP tools.

The `tool_manifest` is interpolated into the local agent instructions at
agent-construction time.

### Model calls

- **Local:** `OpenAIProvider` pointing at `EDGE_LOCAL_BASE_URL`
  (`llama.cpp`, OpenAI-compatible). Model name `EDGE_LOCAL_MODEL_NAME`,
  temperature `0.0`. The `openai-agents` SDK `Runner.run_streamed` drives the
  agent loop (tool calls -> LLM -> tool calls ... up to `MAX_TURNS_LOCAL`).
- **Cloud:** `OpenAIProvider` pointing at `EDGE_HIGH_END_BASE_URL`. Model
  `gpt-4o`, temperature `0.3`. Receives only the handoff prompt as input.

### Data flow per query

```
User query Q
    │
    ▼
Runner.run_streamed(local_dispatch_agent, input=Q, context=run_context)
    │
    ├── model-selected MCP calls
    ├── optional escalate_query call
    └── EdgeDispatchHooks actual_mcp_calls
    │
    ▼
Orchestrator compares actual_mcp_calls with threshold
    │
    ├── [D=0] use local final answer
    │
    └── [D=1] use model handoff or build threshold fallback
            H = {Q, Ŝ(Q), rationale, E}
    │
    ▼ [D=1 only]
Runner.run_streamed(high_end_agent, input=H.to_prompt())
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
  threshold, cost: CostBreakdown, evaluation: { tool_selection, answer_correctness },
  handoff?: { query, selected_tools, rationale, evidence, tool_threshold, prompt } }`.
- **Handoff evidence:** each invoked-tool entry should include `tool`, `reason`,
  `schema`, and `result`; threshold fallback handoffs also include
  `local_model_output`.
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

## 9. External integrations

| Integration | Where used | Why |
|-------------|------------|-----|
| **OpenAI API** (`openai` SDK) | `agent_definition.py` cloud agent; instrumented by `observability.py` | The cloud frontier model for synthesis (D=1). |
| **`llama.cpp` server** | `agent_definition.py` local agents via `OpenAIProvider` | Serves the local SLM with an OpenAI-compatible API on `:8080`. User-installed and user-started. |
| **`openai-agents` SDK** | `agent_definition.py`, `observability.py` | Agent abstraction, `Runner.run_streamed` loop, `RunHooks`, `function_tool`, `MCPServer` plumbing. |
| **MCP (Model Context Protocol)** | `server/main.py`, `mcp_server_*` | Tool exposure to the local dispatch agent. Three stub servers over stdio. |
| **`mcp.server.fastmcp.FastMCP`** | `mcp_server_*/*/__main__.py` | The library that turns a Python module into an MCP server speaking JSON-RPC over stdio. |
| **Arize Phoenix** | `server/observability.py` | Local trace store + UI. Auto-launched in a background thread. Stores data in a self-contained SQLite DB at `~/.phoenix/`. |
| **OpenInference** (`openinference-instrumentation-openai`) | `observability.py` `instrument_openai` | Wraps the OpenAI client so every cloud call becomes a Phoenix span. |
| **Hugging Face CLI** | README only (model download) | Fetches the GGUF model weights for `llama.cpp`. Not invoked by the app. |
| **FastAPI + `sse-starlette`** | `server/main.py`, `server/routes/chat.py` | HTTP server and SSE streaming. |
| **Vite + React + Tailwind** | `frontend/` | Frontend dev server, build, and styling. React Compiler is wired through `@vitejs/plugin-react` + `@rolldown/plugin-babel`. |

No databases, queues, caches, cloud storage, or third-party SaaS (beyond
OpenAI) are integrated. Phoenix's SQLite is the only persistent store and it
lives outside the repo.

---

## 10. Development workflow

### First-time setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

cd frontend && npm install && cd ..

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
| Smoke-test manifest/schema helpers | See the inline script in `README.md` → "Try the manifest without models" | Repo root |
| Run tests | `uv run pytest` (none exist yet) | Repo root |

### Where to make changes safely

| Change | File(s) to edit | Gotchas |
|--------|-----------------|---------|
| Tool catalog / schemas | `server/mcp_router.py` (`build_manifest_from_servers`, `get_tool_schema`) | Startup builds the connected-tool registry; per-query handoffs include schemas only for actually invoked tools. |
| Tool threshold default | `server/config.py` (`DEFAULT_TOOL_THRESHOLD`) or env `EDGE_TOOL_THRESHOLD` | UI overrides persist in localStorage. |
| Pricing default | `server/config.py` (`DEFAULT_PRICE_INPUT_PER_MTOK` / `..._OUTPUT_...`) | UI edits mirror to the in-memory store and localStorage. |
| Agent instructions / prompts | `server/agent_definition.py` (`*_INSTRUCTIONS` constants) | The manifest is interpolated at agent construction; rebuilds require a backend restart. |
| Add an MCP server | Add a stub under `mcp_server_<name>/` and an entry in `MCP_SERVER_CONFIGS` (`config.py`) | The subprocess cwd is the repo root; the package must be importable as `python -m mcp_server_<name>`. |
| New API endpoint | Add a route in `server/routes/` and register it in `main.py` | Inject the orchestrator via `chat.set_orchestrator` or a similar pattern; do not import the orchestrator at module load. |
| Frontend types | `frontend/src/lib/types.ts` | Keep camelCase here; snake_case mapping belongs in `api.ts`. |
| Frontend SSE handling | `frontend/src/lib/api.ts` (`streamChat`) and `hooks/useChat.ts` | New `event` types need a branch in both files. |
| Theme / colours | `frontend/tailwind.config.js` (`edge-*` palette) | `index.css` references the same tokens. |

### Making changes  - general guidance

- **Backend:** restart `uvicorn` (or rely on `--reload`). The orchestrator is
  constructed once at startup, so changes to agent definitions or MCP configs
  require a full restart. Per-query selected tools and handoff schema subsets
  are generated dynamically during each chat run.
- **Frontend:** Vite hot-reloads on save. Changes to `useLocalStorage` keys
  require clearing localStorage to see the new shape.
- **Settings:** threshold/pricing changes via the UI persist in both the
  backend store and localStorage. To reset, clear localStorage and restart the
  backend.

---

## 11. Key design decisions

### 11.1 Model-driven dispatch with threshold enforcement

The thesis makes the **SLM** authoritative for the dispatch decision. This
prototype now follows that direction: the local model chooses MCP tools from
the compact manifest and can call `escalate_query` itself.

The UI threshold remains the manual control. The backend counts actual MCP tool
calls from runtime hooks and escalates when the count reaches the threshold,
even if the SLM answered directly. In that fallback path, the orchestrator
builds a handoff from captured tool records and the local output.

### 11.2 Input + output pricing (extends the thesis)

The thesis cost model prices input tokens only. Real APIs bill input and
output separately, so `CostModel` extends Eq 2.4–2.6 with a per-million output
rate. Both rates are user-configurable in the UI. Without the output rate, the
per-query cost badge would understate real spend. The `C_mono` output estimate
is proxied from observed output (cloud when escalated, local when not), which
is an approximation.

### 11.3 Single local dispatch agent with `escalate_query`

The active path uses one local dispatch agent with MCP tools and
`escalate_query` available. This keeps semantic tool choice in the local model
while preserving a deterministic threshold override in the backend.

### 11.4 Stub MCP servers

Three in-memory stubs (`mcp_server_{docs,db,wiki}`) stand in for real
enterprise sources. They ship representative HR leave data (for example
`EMP-1001`, leave-balance documents, employee records, and policy wiki pages)
so the pipeline runs end-to-end without external dependencies. This keeps the
prototype self-contained and reproducible. The trade-off is trivial tool
behaviour over a few hard-coded records. Real MCP servers can be wired in via
`MCP_SERVER_CONFIGS`.

### 11.5 Phoenix auto-launch

The orchestrator launches Phoenix in a background thread on construction
(`observability.py:369`). First run takes ~8 s for Alembic migrations to build
the SQLite schema at `~/.phoenix/`. This gives zero-config observability:
traces are captured without the operator starting a separate service. The
downside is startup latency and a port binding. Disable by removing the
`launch_phoenix()` call in `EdgeDispatchOrchestrator.__init__`, or point at an
existing Phoenix via `ARIZE_PHOENIX_ENDPOINT`.

### 11.6 In-memory state

Conversations, settings, and evaluation records are Python dicts. No database.
This minimises moving parts for a thesis prototype, but means no persistence
across restarts (except frontend localStorage), no concurrency beyond a single
user, and no audit trail.
