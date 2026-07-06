"""
EdgeDispatch stub MCP server - document store.

Provides representative tools over stdio for the prototype:
  - search_documents(query): hybrid dense + BM25 search across the corpus
  - get_document(doc_id): retrieve a specific document by ID

Run with:  python -m mcp_server_docs
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("document_store")

# Representative in-memory HR document corpus for the prototype.
_DOCUMENTS: dict[str, str] = {
    "LEAVE-EMP-1001": (
        "Employee Leave Balance Statement - Priya Shah (EMP-1001). "
        "Gender: Female. Leave plan: US-Standard. Annual leave allowance: 20 days. "
        "Annual leave used: 6 days. Annual leave balance: 14 days. Sick leave "
        "allowance: 10 days. Sick leave used: 2 days. Sick leave balance: 8 days. "
        "Eligible leave types: annual leave, sick leave, bereavement leave, jury duty, "
        "maternity leave. Parental leave classification: maternity leave."
    ),
    "LEAVE-EMP-1002": (
        "Employee Leave Balance Statement - Liam Chen (EMP-1002). "
        "Gender: Male. Leave plan: US-Standard. Annual leave allowance: 20 days. "
        "Annual leave used: 11 days. Annual leave balance: 9 days. Sick leave "
        "allowance: 10 days. Sick leave used: 1 day. Sick leave balance: 9 days. "
        "Eligible leave types: annual leave, sick leave, caregiver leave, jury duty, "
        "paternity leave. Parental leave classification: paternity leave."
    ),
    "LEAVE-EMP-1003": (
        "Employee Leave Balance Statement - Maya Rodriguez (EMP-1003). "
        "Gender: Female. Leave plan: US-Sales. Annual leave allowance: 22 days. "
        "Annual leave used: 4 days. Annual leave balance: 18 days. Sick leave "
        "allowance: 10 days. Sick leave used: 0 days. Sick leave balance: 10 days. "
        "Eligible leave types: annual leave, sick leave, sales recharge days, "
        "maternity leave. Parental leave classification: maternity leave."
    ),
    "LEAVE-EMP-1004": (
        "Employee Leave Balance Statement - Noah Okafor (EMP-1004). "
        "Gender: Male. Leave plan: US-Part-Time. Annual leave allowance: 10 days "
        "prorated. Annual leave used: 3 days. Annual leave balance: 7 days. "
        "Sick leave allowance: 5 days prorated. Sick leave used: 1 day. Sick leave "
        "balance: 4 days. Eligible leave types: prorated annual leave, sick leave, "
        "unpaid leave, paternity leave. Parental leave classification: paternity leave."
    ),
    "LEAVE-EMP-1005": (
        "Employee Leave Balance Statement - Sofia Rossi (EMP-1005). "
        "Gender: Female. Leave plan: UK-Standard. Annual leave allowance: 25 days. "
        "Annual leave used: 12 days. Annual leave balance: 13 days. Sick leave "
        "allowance: company sick pay up to 20 days. Sick leave used: 5 days. "
        "Sick leave balance: 15 company-paid days. Eligible leave types: annual leave, "
        "sick leave, compassionate leave, maternity leave. Parental leave classification: "
        "maternity leave."
    ),
    "LEAVE-GUIDE-2026": (
        "HR Leave Balance Guide 2026. Defines leave-balance terms used in employee "
        "statements: annual leave balance equals allowance minus approved annual leave; "
        "sick leave balance equals available paid sick days minus recorded sick days; "
        "eligible leaves are derived from employee location, employment type, leave plan, "
        "and parental leave classification."
    ),
}


@mcp.tool()
def search_documents(query: str) -> str:
    """Search across HR leave statements and employee leave documents by keyword.

    Args:
        query: Free-text search query.

    Returns:
        A newline-separated list of matching HR document IDs and snippets.
    """
    q = (query or "").lower()
    matches: list[str] = []
    for doc_id, text in _DOCUMENTS.items():
        if q and any(term in text.lower() for term in q.split()):
            matches.append(f"{doc_id}: {text[:120]}")
    if not matches:
        matches.append("No documents matched the query.")
    return "\n".join(matches)


@mcp.tool()
def get_document(doc_id: str) -> str:
    """Retrieve a specific HR document by ID or title.

    Args:
        doc_id: Document identifier (e.g. LEAVE-EMP-1001).

    Returns:
        The full document text, or a not-found message.
    """
    key = (doc_id or "").strip().upper()
    if key in _DOCUMENTS:
        return f"[{key}] {_DOCUMENTS[key]}"
    return f"Document '{doc_id}' not found. Available: {', '.join(_DOCUMENTS)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
