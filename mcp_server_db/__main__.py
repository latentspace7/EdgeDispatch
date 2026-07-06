"""
EdgeDispatch stub MCP server - relational database.

Provides representative tools over stdio for the prototype:
  - db_lookup(record_id): structured SQL lookup of an employee record
  - db_search(query): full-text search across employee database fields

Run with:  python -m mcp_server_db
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("relational_db")

# Representative in-memory employee master table for the prototype HR system.
_RECORDS: dict[str, dict[str, str]] = {
    "EMP-1001": {
        "employee_id": "EMP-1001",
        "name": "Priya Shah",
        "gender": "Female",
        "department": "People Operations",
        "role": "HR Business Partner",
        "manager": "Ava Thompson",
        "location": "New York",
        "employment_type": "Full-time",
        "start_date": "2021-03-15",
        "status": "active",
        "email": "priya.shah@example.com",
        "leave_plan": "US-Standard",
        "notes": "Eligible for annual leave, sick leave, bereavement leave, and maternity leave.",
    },
    "EMP-1002": {
        "employee_id": "EMP-1002",
        "name": "Liam Chen",
        "gender": "Male",
        "department": "Engineering",
        "role": "Senior Backend Engineer",
        "manager": "Noor Al-Khalid",
        "location": "San Francisco",
        "employment_type": "Full-time",
        "start_date": "2019-08-05",
        "status": "active",
        "email": "liam.chen@example.com",
        "leave_plan": "US-Standard",
        "notes": "Eligible for annual leave, sick leave, caregiver leave, and paternity leave.",
    },
    "EMP-1003": {
        "employee_id": "EMP-1003",
        "name": "Maya Rodriguez",
        "gender": "Female",
        "department": "Sales",
        "role": "Enterprise Account Executive",
        "manager": "Ethan Brooks",
        "location": "Austin",
        "employment_type": "Full-time",
        "start_date": "2022-11-01",
        "status": "active",
        "email": "maya.rodriguez@example.com",
        "leave_plan": "US-Sales",
        "notes": "Eligible for annual leave, sick leave, sales recharge days, and maternity leave.",
    },
    "EMP-1004": {
        "employee_id": "EMP-1004",
        "name": "Noah Okafor",
        "gender": "Male",
        "department": "Finance",
        "role": "Payroll Analyst",
        "manager": "Priya Shah",
        "location": "Chicago",
        "employment_type": "Part-time",
        "start_date": "2023-06-12",
        "status": "active",
        "email": "noah.okafor@example.com",
        "leave_plan": "US-Part-Time",
        "notes": "Eligible for prorated annual leave, sick leave, unpaid leave, and paternity leave.",
    },
    "EMP-1005": {
        "employee_id": "EMP-1005",
        "name": "Sofia Rossi",
        "gender": "Female",
        "department": "Customer Success",
        "role": "Implementation Manager",
        "manager": "Maya Rodriguez",
        "location": "London",
        "employment_type": "Full-time",
        "start_date": "2020-01-20",
        "status": "active",
        "email": "sofia.rossi@example.com",
        "leave_plan": "UK-Standard",
        "notes": "Eligible for annual leave, sick leave, compassionate leave, and maternity leave.",
    },
}


@mcp.tool()
def db_lookup(record_id: str) -> str:
    """Run a structured lookup against employee records by employee ID.

    Args:
        record_id: Employee identifier (e.g. EMP-1001).

    Returns:
        A formatted record summary, or a not-found message.
    """
    key = (record_id or "").strip().upper()
    record = _RECORDS.get(key)
    if not record:
        return f"Employee record '{record_id}' not found. Known IDs: {', '.join(_RECORDS)}"
    lines = [f"{k}: {v}" for k, v in record.items()]
    return "\n".join(lines)


@mcp.tool()
def db_search(query: str) -> str:
    """Full-text search across employee fields.

    Args:
        query: Search terms such as employee name, department, role, gender, or manager.

    Returns:
        Newline-separated matching record summaries.
    """
    q = (query or "").lower()
    terms = q.split()
    matches: list[str] = []
    for record in _RECORDS.values():
        blob = " ".join(record.values()).lower()
        if terms and any(t in blob for t in terms):
            matches.append(
                f"{record['employee_id']} | {record['name']} | "
                f"{record['department']} | {record['role']} | {record['status']}"
            )
    if not matches:
        matches.append("No employee records matched the query.")
    return "\n".join(matches)


if __name__ == "__main__":
    mcp.run(transport="stdio")
