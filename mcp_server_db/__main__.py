"""
EdgeDispatch stub MCP server — relational database.

Provides representative tools over stdio for the prototype:
  - db_lookup(record_id): structured SQL lookup of an approval record
  - db_search(query): full-text search across database fields

Run with:  python -m mcp_server_db
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("relational_db")

# Representative in-memory table of approval records for the prototype.
_RECORDS: dict[str, dict[str, str]] = {
    "REQ-2024-1052": {
        "id": "REQ-2024-1052",
        "title": "Firmware patch — power management",
        "status": "under_review",
        "submitted": "2024-05-12",
        "owner": "D. Nguyen",
        "priority": "expedited",
        "notes": "Awaiting compliance sign-off per expedited-review policy.",
    },
    "REQ-2024-1077": {
        "id": "REQ-2024-1077",
        "title": "Packaging label update",
        "status": "approved",
        "submitted": "2024-05-30",
        "owner": "A. Rahman",
        "priority": "standard",
        "notes": "Approved 2024-06-03.",
    },
    "REQ-2024-1101": {
        "id": "REQ-2024-1101",
        "title": "Supplier change — resin vendor",
        "status": "submitted",
        "submitted": "2024-06-15",
        "owner": "M. Cole",
        "priority": "standard",
        "notes": "Pending triage.",
    },
}


@mcp.tool()
def db_lookup(record_id: str) -> str:
    """Run a structured lookup against approval records by ID.

    Args:
        record_id: Approval identifier (e.g. REQ-2024-1052).

    Returns:
        A formatted record summary, or a not-found message.
    """
    key = (record_id or "").strip().upper()
    record = _RECORDS.get(key)
    if not record:
        return f"Record '{record_id}' not found. Known IDs: {', '.join(_RECORDS)}"
    lines = [f"{k}: {v}" for k, v in record.items()]
    return "\n".join(lines)


@mcp.tool()
def db_search(query: str) -> str:
    """Full-text search across database fields (status, title, owner, priority).

    Args:
        query: Search terms.

    Returns:
        Newline-separated matching record summaries.
    """
    q = (query or "").lower()
    terms = q.split()
    matches: list[str] = []
    for record in _RECORDS.values():
        blob = " ".join(record.values()).lower()
        if terms and any(t in blob for t in terms):
            matches.append(f"{record['id']} | {record['status']} | {record['title']}")
    if not matches:
        matches.append("No records matched the query.")
    return "\n".join(matches)


if __name__ == "__main__":
    mcp.run(transport="stdio")
