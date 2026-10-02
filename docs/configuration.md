# Configuration

Copy [`.env.example`](../.env.example) to `.env` and run commands from the repository root. Keep credentials in the ignored `.env` file.

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Remote execution API key |
| `EDGE_HIGH_END_MODEL_NAME` | Remote model; the thesis used `gpt-5.6-luna` |
| `EDGE_ARTIFACTS_DIR` | Model bundle; defaults to the sibling `thesis_artifacts` folder |
| `EDGE_LOCAL_BASE_URL` | Local llama.cpp API, default `http://127.0.0.1:8080/v1` |
| `EDGE_REDIS_URL` | Coordination service, default `redis://127.0.0.1:6379/0` |
| `EDGE_DATA_DIR` | Local history and assessment storage, default `runtime_data/` |
| `EDGE_CONTEXT_TOKENS` | `0` for automatic model context; apply the same limit to model and backend |
| `EDGE_OUTPUT_TOKENS` | Per-call output allowance, default `1024` |
| `EDGE_MAX_MODEL_CALLS` | Agent execution limit, default `10` |
| `EDGE_CONCURRENCY` | Simultaneous executions, default `1` |
| `EDGE_DEFAULT_ESCALATION_POLICY` | `sticky_escalation` or `reconsider_each_turn` |

The four-epoch adapter uses `EDGE_DECISION_MODE=direct`. Advanced setups can override `EDGE_ADAPTER_PATH`, `EDGE_SERVING_REPORT` and `EDGE_MCP_CONFIG`; artifact identity checks still apply.

## Optional answer assessment

Background assessment is disabled by default. To enable Phoenix native correctness and faithfulness evaluation, install the optional packages and start the dashboard:

```bash
uv sync --locked --extra dev --extra quality
docker compose -f compose.phoenix.yaml up -d
```

Set these values in `.env`:

```dotenv
EDGE_QUALITY_ENABLED=true
EDGE_QUALITY_ALLOW_CONTENT=true
EDGE_QUALITY_MODEL=gpt-5.6-luna
PHOENIX_BASE_URL=http://127.0.0.1:6006
PHOENIX_PROJECT_NAME=edgedispatch
```

`EDGE_QUALITY_API_KEY` defaults to `OPENAI_API_KEY` when empty. `PHOENIX_API_KEY` is needed only for an authenticated Phoenix server. Optional `EDGE_QUALITY_RETRIEVAL_TOOLS` identifies the tools supplying retrieved evidence; the built-in default covers the airline demo.

Include `--extra quality` when starting the backend so uv retains those packages:

```bash
uv run --locked --extra quality --env-file .env uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Open [Phoenix](http://127.0.0.1:6006). Assessment sends answer and evidence content to the configured judge and can incur API charges. Existing history is not submitted automatically. Assessment scores do not change execution decisions.
