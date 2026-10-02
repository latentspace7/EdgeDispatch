# Model artifacts

The application uses an **execution-decision LoRA adapter fine-tuned for four epochs** with the unchanged **LiquidAI/LFM2.5-2.6B** Q8_0 base. Model files live beside the code repository:

```text
repos/
├── thesis_code/
└── thesis_artifacts/
    ├── models/
    │   ├── lfm-base-Q8_0.gguf
    │   ├── execution-decision-adapter.gguf
    │   ├── export-manifest.json
    │   └── serving-check.json
    ├── adapter/
    │   ├── adapter_model.safetensors
    │   ├── adapter_config.json
    │   └── … tokenizer and checkpoint files
    ├── accepted_candidate_seal.json
    ├── selection_seal.json
    ├── classifier/
    ├── evaluation/
    ├── original_source/
    └── provenance/
```

The Hugging Face adapter and `execution-decision-adapter.gguf` are separate formats of the same four-epoch fine-tune. The base is not merged with either adapter.

The bundle is prepared locally for a later Hugging Face release. No public artifact download is configured yet. A code-only clone can install and build, but local inference requires this separate bundle.

## Identity and integrity

| Artifact | Identity |
| --- | --- |
| Base revision | `a4e00e83c0979ee9deb88d04b6360599fa956656` |
| Hugging Face adapter SHA-256 | `2e12ef974b6bf439795cdc526a0c5962edf139db9950d53f28d5eb1b3468e6b9` |
| GGUF adapter SHA-256 | `48da04a37b522c4aff20f05c35c347a35d8ae863f6dcbfcf06b36f3b7cb973ca` |
| Base GGUF SHA-256 | `dd1b81e1809192cc2e4bb19255f4d1b3b4a94fbcee0512fea05240f458c738dc` |
| Recorded llama.cpp revision | `749f688fcaa4c472ec034b08cb8a907c45cfaa02` |

The launcher verifies the accepted serving report and artifact hashes before starting. The backend also checks the loaded model, adapter, chat template and llama.cpp build. Absolute paths inside the original report remain provenance; the application resolves the retained files under `EDGE_ARTIFACTS_DIR` and verifies their original hashes.

Set `EDGE_ARTIFACTS_DIR` if the bundle is elsewhere. Restart the model and backend after changing artifact locations. Use the recorded llama.cpp revision to reproduce the accepted serving configuration; a different build is not automatically accepted.

The selected checkpoint includes its optimizer state because the original selection seal records its hash. Raw evaluation records and frozen source snapshots are retained externally for research traceability. They are not imported by the running app.

## Start the model

Build llama.cpp separately using its upstream instructions, then run from the code repository:

```bash
uv run --locked --env-file .env python -m server.agent.serving --llama-dir ../llama.cpp
```

`EDGE_CONTEXT_TOKENS=0` selects the model's automatic context. Set a lower context in `.env` for both model and backend if needed. The port follows `EDGE_LOCAL_BASE_URL`; `--port`, `--context-tokens` and `--gpu-layers` can override launcher defaults.

The serving checks in the bundle are recorded verification evidence. Moving files and checking hashes does not perform a new inference or provider evaluation.
