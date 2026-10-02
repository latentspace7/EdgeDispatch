from typing import Literal

from mcp.server.fastmcp import FastMCP

from mock_airline.storage import database, page, result

mcp = FastMCP("support_crm")


@mcp.tool(
    description="List support-case summaries for a customer, most recent first. Empty results mean no matching CRM cases, not proof the customer does not exist. Read support_case_get for conversations.",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
def support_case_list(
    customer_id: str,
    status: Literal["all", "open", "pending", "resolved"] = "all",
    offset: int = 0,
    limit: int = 20,
) -> dict:
    rows = database("support").rows(
        "SELECT * FROM support_cases WHERE customer_id = ? AND (? = 'all' OR status = ?) ORDER BY opened_at DESC, case_id",
        (customer_id.strip().upper(), status, status),
    )
    return page("support_crm", rows, offset, limit)


@mcp.tool(
    description="Read one support case, its chronological conversation and associated refund records. Customer or agent messages are historical statements, not proof of payment, eligibility or a completed upgrade.",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
def support_case_get(case_id: str) -> dict:
    key = case_id.strip().upper()
    store = database("support")
    rows = store.rows("SELECT * FROM support_cases WHERE case_id = ?", (key,))
    if not rows:
        return result("support_crm", status="not_found", case_id=key)
    return result(
        "support_crm",
        status="found",
        case=rows[0],
        messages=store.rows(
            "SELECT * FROM case_messages WHERE case_id = ? ORDER BY sent_at, message_id",
            (key,),
        ),
        refunds=store.rows(
            "SELECT * FROM refund_records WHERE case_id = ? ORDER BY requested_at",
            (key,),
        ),
    )


@mcp.tool(
    description="Read a customer's refund history, optionally for one booking. requested/approved are not paid; only paid records with paid_at and amount_paid_cents establish payment. All amounts are AUD cents.",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
def refund_list(
    customer_id: str, booking_id: str = "", offset: int = 0, limit: int = 20
) -> dict:
    booking = booking_id.strip().upper()
    rows = database("support").rows(
        "SELECT * FROM refund_records WHERE customer_id = ? AND (? = '' OR booking_id = ?) ORDER BY requested_at DESC, refund_id",
        (customer_id.strip().upper(), booking, booking),
    )
    return page("support_crm", rows, offset, limit)


if __name__ == "__main__":
    mcp.run(transport="stdio")
