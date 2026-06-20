"""
EdgeDispatch - Cost Model

Implements the token-cost model from the thesis (Section 2.2, Equations 2.4-2.6):

    C_mono = (|S| + |Q| + |E| + |I|) * p_in            (Eq 2.4)
    C_ED   = D * (|H| + |I|) * p_in                     (Eq 2.5)
    dC     = C_mono - C_ED                              (Eq 2.6)

where |S| is the full tool-manifest token cost, |Q| the query, |E| the retrieved
evidence, |I| the synthesis instructions, |H| the handoff document, and D in {0,1}
the dispatch decision (0 = local resolution, 1 = cloud escalation).

This implementation extends the thesis model to also price output tokens, since
real cloud APIs bill input and output separately:

    C = (input_tokens * p_in + output_tokens * p_out) / 1_000_000

For the monolithic baseline we estimate the input tokens the cloud model *would*
have received (full schemas + query + evidence + instructions). For EdgeDispatch
we use the actual input/output tokens observed during the cloud run when D=1,
and 0 when D=0 (local resolution incurs zero cloud cost).

Schema-token constant (~240 tokens/tool) and instruction overhead (~200 tokens)
follow thesis Table 2.1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# Thesis Table 2.1 constants
SCHEMA_TOKENS_PER_TOOL: int = 240
INSTRUCTION_TOKENS: int = 200


@dataclass
class TokenUsage:
    """Observed token usage for a single pipeline run."""
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass
class CostBreakdown:
    """Per-query cost breakdown comparing EdgeDispatch against the monolithic baseline."""
    route: str                       # "local" or "escalated"
    price_input_per_mtok: float      # $/M input tokens used for the calculation
    price_output_per_mtok: float     # $/M output tokens used for the calculation
    tokens_mono_in: int              # estimated monolithic input tokens
    tokens_mono_out: int             # estimated monolithic output tokens
    tokens_ed_in: int                # EdgeDispatch input tokens (thesis Eq 2.5 estimate; 0 if local)
    tokens_ed_out: int               # EdgeDispatch output tokens (observed; 0 if local)
    observed_cloud_in: int           # actual cloud input tokens observed (0 if local)
    observed_cloud_out: int          # actual cloud output tokens observed (0 if local)
    observed_local_in: int           # actual local SLM input tokens observed
    observed_local_out: int          # actual local SLM output tokens observed
    c_mono: float                    # estimated monolithic cost ($)
    c_ed: float                      # EdgeDispatch cost ($) (0.0 when local)
    delta_c: float                   # savings vs monolithic ($)
    schema_tokens_avoided: int       # tool-schema tokens kept out of the cloud context

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "price_input_per_mtok": self.price_input_per_mtok,
            "price_output_per_mtok": self.price_output_per_mtok,
            "tokens_mono_in": self.tokens_mono_in,
            "tokens_mono_out": self.tokens_mono_out,
            "tokens_ed_in": self.tokens_ed_in,
            "tokens_ed_out": self.tokens_ed_out,
            "observed_cloud_in": self.observed_cloud_in,
            "observed_cloud_out": self.observed_cloud_out,
            "observed_local_in": self.observed_local_in,
            "observed_local_out": self.observed_local_out,
            "c_mono": round(self.c_mono, 6),
            "c_ed": round(self.c_ed, 6),
            "delta_c": round(self.delta_c, 6),
            "schema_tokens_avoided": self.schema_tokens_avoided,
        }


def estimate_tokens(text: str) -> int:
    """Rough token estimate (characters / 4). Matches the thesis heuristic."""
    if not text:
        return 0
    return len(text) // 4


def _evidence_to_text(evidence: list[dict[str, Any]]) -> str:
    """Flatten the evidence list to a single string for token estimation."""
    if not evidence:
        return ""
    try:
        return json.dumps(evidence, indent=2, default=str)
    except (TypeError, ValueError):
        return str(evidence)


class CostModel:
    """
    Computes per-query cost breakdowns for the EdgeDispatch architecture.

    Holds the user-configurable pricing (per-million-token rates) and the
    prototype manifest size, then produces a CostBreakdown for each query
    given the dispatch decision, the handoff document, and observed token
    usage from the cloud run.
    """

    def __init__(
        self,
        price_input_per_mtok: float,
        price_output_per_mtok: float,
        manifest_size: int = 0,
    ):
        if price_input_per_mtok < 0 or price_output_per_mtok < 0:
            raise ValueError("Per-million-token prices must be non-negative")
        self.price_input_per_mtok = price_input_per_mtok
        self.price_output_per_mtok = price_output_per_mtok
        self.manifest_size = manifest_size

    def update_pricing(
        self,
        price_input_per_mtok: float | None = None,
        price_output_per_mtok: float | None = None,
    ) -> None:
        """Update pricing in place. None leaves the value unchanged."""
        if price_input_per_mtok is not None:
            if price_input_per_mtok < 0:
                raise ValueError("Input price must be non-negative")
            self.price_input_per_mtok = price_input_per_mtok
        if price_output_per_mtok is not None:
            if price_output_per_mtok < 0:
                raise ValueError("Output price must be non-negative")
            self.price_output_per_mtok = price_output_per_mtok

    def update_manifest_size(self, manifest_size: int) -> None:
        self.manifest_size = max(0, manifest_size)

    def compute(
        self,
        *,
        route: str,
        query: str,
        evidence: list[dict[str, Any]],
        handoff_text: str = "",
        cloud_usage: TokenUsage | None = None,
        local_usage: TokenUsage | None = None,
    ) -> CostBreakdown:
        """
        Compute the cost breakdown for one query.

        Args:
            route: "local" (D=0) or "escalated" (D=1).
            query: Original user query string.
            evidence: Retrieved evidence list (used for both mono and ED estimates).
            handoff_text: The handoff document text sent to the cloud (D=1 only).
            cloud_usage: Observed token usage from the cloud LLM run (D=1 only).
            local_usage: Observed token usage from the local SLM run (always).
        """
        if route not in ("local", "escalated"):
            raise ValueError(f"route must be 'local' or 'escalated', got {route!r}")

        cloud_usage = cloud_usage or TokenUsage()
        local_usage = local_usage or TokenUsage()

        # --- Monolithic baseline estimate (what a monolithic deployment would cost) ---
        # The cloud model would receive every tool schema + query + evidence + instructions.
        schema_tokens = self.manifest_size * SCHEMA_TOKENS_PER_TOOL
        tokens_q = estimate_tokens(query)
        tokens_e = estimate_tokens(_evidence_to_text(evidence))
        tokens_mono_in = schema_tokens + tokens_q + tokens_e + INSTRUCTION_TOKENS

        # Output: the monolithic cloud model would produce a comparable answer.
        # When escalated we use the *actual* observed cloud output tokens as the
        # proxy; when local we use the local SLM's output tokens as a proxy for
        # what the cloud would have emitted.
        if route == "escalated":
            tokens_mono_out = cloud_usage.output_tokens
        else:
            tokens_mono_out = local_usage.output_tokens

        c_mono = self._price(tokens_mono_in, tokens_mono_out)

        # --- EdgeDispatch actual cost ---
        if route == "escalated":
            # The cloud model receives only the handoff document + synthesis instructions.
            tokens_ed_in = estimate_tokens(handoff_text) + INSTRUCTION_TOKENS
            tokens_ed_out = cloud_usage.output_tokens
            c_ed = self._price(tokens_ed_in, tokens_ed_out)
        else:
            # Local resolution: zero cloud tokens (Eq 2.5 with D=0).
            tokens_ed_in = 0
            tokens_ed_out = 0
            c_ed = 0.0

        delta_c = c_mono - c_ed

        return CostBreakdown(
            route=route,
            price_input_per_mtok=self.price_input_per_mtok,
            price_output_per_mtok=self.price_output_per_mtok,
            tokens_mono_in=tokens_mono_in,
            tokens_mono_out=tokens_mono_out,
            tokens_ed_in=tokens_ed_in,
            tokens_ed_out=tokens_ed_out,
            observed_cloud_in=cloud_usage.input_tokens,
            observed_cloud_out=cloud_usage.output_tokens,
            observed_local_in=local_usage.input_tokens,
            observed_local_out=local_usage.output_tokens,
            c_mono=c_mono,
            c_ed=c_ed,
            delta_c=delta_c,
            schema_tokens_avoided=schema_tokens if route == "escalated" else schema_tokens,
        )

    def _price(self, input_tokens: int, output_tokens: int) -> float:
        """Dollar cost for a token pair at the current pricing."""
        return (
            input_tokens * self.price_input_per_mtok
            + output_tokens * self.price_output_per_mtok
        ) / 1_000_000
