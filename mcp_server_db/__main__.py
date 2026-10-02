from typing import Literal

from mcp.server.fastmcp import FastMCP

from mock_airline.storage import database, page, result

mcp = FastMCP("customer_bookings")


@mcp.tool(
    description="Find fictional airline customers by name, customer ID or email; empty query lists customers. Resolve duplicate names before reading histories. Results are paginated.",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
def customer_search(query: str, offset: int = 0, limit: int = 20) -> dict:
    rows = database("customers").rows(
        "SELECT customer_id, name, email, city FROM customers "
        "WHERE instr(lower(name || ' ' || customer_id || ' ' || email), ?) > 0 ORDER BY customer_id",
        (query.strip().lower(),),
    )
    return page("customer_bookings", rows, offset, limit)


@mcp.tool(
    description="Read a customer profile and booking counts by status. Counts include all recorded bookings at the fixed as_of date; eligibility rules come from policy_get.",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
def customer_get(customer_id: str) -> dict:
    key = customer_id.strip().upper()
    store = database("customers")
    rows = store.rows("SELECT * FROM customers WHERE customer_id = ?", (key,))
    if not rows:
        return result("customer_bookings", status="not_found", customer_id=key)
    counts = store.rows(
        "SELECT status, count(*) AS count FROM bookings WHERE customer_id = ? GROUP BY status",
        (key,),
    )
    return result(
        "customer_bookings",
        status="found",
        customer=rows[0],
        booking_counts={row["status"]: row["count"] for row in counts},
        total_bookings=sum(row["count"] for row in counts),
        non_cancelled_bookings=sum(
            row["count"] for row in counts if row["status"] != "cancelled"
        ),
    )


@mcp.tool(
    description="List a customer's bookings in booking order, optionally filtered by status. Follow next_offset for complete history. qualifying_booking_number excludes cancelled bookings; amounts are AUD cents.",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
def booking_list(
    customer_id: str,
    status: Literal["all", "confirmed", "completed", "cancelled"] = "all",
    offset: int = 0,
    limit: int = 20,
) -> dict:
    rows = database("customers").rows(
        "SELECT * FROM booking_history WHERE customer_id = ? AND (? = 'all' OR status = ?) ORDER BY booked_at, booking_id",
        (customer_id.strip().upper(), status, status),
    )
    return page("customer_bookings", rows, offset, limit)


@mcp.tool(
    description="Read one booking's owner, fare, amount paid in AUD cents, cancellation details, qualifying booking number and recorded upgrade. Null fields mean unknown, not a default fare or approval.",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
def booking_get(booking_id: str) -> dict:
    key = booking_id.strip().upper()
    rows = database("customers").rows(
        "SELECT * FROM booking_history WHERE booking_id = ?", (key,)
    )
    return result(
        "customer_bookings",
        status="found" if rows else "not_found",
        booking=rows[0] if rows else None,
        booking_id=key,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
