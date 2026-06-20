"""
EdgeDispatch stub MCP server — policy wiki.

Provides representative tools over stdio for the prototype:
  - wiki_search(query): full-text search across policy wiki pages
  - wiki_get_page(title): retrieve a specific wiki page by title

Run with:  python -m mcp_server_wiki
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("policy_wiki")

# Representative in-memory wiki pages for the prototype.
_PAGES: dict[str, str] = {
    "expedited-review-policy": (
        "Expedited Review Policy (EXP-02). An approval may be expedited when it "
        "is classified low-risk AND references a completed qualification report. "
        "Expedited reviews must still receive compliance sign-off, but the "
        "standard 14-day waiting period is waived. Owner: Compliance Committee."
    ),
    "change-control-procedure": (
        "Change Control Procedure (CCP-01). All changes require a REQ-* "
        "identifier. Status lifecycle: submitted -> under_review -> approved "
        "or rejected. Expedited requests follow EXP-02."
    ),
    "record-retention-guideline": (
        "Record Retention Guideline (RRG-04). Approval records and their "
        "supporting evidence must be retained for 7 years from approval date."
    ),
}


@mcp.tool()
def wiki_search(query: str) -> str:
    """Search policy wiki pages by keyword or topic.

    Args:
        query: Search terms.

    Returns:
        Newline-separated matching page titles and snippets.
    """
    q = (query or "").lower()
    terms = q.split()
    matches: list[str] = []
    for title, text in _PAGES.items():
        if terms and any(t in text.lower() or t in title.lower() for t in terms):
            matches.append(f"{title}: {text[:120]}")
    if not matches:
        matches.append("No wiki pages matched the query.")
    return "\n".join(matches)


@mcp.tool()
def wiki_get_page(title: str) -> str:
    """Retrieve a specific wiki page by title (slug).

    Args:
        title: Page slug (e.g. expedited-review-policy).

    Returns:
        The full page text, or a not-found message.
    """
    key = (title or "").strip().lower()
    if key in _PAGES:
        return f"[{key}] {_PAGES[key]}"
    return f"Wiki page '{title}' not found. Available: {', '.join(_PAGES)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
