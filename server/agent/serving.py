from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

from .contract import BASE_MODEL, verify_artifacts
from .settings import AppConfig


def main() -> None:
    config = AppConfig()
    parser = argparse.ArgumentParser(
        description="Serve the accepted EdgeDispatch base model and execution-decision adapter."
    )
    parser.add_argument("--llama-dir", type=Path, required=True)
    parser.add_argument("--models", type=Path)
    parser.add_argument(
        "--port", type=int, default=urlsplit(config.local_url).port or 8080
    )
    parser.add_argument("--context-tokens", type=int, default=config.context_tokens)
    parser.add_argument("--gpu-layers", type=int, default=99)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.context_tokens < 0:
        parser.error("Port must be 1-65535 and context tokens must be non-negative")
    models = args.models.resolve() if args.models else Path(config.adapter_path).parent
    adapter = (
        models / "execution-decision-adapter.gguf"
        if args.models
        else Path(config.adapter_path)
    )
    report = models / "serving-check.json" if args.models else config.serving_report
    verify_artifacts(config.artifacts_dir, adapter, report)
    argv = [
        str(args.llama_dir.resolve() / "build/bin/llama-server"),
        "--model",
        str(models / "lfm-base-Q8_0.gguf"),
        "--lora",
        str(adapter),
        "--lora-init-without-apply",
        "--alias",
        BASE_MODEL,
        "--host",
        "127.0.0.1",
        "--cors-origins",
        "http://127.0.0.1:8000",
        "--port",
        str(args.port),
        "--ctx-size",
        str(args.context_tokens),
        "--fit",
        "off",
        "--parallel",
        "1",
        "--jinja",
        "--reasoning",
        "on",
        "--no-context-shift",
        "--n-gpu-layers",
        str(args.gpu_layers),
    ]
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
