"""
EdgeDispatch stub MCP server — document store.

Provides representative tools over stdio for the prototype:
  - search_documents(query): hybrid dense + BM25 search across the corpus
  - get_document(doc_id): retrieve a specific document by ID

Run with:  python -m mcp_server_docs
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("document_store")

# Representative in-memory corpus for the prototype.
_DOCUMENTS: dict[str, str] = {
    "DOC-1001": (
        "Qualification Report QR-2024 — Device performance validation. "
        "Summary: all acceptance criteria met. Expedited review recommended "
        "given the low-risk classification. Author: J. Patel. Pages: 42."
    ),
    "DOC-1002": (
        "Technical Specification TS-007 — Interface contract for the approval "
        "service. Defines the REQ-* identifier scheme and status lifecycle "
        "(submitted, under_review, approved, rejected, expedited)."
    ),
    "DOC-1003": (
        "Field Service Report FSR-55 — Site visit notes. No anomalies detected. "
        "Reference approval REQ-2024-1052 cited as the governing change request."
    ),
}


@mcp.tool()
def search_documents(query: str) -> str:
    """Search across PDFs, DOCX files, and technical reports by keyword.

    Args:
        query: Free-text search query.

    Returns:
        A newline-separated list of matching document IDs and snippets.
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
    """Retrieve a specific document by ID or title.

    Args:
        doc_id: Document identifier (e.g. DOC-1001).

    Returns:
        The full document text, or a not-found message.
    """
    key = (doc_id or "").strip().upper()
    if key in _DOCUMENTS:
        return f"[{key}] {_DOCUMENTS[key]}"
    return f"Document '{doc_id}' not found. Available: {', '.join(_DOCUMENTS)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
