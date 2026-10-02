import re

from mcp.server.fastmcp import FastMCP

from mock_airline.storage import page, policies, result

mcp = FastMCP("airline_policy")


@mcp.tool(
    description="Find fictional airline policies by topic or ID; empty query lists all policies. Search snippets are incomplete: use policy_get for full rules and exceptions.",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
def policy_search(query: str, offset: int = 0, limit: int = 20) -> dict:
    terms = re.findall(r"[a-z0-9]+", query.lower())
    matches = []
    for policy in policies():
        text = (
            policy["policy_id"] + " " + policy["title"] + " " + policy["text"]
        ).lower()
        title = (policy["policy_id"] + " " + policy["title"]).lower()
        score = sum(5 * (term in title) + (term in text) for term in terms)
        if not terms or score:
            matches.append(
                (
                    score,
                    {
                        key: policy[key]
                        for key in ("policy_id", "title", "version", "effective_from")
                    }
                    | {"snippet": policy["text"][:240]},
                )
            )
    matches.sort(key=lambda item: (-item[0], item[1]["policy_id"]))
    return page("airline_policy", [item[1] for item in matches], offset, limit)


@mcp.tool(
    description="Read a complete airline policy by ID, including exceptions and source authority. All policies apply at the fixed dataset as_of date and are fictional, not Qantas policies.",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
def policy_get(policy_id: str) -> dict:
    key = policy_id.strip().upper()
    policy = next((item for item in policies() if item["policy_id"] == key), None)
    return result(
        "airline_policy",
        status="found" if policy else "not_found",
        policy=policy,
        policy_id=key,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
