from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

ARTIFACTS = Path(__file__).resolve().parents[2] / "thesis_artifacts"
PACKAGE_SHA256 = "82c0ecc6909699ea066498d1d11fe02f0bb81e4bfe41c4e9f2dc757f8bbb0132"
CLASSIFIER_SHA256 = "9714d74908d141a77c311957fb696c12bf105a1d142210babfc66dd9db81cd76"
ROUTES = {"LOCAL", "ESCALATE"}


def read(path: Path, expected: str | None = None):
    data = path.read_bytes()
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"Evidence checksum changed: {path}")
    return json.loads(data)


def classifier_results(artifacts: Path) -> dict:
    pairs = read(artifacts / "classifier/pairs.json", CLASSIFIER_SHA256)
    if len(pairs) != 200 or len({row["id"] for row in pairs}) != 200:
        raise ValueError("Expected 200 distinct prospective classifier decisions")
    scored = [row for row in pairs if row["target"] in ROUTES]
    confusion = Counter((row["target"], row["route"]) for row in scored)
    tp, fn = confusion["ESCALATE", "ESCALATE"], confusion["ESCALATE", "LOCAL"]
    tn, fp = confusion["LOCAL", "LOCAL"], confusion["LOCAL", "ESCALATE"]
    if (tp, fn, tn, fp) != (100, 11, 49, 37):
        raise ValueError("Saved classifier decisions no longer match the thesis")
    return {
        "attempted": len(pairs),
        "scored": len(scored),
        "unresolved": len(pairs) - len(scored),
        "confusion": {f"{a} -> {b}": n for (a, b), n in sorted(confusion.items())},
        "accuracy": (tp + tn) / len(scored),
        "balanced_accuracy": (tp / (tp + fn) + tn / (tn + fp)) / 2,
        "escalate_precision": tp / (tp + fp),
        "escalate_recall": tp / (tp + fn),
        "escalate_f1": 2 * tp / (2 * tp + fp + fn),
        "local_recall": tn / (tn + fp),
        "hf_gguf_agreement": sum(row["hf_prediction"] == row["route"] for row in pairs),
    }


def answer_results(artifacts: Path) -> dict:
    evaluation = artifacts / "evaluation"
    manifest = read(evaluation / "evals/package_manifest.json", PACKAGE_SHA256)
    entries = {row["destination"]: row for row in manifest["copied_files"]}
    paths = sorted((evaluation / "evals/records").glob("*.json"))
    records = [
        read(path, entries[str(path.relative_to(evaluation))]["sha256"])
        for path in paths
    ]
    if len(records) != 150 or len({(row["id"], row["arm"]) for row in records}) != 150:
        raise ValueError("Expected 150 distinct question/condition records")
    expected = read(
        evaluation / "evals/results.json", entries["evals/results.json"]["sha256"]
    )["combined50"]["conditions"]
    results = {}
    for arm in ("always_local", "always_remote", "edgedispatch"):
        rows = [row for row in records if row["arm"] == arm]
        if len(rows) != 50:
            raise ValueError(f"Expected 50 questions for {arm}")
        result = {
            "attempts": len(rows),
            "completed": sum(row["status"] == "completed" for row in rows),
        }
        for metric in ("correctness", "faithfulness"):
            scores = [row["scores"][metric] for row in rows]
            scored = [score for score in scores if score["status"] == "scored"]
            result[metric] = {
                "passed": sum(score["score"] == 1 for score in scored),
                "scored": len(scored),
            }
            if any(
                result[metric][key] != expected[arm][metric][key]
                for key in result[metric]
            ):
                raise ValueError(f"Saved {arm} {metric} differs from recorded results")
        result["execution_api_cost_usd"] = sum(
            row["execution_cost_usd"] for row in rows
        )
        if any(result[key] != expected[arm][key] for key in ("attempts", "completed")):
            raise ValueError(f"Saved {arm} delivery differs from recorded results")
        if not math.isclose(
            result["execution_api_cost_usd"],
            expected[arm]["execution_api_cost_usd"],
            abs_tol=1e-10,
        ):
            raise ValueError(f"Saved {arm} cost differs from recorded results")
        results[arm] = result
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", type=Path, default=ARTIFACTS)
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "classifier": classifier_results(args.artifacts_dir),
                "answer_quality": answer_results(args.artifacts_dir),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
