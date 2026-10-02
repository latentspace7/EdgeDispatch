# Reproducing the final experiment

The execution-decision LoRA was fine-tuned for four epochs on 1,020 training
examples, with 199 development examples. It learns `LOCAL` or `ESCALATE`; the
untouched base model executes local tasks. A new training run never replaces the
submitted model automatically.

The datasets, selected adapter, original source snapshots and recorded evaluations
live in the sibling `../thesis_artifacts/` directory. They are excluded from the
application repository. The artifact package must be available locally before
running these commands from the repository root.

## Inspect the recorded results

```bash
python3 training/evaluate.py
```

This verifies the saved source hashes and recomputes the prospective classifier
metrics and the 50-question, 150-attempt answer evaluation. It makes no model or
provider calls and needs only Python. Use `--artifacts-dir PATH` for another
artifact location.

## Train the fresh adapter

The training environment is separate from the FastAPI application. Install the
GPU dependencies on a machine with one CUDA GPU:

```bash
uv sync --project training --locked --extra gpu
```

Use the pinned Hugging Face snapshot of `LiquidAI/LFM2.5-2.6B` at revision
`a4e00e83c0979ee9deb88d04b6360599fa956656`. `MODEL_SNAPSHOT` must point to its
`snapshots/<revision>` directory. Validate complete input/tokenizer identities,
then probe training and development memory before running four epochs:

```bash
uv run --project training --locked --extra gpu training/train.py
uv run --project training --locked --extra gpu training/train.py \
  --model-dir "$MODEL_SNAPSHOT" --output ../thesis_artifacts/memory-probe \
  --memory-probe
uv run --project training --locked --extra gpu training/train.py \
  --model-dir "$MODEL_SNAPSHOT" --output ../thesis_artifacts/reproduction \
  --memory-report ../thesis_artifacts/memory-probe/memory_probe.json \
  --execute-training
```

Each output directory must be new. The trainer retains the original fresh-arm
objective, class weights, seed, batch accumulation, learning-rate schedule,
complete untruncated prompts and per-epoch development scoring. Original scripts
and sealed reports are
preserved in the artifact package; the dependency lock here describes the current
reproduction environment. A new run does not replace the submitted measurements.
