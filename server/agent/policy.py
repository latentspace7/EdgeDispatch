from __future__ import annotations

from .contract import Decision
from .storage import POLICIES


def policy_decision(
    policy: str, *, force_remote: bool, previously_escalated: bool
) -> Decision | None:
    if policy not in POLICIES:
        raise ValueError("Unknown escalation policy")
    if force_remote:
        return Decision("ESCALATE", "explicit_user_escalation")
    if policy == "sticky_escalation" and previously_escalated:
        return Decision("ESCALATE", "sticky_policy")
    return None
