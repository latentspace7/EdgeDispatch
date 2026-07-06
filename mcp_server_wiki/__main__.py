"""
EdgeDispatch stub MCP server - policy wiki.

Provides representative tools over stdio for the prototype:
  - wiki_search(query): full-text search across policy wiki pages
  - wiki_get_page(title): retrieve a specific wiki page by title

Run with:  python -m mcp_server_wiki
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("policy_wiki")

# Representative in-memory HR policy wiki pages for the prototype.
_PAGES: dict[str, str] = {
    "annual-leave-policy": (
        "Annual Leave Policy (HR-AL-01). Full-time US employees receive 20 annual "
        "leave days per calendar year; US Sales employees receive 22 days. Full-time "
        "UK employees receive 25 days. Part-time employees receive prorated annual "
        "leave based on scheduled weekly hours. Annual leave requires manager approval "
        "and should be requested at least 10 business days before planned time off."
    ),
    "sick-leave-policy": (
        "Sick Leave Policy (HR-SL-02). Full-time US employees receive 10 paid sick "
        "leave days per year. Part-time US employees receive prorated sick leave. UK "
        "employees may receive company sick pay up to 20 days per year after manager "
        "notification. Sick leave should be recorded in the HR system within 3 business "
        "days of return unless local law requires otherwise."
    ),
    "parental-leave-policy": (
        "Parental Leave Policy (HR-PL-03). For this prototype, parental leave "
        "classification is derived from the employee profile gender field: Female "
        "employees are eligible for maternity leave; all other employees are eligible "
        "for paternity leave. Maternity leave provides up to 16 paid weeks. Paternity "
        "leave provides up to 4 paid weeks. Employees must notify HR at least 30 days "
        "before the expected start date when practicable."
    ),
    "bereavement-and-compassionate-leave": (
        "Bereavement and Compassionate Leave Policy (HR-BC-04). Employees may take "
        "up to 5 paid days for the death of an immediate family member. UK employees "
        "may also request compassionate leave for urgent family circumstances. HR may "
        "approve additional unpaid leave case by case."
    ),
    "leave-eligibility-rules": (
        "Leave Eligibility Rules (HR-EL-05). Eligibility is calculated from employee "
        "status, employment type, location, leave plan, and gender. Active employees "
        "can use annual and sick leave. Part-time employees receive prorated balances. "
        "Female employees map to maternity leave; employees with any other gender value "
        "map to paternity leave for the prototype parental-leave rule."
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
        title: Page slug (e.g. annual-leave-policy).

    Returns:
        The full page text, or a not-found message.
    """
    key = (title or "").strip().lower()
    if key in _PAGES:
        return f"[{key}] {_PAGES[key]}"
    return f"Wiki page '{title}' not found. Available: {', '.join(_PAGES)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
