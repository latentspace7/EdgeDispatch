from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from collections import Counter
from pathlib import Path

import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.agent.contract import (
    BASE_MODEL,
    BASE_REVISION,
    decision_messages,
    decision_prefix,
)

ROUTES = {"LOCAL", "ESCALATE"}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def request_for(row):
    messages = row["messages"]
    if len(messages) != 3 or row["target"] not in ROUTES:
        raise ValueError("Expected three outer messages and a plain route target")
    request = json.loads(messages[1]["content"])
    expected = decision_messages(request) + [
        {"role": "assistant", "content": row["target"]}
    ]
    if messages != expected:
        raise ValueError("Changed plain-label routing contract")
    return request


def validate_groups(train, dev):
    for rows, split in ((train, "training_diagnostic_only"), (dev, "development")):
        if not rows or any(r["split"] != split for r in rows):
            raise ValueError(
                "Wrong split; development must never enter gradient training"
            )
        requests = [
            json.dumps(request_for(r), sort_keys=True, ensure_ascii=False) for r in rows
        ]
        if len(set(requests)) != len(requests):
            raise ValueError("Duplicate exact routing request")
    if any(r.get("eligible_for_training") is not False for r in dev):
        raise ValueError("Development rows must be explicitly training-ineligible")
    if not all(r.get("eligible_for_development_evaluation") is True for r in dev):
        raise ValueError("Unreviewed development labels")
    for key in ("entities", "family"):
        a = {x for r in train for x in (r[key] if key == "entities" else [r[key]])}
        b = {x for r in dev for x in (r[key] if key == "entities" else [r[key]])}
        if a & b:
            raise ValueError(f"Cross-split {key} overlap")
    a = {json.dumps(request_for(r), sort_keys=True) for r in train}
    b = {json.dumps(request_for(r), sort_keys=True) for r in dev}
    if a & b:
        raise ValueError("Cross-split exact request overlap")


def encode_row(tokenizer, row, limit):
    request = request_for(row)
    encoded = tokenizer.apply_chat_template(
        row["messages"],
        tokenize=True,
        add_generation_prompt=False,
        return_dict=True,
        return_assistant_tokens_mask=True,
    )
    ids, mask = encoded["input_ids"], encoded["assistant_masks"]
    if len(ids) != len(mask) or not any(mask):
        raise ValueError("Missing assistant-only mask")
    start = mask.index(1)
    if not start or not all(mask[start:]) or any(mask[:start]):
        raise ValueError("Assistant supervision must be one contiguous suffix")
    prefix = tokenizer.encode(decision_prefix(request), add_special_tokens=False)
    if ids[:start] != prefix:
        raise ValueError("HF training tokens differ from the plain runtime prefix")
    if tokenizer.decode(ids[start:]) != row["target"] + "<|im_end|>\n":
        raise ValueError("Unexpected target suffix or reasoning prefill")
    if len(ids) > limit:
        raise ValueError(
            f"Overlength {row['id']}: {len(ids)} > {limit}; truncation prohibited"
        )
    return {
        "input_ids": ids,
        "labels": [x if m else -100 for x, m in zip(ids, mask)],
        "prefix_ids": prefix,
    }


def collate(features, weights, pad_id):
    length = max(len(x["input_ids"]) for x in features)
    return {
        "input_ids": torch.tensor(
            [
                x["input_ids"] + [pad_id] * (length - len(x["input_ids"]))
                for x in features
            ]
        ),
        "attention_mask": torch.tensor(
            [
                [1] * len(x["input_ids"]) + [0] * (length - len(x["input_ids"]))
                for x in features
            ]
        ),
        "labels": torch.tensor(
            [x["labels"] + [-100] * (length - len(x["labels"])) for x in features]
        ),
        "example_weights": torch.tensor(weights, dtype=torch.float32),
    }


def weighted_suffix_loss(logits, labels, example_weights):
    shifted = labels[:, 1:]
    keep = shifted != -100
    counts = keep.sum(dim=1)
    if (counts == 0).any() or logits.shape[:2] != labels.shape:
        raise ValueError("Each example must contain shifted supervised suffix tokens")
    if (
        example_weights.shape != counts.shape
        or not torch.isfinite(example_weights).all()
        or (example_weights <= 0).any()
    ):
        raise ValueError("Invalid per-example class weights")
    losses = F.cross_entropy(
        logits[:, :-1][keep].float(), shifted[keep], reduction="none"
    )
    example_ids = torch.arange(labels.shape[0], device=labels.device)[
        :, None
    ].expand_as(shifted)[keep]
    sums = torch.zeros(
        labels.shape[0], device=logits.device, dtype=losses.dtype
    ).scatter_add(0, example_ids, losses)
    return ((sums / counts) * example_weights).mean()


def metrics(rows, predictions):
    if len(rows) != len(predictions):
        raise ValueError("Missing predictions")
    n = Counter(r["target"] for r in rows)
    hits = Counter(r["target"] for r, p in zip(rows, predictions) if p == r["target"])
    missed = sum(
        r["target"] == "ESCALATE" and p != "ESCALATE" for r, p in zip(rows, predictions)
    )
    false = sum(
        r["target"] == "LOCAL" and p == "ESCALATE" for r, p in zip(rows, predictions)
    )
    valid_escalations = sum(p == "ESCALATE" for p in predictions)
    return {
        "rows": len(rows),
        "class_counts": dict(n),
        "balanced_accuracy": (
            hits["LOCAL"] / n["LOCAL"] + hits["ESCALATE"] / n["ESCALATE"]
        )
        / 2
        if all(n[x] for x in ROUTES)
        else None,
        "failure_recall": hits["ESCALATE"] / n["ESCALATE"] if n["ESCALATE"] else None,
        "missed_failures": missed,
        "escalations_on_local_success": false,
        "false_escalation_rate": false / n["LOCAL"] if n["LOCAL"] else None,
        "local_success_recall": hits["LOCAL"] / n["LOCAL"] if n["LOCAL"] else None,
        "escalate_precision": hits["ESCALATE"] / valid_escalations
        if valid_escalations
        else None,
        "valid_escalations": valid_escalations,
        "valid_locals": sum(p == "LOCAL" for p in predictions),
        "invalid": sum(p not in ROUTES for p in predictions),
        "confusion": dict(
            Counter(
                f"{r['target']} -> {p if p in ROUTES else 'INVALID'}"
                for r, p in zip(rows, predictions)
            )
        ),
    }


def grouped_metrics(rows, predictions, seed=424244, resamples=2000):
    result = metrics(rows, predictions)
    for key in ("family", "position", "session"):
        result["by_" + key] = {}
        for value in sorted({r[key] for r in rows}, key=str):
            idx = [i for i, r in enumerate(rows) if r[key] == value]
            result["by_" + key][str(value)] = metrics(
                [rows[i] for i in idx], [predictions[i] for i in idx]
            )
    groups = sorted({r["session"] for r in rows})
    rng = random.Random(seed)
    samples = {
        "balanced_accuracy": [],
        "failure_recall": [],
        "false_escalation_rate": [],
    }
    for _ in range(resamples):
        ids = [
            i
            for session in rng.choices(groups, k=len(groups))
            for i, r in enumerate(rows)
            if r["session"] == session
        ]
        m = metrics([rows[i] for i in ids], [predictions[i] for i in ids])
        for key, values in samples.items():
            if m[key] is not None:
                values.append(m[key])
    result["conversation_bootstrap"] = {
        key: {
            "valid_resamples": len(v),
            "percentile_95_interval": [
                sorted(v)[int(0.025 * (len(v) - 1))],
                sorted(v)[int(0.975 * (len(v) - 1))],
            ]
            if v
            else None,
        }
        for key, v in samples.items()
    }
    result["bootstrap_note"] = (
        "Whole logical conversations; descriptive conditional uncertainty, not unseen-domain evidence"
    )
    return result


def accumulation_group_size(index, count, accumulation):
    if not 1 <= index <= count or accumulation < 1:
        raise ValueError("Invalid accumulation position")
    start = ((index - 1) // accumulation) * accumulation
    return min(accumulation, count - start)


def load_inputs(config_path, root):
    cfg = read(config_path)
    if cfg["base_model"] != BASE_MODEL or cfg["base_revision"] != BASE_REVISION:
        raise ValueError("Wrong base identity")
    for path, expected in cfg["input_sha256"].items():
        if sha(root / path) != expected:
            raise ValueError(f"Frozen input changed: {path}")
    train, dev = read(root / cfg["train_rows"]), read(root / cfg["development_rows"])
    validate_groups(train, dev)
    for name, rows in (("training", train), ("development", dev)):
        if (
            len(rows) != cfg["population"][name]["rows"]
            or dict(Counter(r["target"] for r in rows))
            != cfg["population"][name]["class_counts"]
        ):
            raise ValueError(f"Frozen {name} population changed")
    if any(r.get("eligible_for_training") is not True for r in train):
        raise ValueError("Unreviewed or ineligible training row")
    if (
        set(Counter(r["target"] for r in train)) != ROUTES
        or set(Counter(r["target"] for r in dev)) != ROUTES
    ):
        raise ValueError("Both resolved classes required")
    for label, n in Counter(r["target"] for r in train).items():
        if not math.isclose(
            cfg["training"]["class_weights"][label], len(train) / (2 * n)
        ):
            raise ValueError("Wrong class weight")
    if cfg["selection_rule"] != {
        "min_local_success_recall": 0.5,
        "min_failure_recall": 0.6,
        "max_invalid": 0,
    }:
        raise ValueError("Changed preregistered selection rule")
    return cfg, train, dev


def initialize_adapter(model, cfg):
    from peft import LoraConfig, get_peft_model

    lora = cfg["lora"]
    model = get_peft_model(
        model,
        LoraConfig(
            r=lora["rank"],
            lora_alpha=lora["alpha"],
            lora_dropout=lora["dropout"],
            target_modules=lora["target_modules"],
            bias="none",
            task_type="CAUSAL_LM",
            use_dora=False,
            use_rslora=False,
        ),
    )
    if any(p.requires_grad for n, p in model.named_parameters() if "lora_" not in n):
        raise ValueError("Base parameters must remain frozen")
    if not any(p.requires_grad for n, p in model.named_parameters() if "lora_" in n):
        raise ValueError("No trainable LoRA parameters")
    return model, {
        "arm": "fresh",
        "initialized_from_adapter": False,
        "optimizer_state_resumed": False,
    }


def score_model(model, tokenizer, rows, encoded):
    predictions, details = [], []
    model.eval()
    with torch.inference_mode():
        for row, enc in zip(rows, encoded):
            ids = torch.tensor([enc["prefix_ids"]], device="cuda")
            output = model.generate(
                input_ids=ids,
                attention_mask=torch.ones_like(ids),
                do_sample=False,
                max_new_tokens=8,
                eos_token_id=tokenizer.convert_tokens_to_ids("<|im_end|>"),
                pad_token_id=tokenizer.pad_token_id,
                use_cache=True,
            )
            raw = tokenizer.decode(output[0, ids.shape[1] :], skip_special_tokens=False)
            clean = tokenizer.decode(
                output[0, ids.shape[1] :], skip_special_tokens=True
            ).strip()
            prediction = clean if clean in ROUTES else "INVALID"
            predictions.append(prediction)
            details.append(
                {
                    "id": row["id"],
                    "session": row["session"],
                    "expected": row["target"],
                    "prediction": prediction,
                    "raw": raw,
                }
            )
    return {"metrics": grouped_metrics(rows, predictions), "predictions": details}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("config.json")
    )
    parser.add_argument(
        "--artifacts-dir", type=Path, default=ROOT.parent / "thesis_artifacts"
    )
    parser.add_argument("--tokenizer-dir", type=Path)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--output", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute-training", action="store_true")
    modes.add_argument("--memory-probe", action="store_true")
    parser.add_argument("--memory-report", type=Path)
    args = parser.parse_args()
    args.tokenizer_dir = args.tokenizer_dir or args.artifacts_dir / "adapter"
    cfg, train, dev = load_inputs(args.config, args.artifacts_dir)
    from transformers import AutoTokenizer

    for name, expected in cfg["tokenizer_sha256"].items():
        if sha(args.tokenizer_dir / name) != expected:
            raise ValueError("Tokenizer/template identity changed")
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_dir, local_files_only=True)
    train_encoded = [
        encode_row(tokenizer, r, cfg["training"]["max_sequence_tokens"]) for r in train
    ]
    dev_encoded = [
        encode_row(tokenizer, r, cfg["development_max_sequence_tokens"]) for r in dev
    ]
    print(
        json.dumps(
            {
                "validated_training": len(train),
                "validated_development": len(dev),
                "max_training_tokens": max(len(r["input_ids"]) for r in train_encoded),
                "max_development_tokens": max(len(r["input_ids"]) for r in dev_encoded),
                "truncated": 0,
                "model_loaded": False,
            }
        ),
        flush=True,
    )
    if not args.execute_training and not args.memory_probe:
        return
    if (
        not torch.cuda.is_available()
        or torch.cuda.device_count() != 1
        or int(os.environ.get("WORLD_SIZE", "1")) != 1
    ):
        raise RuntimeError("This plan requires one CUDA GPU")
    if args.model_dir is None or args.output is None or args.output.exists():
        raise ValueError(
            "A pinned model directory and fresh output directory are required; no automatic resume"
        )
    if (
        args.model_dir.resolve().name != BASE_REVISION
        or args.model_dir.resolve().parent.name != "snapshots"
    ):
        raise ValueError("Use the exact pinned Hugging Face snapshot directory")
    if args.execute_training:
        probe = read(args.memory_report) if args.memory_report else {}
        if (
            not probe.get("passed")
            or probe.get("config_sha256") != sha(args.config)
            or probe.get("trainer_sha256") != sha(__file__)
            or probe.get("gpu") != torch.cuda.get_device_name(0)
            or probe.get("arm") != "fresh"
        ):
            raise ValueError("Matching on-pod memory probe required before training")
    from transformers import AutoModelForCausalLM, get_linear_schedule_with_warmup

    t = cfg["training"]
    if t["per_device_batch_size"] != 1 or t["gradient_accumulation_steps"] < 1:
        raise ValueError("This schedule requires batch1 and positive accumulation")
    random.seed(t["seed"])
    torch.manual_seed(t["seed"])
    torch.cuda.manual_seed_all(t["seed"])
    args.output.mkdir(parents=True, exist_ok=False)
    write(
        args.output / "launch.json",
        {
            "status": "started",
            "config_sha256": sha(args.config),
            "base_model": BASE_MODEL,
            "base_revision": BASE_REVISION,
            "base_files": {
                str(p.relative_to(args.model_dir)): sha(p)
                for p in args.model_dir.rglob("*")
                if p.is_file()
            },
            "arm": "fresh",
            "fresh_adapter": True,
            "optimizer_state_resumed": False,
        },
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model_dir,
        local_files_only=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    ).cuda()
    if model.config.max_position_embeddings < cfg["development_max_sequence_tokens"]:
        raise ValueError(
            "Model cannot accept the frozen full development context limit"
        )
    model, initialization = initialize_adapter(model, cfg)
    write(args.output / "initialization_verification.json", initialization)
    model.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    model.enable_input_require_grads()
    model.config.use_cache = False
    if any(p.requires_grad for n, p in model.named_parameters() if "lora_" not in n):
        raise ValueError("Base parameters must remain frozen")
    if args.memory_probe:
        torch.cuda.reset_peak_memory_stats()
        index = max(range(len(train)), key=lambda i: len(train_encoded[i]["input_ids"]))
        batch = {
            k: v.cuda()
            for k, v in collate(
                [train_encoded[index]],
                [t["class_weights"][train[index]["target"]]],
                tokenizer.pad_token_id,
            ).items()
        }
        model.train()
        output = model(
            input_ids=batch["input_ids"],
            attention_mask=batch["attention_mask"],
            use_cache=False,
        )
        loss = weighted_suffix_loss(
            output.logits, batch["labels"], batch["example_weights"]
        )
        if not torch.isfinite(loss):
            raise RuntimeError("Non-finite memory-probe loss")
        loss.backward()
        if not any(
            p.grad is not None and torch.count_nonzero(p.grad)
            for n, p in model.named_parameters()
            if "lora_" in n
        ):
            raise RuntimeError("No LoRA gradient in memory probe")
        backward_peak = torch.cuda.max_memory_allocated()
        model.zero_grad(set_to_none=True)
        del output, loss, batch
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        requests = read(args.artifacts_dir / cfg["development_requests"])
        prefixes = [
            (
                r["id"],
                tokenizer.encode(
                    decision_prefix(r["request"]), add_special_tokens=False
                ),
            )
            for r in requests
        ]
        longest_id, longest = max(prefixes, key=lambda pair: len(pair[1]))
        if len(longest) + 8 > cfg["development_max_sequence_tokens"]:
            raise ValueError(
                "Full development prompt plus output reserve exceeds limit"
            )
        model.eval()
        with torch.inference_mode():
            ids = torch.tensor([longest], device="cuda")
            output = model(
                input_ids=ids,
                attention_mask=torch.ones_like(ids),
                use_cache=True,
                logits_to_keep=1,
            )
            if not torch.isfinite(output.logits).all():
                raise RuntimeError("Non-finite development prefill")
        report = {
            "passed": True,
            "arm": "fresh",
            "config_sha256": sha(args.config),
            "trainer_sha256": sha(__file__),
            "gpu": torch.cuda.get_device_name(0),
            "longest_training_id": train[index]["id"],
            "training_full_tokens": len(train_encoded[index]["input_ids"]),
            "backward_peak_gpu_bytes": backward_peak,
            "longest_development_id": longest_id,
            "development_prompt_tokens": len(longest),
            "prefill_peak_gpu_bytes": torch.cuda.max_memory_allocated(),
            "optimizer_steps": 0,
            "generated_decisions": 0,
            "saved_adapters": 0,
        }
        write(args.output / "memory_probe.json", report)
        print(json.dumps(report), flush=True)
        return
    random.seed(t["seed"])
    torch.manual_seed(t["seed"])
    torch.cuda.manual_seed_all(t["seed"])
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=t["learning_rate"],
        weight_decay=t["weight_decay"],
    )
    accumulation = t["gradient_accumulation_steps"]
    steps = math.ceil(len(train) / accumulation) * t["epochs"]
    scheduler = get_linear_schedule_with_warmup(
        optimizer, math.ceil(steps * t["warmup_ratio"]), steps
    )
    history, reports, optimizer_steps = [], [], 0
    for epoch in range(1, t["epochs"] + 1):
        order = list(range(len(train)))
        random.Random(t["seed"] + epoch).shuffle(order)
        model.train()
        optimizer.zero_grad(set_to_none=True)
        for n, index in enumerate(order, 1):
            batch = {
                k: v.cuda()
                for k, v in collate(
                    [train_encoded[index]],
                    [t["class_weights"][train[index]["target"]]],
                    tokenizer.pad_token_id,
                ).items()
            }
            output = model(
                input_ids=batch["input_ids"],
                attention_mask=batch["attention_mask"],
                use_cache=False,
            )
            loss = weighted_suffix_loss(
                output.logits, batch["labels"], batch["example_weights"]
            )
            if not torch.isfinite(loss):
                raise RuntimeError("Non-finite weighted loss; preserve run and stop")
            (loss / accumulation_group_size(n, len(order), accumulation)).backward()
            history.append(
                {"epoch": epoch, "id": train[index]["id"], "weighted_loss": loss.item()}
            )
            del output, loss, batch
            if n % accumulation == 0 or n == len(order):
                torch.nn.utils.clip_grad_norm_(
                    model.parameters(), 1.0, error_if_nonfinite=True
                )
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
                optimizer_steps += 1
        checkpoint = args.output / f"epoch-{epoch}"
        model.save_pretrained(checkpoint, safe_serialization=True)
        tokenizer.save_pretrained(checkpoint)
        write(
            checkpoint / "training_state.json",
            {
                "epoch": epoch,
                "examples_seen": order,
                "config_sha256": sha(args.config),
                "adapter_sha256": sha(checkpoint / "adapter_model.safetensors"),
            },
        )
        torch.save(
            {
                "epoch": epoch,
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict(),
                "torch_rng": torch.get_rng_state(),
                "cuda_rng": torch.cuda.get_rng_state_all(),
                "python_rng": random.getstate(),
                "optimizer_steps": optimizer_steps,
            },
            checkpoint / "optimizer_state.pt",
        )
        report = {
            "arm": "fresh",
            "epoch": epoch,
            **score_model(model, tokenizer, dev, dev_encoded),
        }
        reports.append(report)
        write(checkpoint / "development_report.json", report)
        write(args.output / "loss_history.json", history)
        print(
            json.dumps(
                {
                    "epoch": epoch,
                    "metrics": report["metrics"],
                    "peak_gpu_bytes": torch.cuda.max_memory_allocated(),
                }
            ),
            flush=True,
        )
    if optimizer_steps != steps:
        raise RuntimeError("Optimizer step count disagrees with sealed schedule")
    write(
        args.output / "arm_completion.json",
        {
            "status": "completed_unselected",
            "arm": "fresh",
            "epochs": t["epochs"],
            "optimizer_steps": optimizer_steps,
            "example_presentations": len(train) * t["epochs"],
            "config_sha256": sha(args.config),
            "reports": reports,
            "prospective_test_seen": False,
        },
    )


if __name__ == "__main__":
    main()
