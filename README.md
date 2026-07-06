# EdgeDispatch - Hybrid LLM Orchestration for Local-First AI Workflows

A two-tier, MCP-aware multi-agent system implementing the EdgeDispatch thesis
architecture: a local SLM (via `llama.cpp`) handles tool dispatch and simple
queries at zero cloud cost, and a cloud frontier model (OpenAI) is invoked only
when a query exceeds a configurable tool-call threshold - receiving a compact
handoff document instead of the full tool manifest.

The backend computes a per-query cost breakdown (thesis Eq 2.4–2.6) against
**user-configurable API pricing**, and surfaces tool-selection + answer
correctness metrics (thesis Sec 4.6–4.7) per query.

> Derived from: *EdgeDispatch: Hybrid LLM Orchestration for Local-First AI
> Workflows* (Fernandes, La Trobe University, CSE5001 Thesis A, 2026).

---

## Table of Contents

1. [Full Stack Runbook](#full-stack-runbook) - start here
2. [Architecture](#architecture)
3. [How Dispatch Works](#how-dispatch-works)
4. [Cost Model & Pricing](#cost-model--pricing)
5. [Prerequisites](#prerequisites)
6. [Backend Setup](#backend-setup)
7. [Frontend Setup](#frontend-setup)
8. [Local Model (llama.cpp)](#local-model-llamacpp)
9. [Cloud Model (OpenAI)](#cloud-model-openai)
10. [MCP Servers](#mcp-servers)
11. [Evaluation Metrics](#evaluation-metrics)
12. [Observability (Arize Phoenix)](#observability-arize-phoenix)
13. [Configuration Reference](#configuration-reference)
14. [Programmatic API](#programmatic-api)
15. [Project Structure](#project-structure)
16. [Troubleshooting](#troubleshooting)
17. [Known Limitations & Thesis B](#known-limitations--thesis-b)

---

## Full Stack Runbook

The fastest path to a running application. Each numbered step is a prerequisite
for the next. You will need **three terminals** (backend, local model, frontend)
plus an OpenAI API key for the cloud tier.

### Step 1 - Install uv (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2 - Install Python dependencies

From the repository root:

```bash
uv sync
```

### Step 3 - Set your OpenAI API key (cloud tier)

```bash
export OPENAI_API_KEY="sk-your-key-here"
```

> **Without this**, queries that escalate to the cloud tier will fail. Queries
> that resolve locally still work (provided Step 4 is done).

### Step 4 - Start the local model (Terminal 1)

The local agent needs a `llama.cpp` server with an OpenAI-compatible API. Full
instructions are in [Local Model (llama.cpp)](#local-model-llamacpp); the short
version:

```bash
# Install llama.cpp and the huggingface CLI (one-off)
brew install llama.cpp
pip install huggingface_hub

# Download a small function-calling model (~1 GB)
huggingface-cli downQwen/Qwen3.5-4B Qwen3.5-4B-Q4_K_M.gguf \
  --local-dir ./models

# Start the server (keep this terminal open)
llama-server \
  -m ./models/Qwen3.5-4B-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 \
  --ctx-size 8192 --n-gpu-layers 99
```

Verify it is serving:

```bash
curl http://localhost:8080/v1/models
```

Point the backend at it (add to your shell profile if you want this to persist):

```bash
export EDGE_LOCAL_BASE_URL="http://localhost:8080/v1"
export EDGE_LOCAL_MODEL_NAME="Qwen3.5-4B-Q4_K_M"
```

> **Skipping this step:** the backend will still start and settings/cost helpers
> will work, but chat dispatch requires the local SLM because the model chooses
> which MCP tools to call. See [Troubleshooting](#troubleshooting) for the
> fallback.

### Step 5 - Start the backend (Terminal 2)

From the repository root:

```bash
uv run uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
```

On startup the backend will:

1. Connect three stub MCP servers (document store, relational DB, policy wiki)
   as stdio subprocesses - no extra setup needed.
2. Instrument the OpenAI client with OpenInference spans.
3. **Auto-launch Arize Phoenix** at `http://localhost:6006`. The first run takes
   ~8 seconds because Phoenix builds its self-contained SQLite database at
   `~/.phoenix/` (see [Observability](#observability-arize-phoenix)). Subsequent
   starts are faster.

Verify the backend is healthy:

```bash
curl http://localhost:8000/api/health
# {"status":"ok","version":"0.1.0","orchestrator_ready":true}
```

Confirm the MCP servers connected and pricing defaults are in place:

```bash
curl http://localhost:8000/api/settings
# {"tool_threshold":2,"price_input_per_mtok":5.0,"price_output_per_mtok":15.0,
#  "local_model":"local-model","high_end_model":"gpt-4o",
#  "mcp_server_count":3,"arize_endpoint":"http://localhost:6006"}
```

`mcp_server_count` should read `3`. If it reads `0`, see
[Troubleshooting](#mcp-servers-fail-to-connect).

### Step 6 - Start the frontend (Terminal 3)

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The Vite dev server proxies `/api/*` to the
backend at `:8000`.

### Step 7 - Send a test message

In the browser, type the thesis example query:

> What is the status of approval REQ-2024-1052, and what does our compliance
> policy say about expedited reviews?

Expected behaviour:

- A status line says the local model is choosing tools and shows the current
  escalation threshold.
- The assistant answer appears, streamed token-by-token.
- A cost badge under the answer shows `☁ Cloud synthesis`, `C_ED`, the
  monolithic `C_mono`, and `saved ΔC`.

Then try a simple single-source query:

> What is the status of REQ-2024-1052?

This should route locally (`D=0`) and show a cyan `⌂ Local resolution` badge
with `0 tok to cloud`.

### Step 8 - Adjust pricing (optional)

Open **Settings** (gear icon, top right). Under **Cloud Pricing**, set the
input and output $/M token rates to match your current OpenAI pricing. The
change takes effect on the next query and is mirrored to localStorage.

You can also drag the **Tool Threshold** slider (1–10) to change how many tool
calls the dispatcher tolerates before escalating.

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│                  User (Browser)                   │
│         React + TypeScript + Tailwind             │
│   Chat + per-query cost badge + pricing settings  │
│   Chat history persisted in localStorage          │
└─────────────────────┬────────────────────────────┘
                      │ SSE streaming (/api/chat)
                      ▼
┌──────────────────────────────────────────────────┐
│              FastAPI Backend (:8000)              │
│  /api/chat (SSE)  /api/conversations              │
│  /api/settings (threshold + pricing)              │
│  /api/evaluations   /api/health                   │
└─────────────────────┬────────────────────────────┘
                      │
            Local SLM chooses MCP tools from M
            ── model-driven dispatch (Eq 2.1) ──
            │                     │
 actual calls < threshold  actual calls >= threshold
       D = 0 (local)         D = 1 (escalate)
            │                     │
            ▼                     ▼
┌──────────────────────┐  ┌───────────────────────────┐
│ Local resolve agent  │  │ Local escalate agent      │
│ (llama.cpp :8080)    │  │ (llama.cpp :8080)         │
│ MCP tools → answer   │  │ MCP tools → escalate_query│
│ Zero cloud tokens    │  │ → handoff doc H           │
└──────────────────────┘  └─────────────┬─────────────┘
                                        ▼
                          ┌───────────────────────────┐
                          │ Cloud synthesis agent     │
                          │ (OpenAI gpt-4o)           │
                          │ receives ONLY H           │
                          │ → final answer A          │
                          └───────────────────────────┘
             │
      ┌──────┴──────┐
      │  MCP Tools  │  (stdio, 3 stub servers)
      │  docs/db/wiki│
      └─────────────┘
```

**Core invariant:** the cloud model never receives the full tool manifest. It
only receives a handoff document `H = {Q, Ŝ(Q), rationale, E}`. All tool-schema
overhead is absorbed at the local tier at zero cloud cost.

### Stack
- **Frontend**: React 19 + TypeScript + Tailwind CSS 3 + Lucide icons
- **Backend**: FastAPI + `sse-starlette` + `openai-agents` SDK
- **Observability**: Arize Phoenix (auto-launched, self-contained SQLite) +
  OpenInference instrumentation
- **MCP**: three stub servers via `mcp.server.fastmcp` over stdio

---

## How Dispatch Works

The dispatch decision (thesis Eq 2.1: `(D, Ŝ(Q)) = Evaluate(Q, M)`) is made by
the local SLM. The backend gives the local agent the compact MCP manifest and
the UI-configured tool threshold; the agent chooses which MCP tools to invoke.
The backend then counts actual MCP calls and escalates when the model calls
`escalate_query` or when the actual call count reaches the threshold.

Each handoff evidence item records the invoked tool, why it was called, the
tool schema, and the returned result. If the threshold is reached but the local
model answers directly, the backend builds a fallback handoff from captured
tool-call records plus the local model output.

```
If actual MCP calls >= tool_threshold → D=1 (escalate) → cloud synthesis
If actual MCP calls <  tool_threshold → D=0 (local)    → answer at edge
```

---

## Cost Model & Pricing

Per-query cost is computed in `server/cost.py` against user-configurable API
pricing (thesis Eq 2.4–2.6), extended to price output tokens since real APIs
bill input and output separately:

```
C_mono = (|S| + |Q| + |E| + |I|) · p_in            # estimated monolithic baseline
C_ED   = D · (|H| + |I|) · p_in                     # EdgeDispatch (0 when D=0)
ΔC     = C_mono − C_ED                              # savings
```

with the realistic extension `C = (in·p_in + out·p_out) / 1_000_000`.
Constants `|S| = tools · 240 tok/tool` and `|I| = 200 tok` follow thesis
Table 2.1.

- **When local (D=0):** `C_ED = 0` (zero cloud tokens); `ΔC = C_mono`.
- **When escalated (D=1):** the cloud model receives only `H`; `C_ED` uses the
  handoff size + observed cloud output tokens; `ΔC` is the saving vs sending
  every schema to the cloud.

### Setting the price

Pricing is editable in the UI (**Settings → Cloud Pricing**) and persisted on
the backend (`PUT /api/settings`) plus mirrored to localStorage. It takes effect
on the next query. Defaults: input `$5.00/M`, output `$15.00/M` (overridable via
env). The per-query cost badge under each assistant message shows the route,
tokens sent to the cloud, `C_ED`, the monolithic `C_mono`, and `ΔC` saved.

---

## Prerequisites

| Component | Minimum | Notes |
|-----------|---------|-------|
| Python | 3.11+ | The `uv`-managed venv uses 3.13 in development |
| uv | Latest | Python package manager |
| Node.js | 18+ | For the frontend |
| npm | 9+ | Comes with Node |
| macOS / Linux | arm64 or x86_64 | Windows untested |
| RAM | 8 GB (16 GB recommended) | The local model is the main consumer |
| Disk | ~5 GB | Model weights + dependencies |
| OpenAI API key | Required for the cloud tier | Queries that escalate will fail without it |

---

## Backend Setup

```bash
uv sync
uv run uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
```

Run this from the **repository root** so the stub MCP servers (launched as
`python -m mcp_server_*` subprocesses) resolve correctly.

Verify:

```bash
curl http://localhost:8000/api/health
# {"status":"ok","version":"0.1.0","orchestrator_ready":true}
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Send a message; receive an SSE stream (`status`/`token`/`done`/`error`) |
| `GET` | `/api/conversations` | List all conversations |
| `GET` | `/api/conversations/{id}` | Get a conversation |
| `DELETE` | `/api/conversations/{id}` | Delete a conversation |
| `GET` | `/api/settings` | Get config (threshold, pricing, models, MCP count) |
| `PUT` | `/api/settings` | Update threshold and/or pricing (partial) |
| `GET` | `/api/evaluations` | Evaluation summary + per-query records (Tool F1, rubric) |
| `GET` | `/api/health` | Health check |

### SSE `done` event payload

```json
{
  "conversation_id": "abc123",
  "was_escalated": false,
  "tool_count": 1,
  "threshold": 2,
  "cost": {
    "route": "local",
    "c_mono": 0.0272, "c_ed": 0.0, "delta_c": 0.0272,
    "tokens_mono_in": 5440, "tokens_ed_in": 0,
    "schema_tokens_avoided": 1440
  },
  "evaluation": { "tool_selection": { "f1": 1.0, "...": "..." }, "answer_correctness": { "...": "..." } }
}
```

---

## Frontend Setup

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173 (proxies /api → :8000)
npm run build    # outputs dist/
```

The settings dialog exposes the **tool threshold slider** and **cloud pricing
inputs** (input/output $/M tokens). Each assistant message shows a cost badge
with the route taken and the saving vs the monolithic baseline.

---

## Local Model (llama.cpp)

The local agent requires a running `llama.cpp` server with an OpenAI-compatible
API. The backend assumes a function-calling-capable, fine-tuned SLM is served
here (the thesis trains Qwen3.5-2B via SFT + GRPO on xLAM-60k; training code is
not in this repo).

### Install llama.cpp and a model

```bash
# Install llama.cpp
brew install llama.cpp

# Install the Hugging Face CLI (needed to download the model)
pip install huggingface_hub

# Download a small function-calling model (~1 GB)
huggingface-cli download Qwen/Qwen3.5-4B Qwen3.5-4B-Q4_K_M.gguf \
  --local-dir ./models
```

### Start the server

```bash
llama-server \
  -m ./models/Qwen3.5-4B-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 \
  --ctx-size 8192 --n-gpu-layers 99
```

| Flag | Meaning |
|------|---------|
| `-m` | Path to the GGUF model file |
| `--host` | Bind address |
| `--port` | HTTP API port |
| `--ctx-size` | Context window in tokens |
| `--n-gpu-layers` | GPU layers (99 = all, for Apple Silicon / CUDA) |

Verify: `curl http://localhost:8080/v1/models`.

### Point the backend at it

```bash
export EDGE_LOCAL_BASE_URL="http://localhost:8080/v1"
export EDGE_LOCAL_MODEL_NAME="Qwen3.5-4B-Q4_K_M"
```

> The thesis expects a **GRPO-fine-tuned** Qwen3.5-2B (LoRA r=64, xLAM-60k).
> The off-the-shelf `Qwen2.5-1.5B-Instruct` above is a stand-in so the
> pipeline runs end-to-end; function-calling reliability will be lower than the
> fine-tuned model. See [Known Limitations](#known-limitations--thesis-b).

---

## Cloud Model (OpenAI)

```bash
export OPENAI_API_KEY="sk-your-key-here"
export EDGE_HIGH_END_MODEL_NAME="gpt-4o"     # default
```

For Azure or other OpenAI-compatible providers:

```bash
export EDGE_HIGH_END_BASE_URL="https://your-resource.openai.azure.com/..."
export EDGE_HIGH_END_API_KEY="your-key"
```

---

## MCP Servers

Three stub MCP servers provide the prototype source environment (thesis
Section 4.2): a document store, a relational database, and a policy wiki. They
live at the repo root as `mcp_server_docs/`, `mcp_server_db/`,
`mcp_server_wiki/` and are launched automatically as stdio subprocesses by
`main.py` from `MCP_SERVER_CONFIGS`.

| Server | Tools | Sample data |
|--------|-------|-------------|
| `mcp_server_docs` | `search_documents`, `get_document` | Qualification/spec/field-service reports |
| `mcp_server_db` | `db_lookup`, `db_search` | Approval records incl. `REQ-2024-1052` |
| `mcp_server_wiki` | `wiki_search`, `wiki_get_page` | Expedited-review policy, change-control, retention |

Sanity-check one in isolation (from the repo root, using the project venv):

```bash
uv run python -m mcp_server_db   # speaks MCP over stdio; Ctrl+C to quit
```

To swap in real MCP servers, edit `MCP_SERVER_CONFIGS` in `server/config.py`
or pass `MCPServer` instances to `EdgeDispatchOrchestrator(mcp_servers=...)`.

---

## Evaluation Metrics

The system computes thesis Section 4.6–4.7 metrics **per query** and exposes
them via `GET /api/evaluations` and the SSE `done` event.

### Tool Selection (RQ1)

```python
evaluator.evaluate_tool_selection(
    predicted_tools=["db_lookup", "wiki_search"],
    ground_truth_tools=["db_lookup", "wiki_search"],
)
# → {f1, precision, recall, invalid_tool_rate, ...}
```

Proof-of-concept target: **Tool F1 > 0.80**. In the prototype, tool-selection
records are derived from actual MCP calls captured by the runtime hooks; a
labelled enterprise set is reserved for Thesis B.

### Answer Correctness (6-point rubric, RQ3)

| Dimension | Range | Criterion |
|-----------|-------|-----------|
| D1: Factual accuracy | 0–2 | All claims supported by evidence |
| D2: Evidence traceability | 0–2 | Each claim traceable to a source |
| D3: Query resolution | 0–1 | Addresses all parts of the question |
| D4: Constraint discipline | 0–1 | No fabrication; states gaps honestly |

The prototype uses heuristic string matching; full evaluation (judge LLM or
human reviewers) is Thesis B.

### Try the manifest without models

Manifest/schema construction and the cost model work without any LLM running.
Dispatch itself requires the local model because tool choice is model-driven:

```bash
uv run python -c "
from types import SimpleNamespace
from server.mcp_router import MCPRouter
router = MCPRouter(threshold=2)
router.build_manifest_from_servers([
    SimpleNamespace(name='document_store'),
    SimpleNamespace(name='relational_db'),
    SimpleNamespace(name='policy_wiki'),
])
print(router.get_tool_schema('db_lookup'))
"
```

---

## Observability (Arize Phoenix)

Phoenix is **auto-launched** at `http://localhost:6006` when the orchestrator
starts, and the OpenAI client is instrumented with OpenInference spans so every
cloud call is captured as a trace. `EdgeDispatchHooks` records tool calls, agent
turns, and token usage; `ArizeEvaluator` stores per-query evaluation records
(available at `GET /api/evaluations`).

### Where Phoenix stores data

Phoenix uses a **self-contained local SQLite database** at `~/.phoenix/`. No
external database (Postgres, MySQL, etc.) is required - the schema is built
automatically via Alembic migrations on first run. That first-run migration
takes ~8 seconds; subsequent starts are fast because the schema already exists.

If you would rather not pay the first-run cost during backend startup, pre-warm
the database once:

```bash
uv run python -c "import phoenix as px; px.launch_app(); input('press Enter to quit')"
# wait for the UI to come up, then press Enter
```

### Disabling Phoenix

To point the backend at an already-running Phoenix (e.g. started in a separate
terminal with `uv run python -m phoenix serve`), set:

```bash
export ARIZE_PHOENIX_ENDPOINT="http://localhost:6006"
```

The orchestrator's `launch_phoenix()` is idempotent and will skip if Phoenix is
already running. To disable observability entirely, remove the
`launch_phoenix()` call in `EdgeDispatchOrchestrator.__init__`
(`server/agent_definition.py`).

---

## Configuration Reference

All settings live in `server/config.py` and are env-var overridable.

### Local model
| Variable | Default | Description |
|----------|---------|-------------|
| `EDGE_LOCAL_MODEL_NAME` | `local-model` | Model name in API requests |
| `EDGE_LOCAL_BASE_URL` | `http://localhost:8080/v1` | llama.cpp server URL |
| `EDGE_LOCAL_API_KEY` | `not-needed` | API key (ignored by llama.cpp) |
| `EDGE_TEMPERATURE_LOCAL` | `0.0` | Sampling temperature |

### Cloud model
| Variable | Default | Description |
|----------|---------|-------------|
| `EDGE_HIGH_END_MODEL_NAME` | `gpt-4o` | OpenAI model |
| `EDGE_HIGH_END_API_KEY` | `$OPENAI_API_KEY` | API key |
| `EDGE_HIGH_END_BASE_URL` | `https://api.openai.com/v1` | API endpoint |
| `EDGE_TEMPERATURE_HIGH_END` | `0.3` | Sampling temperature |

### Routing & pricing
| Variable | Default | Description |
|----------|---------|-------------|
| `EDGE_TOOL_THRESHOLD` | `2` | Max MCP tool calls before escalation |
| `EDGE_MAX_TURNS_LOCAL` | `10` | Max agent loop turns (local) |
| `EDGE_MAX_TURNS_HIGH_END` | `5` | Max agent loop turns (cloud) |
| `EDGE_PRICE_INPUT_PER_MTOK` | `5.0` | Default input $/M tokens (UI-editable) |
| `EDGE_PRICE_OUTPUT_PER_MTOK` | `15.0` | Default output $/M tokens (UI-editable) |

### Observability
| Variable | Default | Description |
|----------|---------|-------------|
| `ARIZE_PHOENIX_ENDPOINT` | `http://localhost:6006` | Phoenix collector URL (host:port parsed) |
| `ARIZE_API_KEY` | `""` | Arize cloud API key |
| `ARIZE_PROJECT_NAME` | `EdgeDispatch` | Project name |
| `EDGE_LOG_LEVEL` | `INFO` | Python logging level |

### Quick env setup

```bash
export EDGE_LOCAL_BASE_URL="http://localhost:8080/v1"
export EDGE_LOCAL_MODEL_NAME="Qwen3.5-4B-Q4_K_M"
export OPENAI_API_KEY="sk-your-key-here"
export EDGE_TOOL_THRESHOLD="2"
export EDGE_PRICE_INPUT_PER_MTOK="5.0"
export EDGE_PRICE_OUTPUT_PER_MTOK="15.0"
export EDGE_LOG_LEVEL="INFO"
```

---

## Programmatic API

```python
import asyncio
from server.agent_definition import EdgeDispatchOrchestrator
from agents.mcp import MCPServerStdio, MCPServerStdioParams

async def main():
    servers = [
        MCPServerStdio(
            params=MCPServerStdioParams(command="python", args=["-m", "mcp_server_db"]),
            name="relational_db",
        ),
    ]
    for s in servers:
        await s.connect()

    orch = EdgeDispatchOrchestrator(mcp_servers=servers, tool_threshold=2)
    result = await orch.process_query(
        "What is the status of REQ-2024-1052 and what does the policy say?"
    )

    print(f"Answer:    {result.final_answer}")
    print(f"Escalated: {result.was_escalated}")
    print(f"Tools:     {result.tool_count}")
    print(f"Cost:      {result.cost.as_dict()}")
    print(f"Eval:      {result.evaluation}")

    for s in servers:
        await s.cleanup()
    await orch.close()

asyncio.run(main())
```

### Key classes

| Class | Module | Purpose |
|-------|--------|---------|
| `EdgeDispatchOrchestrator` | `agent_definition.py` | Main pipeline coordinator |
| `MCPRouter` | `mcp_router.py` | Compact tool manifest, tool schemas, and handoff docs |
| `CostModel` / `CostBreakdown` | `cost.py` | Thesis Eq 2.4–2.6 cost computation |
| `EdgeDispatchHooks` | `observability.py` | RunHooks for tracing + token usage |
| `ArizeEvaluator` | `observability.py` | Tool F1, answer rubric, Phoenix integration |

---

## Project Structure

```
edge-dispatch/
├── pyproject.toml
├── uv.lock
├── README.md                      ← This file
├── memory.md                      ← Timestamped session journal
├── backend_spec.md                ← Backend blueprint
├── frontend_spec.md               ← Frontend blueprint
├── EdgeDispatch_Thesis_A.md       ← Source thesis (Markdown)
├── mcp_server_docs/               ← Stub MCP server: document store
├── mcp_server_db/                 ← Stub MCP server: relational DB
├── mcp_server_wiki/               ← Stub MCP server: policy wiki
├── server/                        ← Python backend
│   ├── main.py                    ← FastAPI app + MCP server lifecycle
│   ├── config.py                  ← Env-based configuration
│   ├── cost.py                    ← Thesis cost model (Eq 2.4–2.6)
│   ├── mcp_router.py              ← Tool manifest/schemas + handoff documents
│   ├── agent_definition.py        ← Model-driven local agent + cloud agent + orchestrator
│   ├── observability.py           ← Phoenix hooks + Arize evaluator
│   ├── models/schemas.py          ← Pydantic request/response models
│   └── routes/
│       ├── chat.py                ← /api/chat (SSE), /api/conversations, /api/evaluations
│       └── settings.py            ← /api/settings (threshold + pricing)
└── frontend/                      ← React frontend
    └── src/
        ├── lib/{types,api,utils}.ts
        ├── hooks/{useChat,useLocalStorage}.ts
        └── components/{Sidebar,ChatInterface,MessageBubble,ChatInput,SettingsDialog}.tsx
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'agents'`
```bash
uv sync
```
Then prefix backend commands with `uv run` so the project venv is used.

### `ConnectionRefusedError` from llama.cpp
The local model server is not running, or `EDGE_LOCAL_BASE_URL` points at the
wrong port. Verify:
```bash
curl http://localhost:8080/v1/models
```
Ensure `EDGE_LOCAL_BASE_URL` matches. The backend will still start without
llama.cpp, but queries routed to the local agent will fail.

### `huggingface-cli: command not found`
```bash
pip install huggingface_hub
```

### MCP servers fail to connect
The stub servers run as `python -m mcp_server_*` subprocesses with their working
directory set to the project root. Run the backend from the repo root so the
subprocess can resolve the packages. Test a stub directly:
```bash
uv run python -m mcp_server_db
```
If `mcp_server_count` in `/api/settings` reads `0`, check the backend startup
logs for `Failed to connect MCP server <name>` warnings. A failed server is
skipped (the backend continues with the others), so the manifest will only
contain tools from the servers that did connect.

### OpenAI API key not set
Queries that escalate to the cloud tier will fail with an auth error. Set:
```bash
export OPENAI_API_KEY="sk-your-actual-key"
```

### Phoenix first-run is slow or times out
The first launch builds a SQLite schema at `~/.phoenix/` via Alembic migrations
(~8 seconds). You may see a non-fatal `Failed to launch Phoenix: server took
too long to start` warning on the very first run - the orchestrator still
initialises and OpenInference spans are still collected. The next start will be
fast. To pre-warm the database, see
[Observability](#observability-arize-phoenix).

### Port 6006 already in use (Phoenix)
Phoenix auto-launches on startup. If the port is taken, either:
- kill the existing process (`lsof -i :6006` then `kill <pid>`), or
- set `ARIZE_PHOENIX_ENDPOINT` to a free port (e.g. `http://localhost:6007`)
  and restart the backend.

### CORS errors in the browser
The backend allows `localhost:5173` and `localhost:3000`. For another frontend
port, edit `allow_origins` in `server/main.py` and restart.

### TypeScript build errors
```bash
cd frontend && npx tsc --noEmit
```
Ensure `tsconfig.app.json` has `"ignoreDeprecations": "6.0"` if using
TypeScript 5.7+.

---

## Known Limitations & Thesis B

- **Dispatch authority:** dispatch is SLM-authoritative. The UI threshold is the
  manual control; the backend enforces it against actual MCP tool calls.
- **No trained SLM in this repo:** the backend assumes a fine-tuned model is
  served by llama.cpp. Training code (SFT + GRPO on xLAM-60k) is out of scope
  for this backend deliverable. The off-the-shelf Qwen2.5-1.5B-Instruct works
  as a stand-in but function-calling reliability will be lower than the
  GRPO-aligned model.
- **Rubric heuristics:** answer correctness uses string-matching heuristics; the
  thesis prescribes a judge LLM or human reviewers for full evaluation.
- **Tool-F1 ground truth:** currently compared against actual runtime MCP calls
  until a labelled enterprise query set exists.
- **In-memory state:** conversations and evaluation records are in-memory and
  reset on backend restart (frontend conversations persist via localStorage).
