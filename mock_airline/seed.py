from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack, closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import AS_OF, DATASET_VERSION

DATA = Path(__file__).parent / "data"
NOW = datetime.fromisoformat(AS_OF)

CUSTOMER_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE customers (
 customer_id TEXT PRIMARY KEY, name TEXT NOT NULL, date_of_birth TEXT NOT NULL,
 address TEXT NOT NULL, city TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 phone TEXT NOT NULL, member_since TEXT NOT NULL
);
CREATE TABLE bookings (
 booking_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers,
 booked_at TEXT NOT NULL, flight_number TEXT NOT NULL, origin TEXT NOT NULL,
 destination TEXT NOT NULL, departure_at TEXT NOT NULL,
 cabin TEXT NOT NULL CHECK(cabin IN ('economy', 'business')),
 fare_type TEXT CHECK(fare_type IN ('Flex', 'Saver')),
 status TEXT NOT NULL CHECK(status IN ('confirmed', 'completed', 'cancelled')),
 cancelled_by TEXT CHECK(cancelled_by IN ('customer', 'airline')),
 cancelled_at TEXT, amount_paid_cents INTEGER NOT NULL CHECK(amount_paid_cents > 0),
 currency TEXT NOT NULL CHECK(currency = 'AUD'),
 upgrade_applied INTEGER NOT NULL CHECK(upgrade_applied IN (0, 1)),
 CHECK((status = 'cancelled' AND cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL)
    OR (status != 'cancelled' AND cancelled_by IS NULL AND cancelled_at IS NULL))
);
CREATE INDEX bookings_customer ON bookings(customer_id, booked_at);
CREATE VIEW booking_history AS
 SELECT *, CASE WHEN status = 'cancelled' THEN NULL ELSE
 SUM(CASE WHEN status != 'cancelled' THEN 1 ELSE 0 END)
 OVER (PARTITION BY customer_id ORDER BY booked_at, booking_id)
 END AS qualifying_booking_number FROM bookings;
"""

SUPPORT_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE support_cases (
 case_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, booking_id TEXT NOT NULL,
 category TEXT NOT NULL, subject TEXT NOT NULL, opened_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('open', 'pending', 'resolved')),
 resolution TEXT
);
CREATE INDEX cases_customer ON support_cases(customer_id, opened_at);
CREATE TABLE case_messages (
 message_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES support_cases,
 sent_at TEXT NOT NULL, author_type TEXT NOT NULL CHECK(author_type IN ('customer', 'agent')),
 text TEXT NOT NULL
);
CREATE TABLE refund_records (
 refund_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES support_cases,
 customer_id TEXT NOT NULL, booking_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('requested', 'approved', 'rejected', 'paid')),
 amount_requested_cents INTEGER NOT NULL CHECK(amount_requested_cents > 0),
 amount_paid_cents INTEGER NOT NULL CHECK(amount_paid_cents >= 0),
 currency TEXT NOT NULL CHECK(currency = 'AUD'), requested_at TEXT NOT NULL,
 paid_at TEXT, reason TEXT NOT NULL,
 CHECK((status = 'paid' AND paid_at IS NOT NULL AND amount_paid_cents > 0)
    OR (status != 'paid' AND paid_at IS NULL AND amount_paid_cents = 0))
);
CREATE INDEX refunds_customer ON refund_records(customer_id, booking_id);
"""


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def insert(connection: sqlite3.Connection, table: str, values: tuple):
    connection.execute(
        f"INSERT INTO {table} VALUES ({','.join('?' for _ in values)})", values
    )


def refund_status(customer: int) -> str | None:
    if customer in {6, 8}:
        return "requested"
    if customer == 7:
        return "paid"
    if customer in {10, 100} or customer % 2:
        return None
    if customer == 12:
        return "rejected"
    if customer % 8 == 0:
        return "paid"
    return "approved" if customer % 4 == 0 else "rejected"


def make_customers(connection: sqlite3.Connection):
    names = [
        "Maya Patel",
        "Liam Chen",
        "Alex Morgan",
        "Alex Morgan",
        "Zoe Turner",
        "Amir Khan",
        "Sofia Bennett",
        "Olivia Reed",
        "Isla Martin",
        "Lucas Nguyen",
        "Grace Wilson",
        "Daniel Kim",
    ]
    first = [
        "Ava",
        "Jack",
        "Chloe",
        "Oscar",
        "Ruby",
        "Leo",
        "Mia",
        "Hugo",
        "Ella",
        "Arjun",
    ]
    last = [
        "Taylor",
        "Singh",
        "Harris",
        "Zhang",
        "Evans",
        "Brown",
        "Thomas",
        "White",
        "Ahmed",
        "Scott",
    ]
    routes = [
        ("SYD", "MEL", "Sydney"),
        ("MEL", "BNE", "Melbourne"),
        ("BNE", "PER", "Brisbane"),
        ("PER", "ADL", "Perth"),
        ("ADL", "CBR", "Adelaide"),
        ("CBR", "SYD", "Canberra"),
        ("OOL", "SYD", "Gold Coast"),
        ("HBA", "MEL", "Hobart"),
    ]
    counts = [11, 10, 12, 13, 11, 11, 11, 7, 6, 9, 8, 12] + [
        5 + i % 11 for i in range(13, 101)
    ]
    counts[-1] += 1000 - sum(counts)
    for number, count in enumerate(counts, 1):
        customer_id = f"CUST-{number:04d}"
        name = (
            names[number - 1]
            if number <= len(names)
            else f"{first[(number - 13) // 10]} {last[(number - 13) % 10]}"
        )
        origin, destination, city = routes[(number - 1) % len(routes)]
        insert(
            connection,
            "customers",
            (
                customer_id,
                name,
                f"{1960 + number % 43:04d}-{1 + number % 12:02d}-{1 + number % 27:02d}",
                f"Unit {number}, {100 + number} Example Street",
                city,
                f"customer{number:03d}@example.com",
                f"+61-TEST-{number:04d}",
                f"{2010 + number % 9}-01-15",
            ),
        )
        for sequence in range(1, count + 1):
            latest = sequence == count
            departure = (
                NOW + timedelta(days=10 + number % 20, hours=number % 18)
                if latest
                else NOW - timedelta(days=45 * (count - sequence), hours=2)
            )
            booked = departure - timedelta(days=45)
            status = "confirmed" if latest else "completed"
            cancelled_by = None
            cancelled_at = None
            fare = "Flex" if (number + sequence) % 3 == 0 else "Saver"
            cabin = "economy"
            upgraded = 0
            if (number == 4 and sequence in {2, 5}) or (
                number > 12 and number % 5 == 0 and sequence == 2
            ):
                status, cancelled_by = "cancelled", "customer"
                cancelled_at = stamp(departure - timedelta(days=5))
            if latest:
                refund = refund_status(number)
                if refund in {"paid", "approved", "requested"}:
                    fare = "Flex"
                if refund == "rejected":
                    fare = "Saver"
                if refund == "paid":
                    status, cancelled_by, cancelled_at = (
                        "cancelled",
                        "customer",
                        stamp(NOW - timedelta(days=7)),
                    )
                if number == 5:
                    cabin, upgraded = "business", 1
                if number == 6:
                    fare, status, cancelled_by, cancelled_at = (
                        "Saver",
                        "cancelled",
                        "airline",
                        stamp(NOW - timedelta(days=8)),
                    )
                if number in {9, 12}:
                    fare = "Saver"
                if number == 10:
                    fare = "Flex"
                if number == 11:
                    fare = None
            insert(
                connection,
                "bookings",
                (
                    f"BOOK-{number:04d}-{sequence:02d}",
                    customer_id,
                    stamp(booked),
                    f"SC{100 + number * 3 + sequence}",
                    origin,
                    destination,
                    stamp(departure),
                    cabin,
                    fare,
                    status,
                    cancelled_by,
                    cancelled_at,
                    12000 + ((number * 17 + sequence * 31) % 90) * 100,
                    "AUD",
                    upgraded,
                ),
            )


def make_support(connection: sqlite3.Connection, customers: sqlite3.Connection):
    topics = [
        (
            "baggage",
            "Delayed checked bag",
            "My checked bag did not arrive on the same flight.",
            "The baggage desk located the bag and arranged delivery.",
            "Bag delivered; customer confirmed receipt.",
        ),
        (
            "seat",
            "Aisle-seat request",
            "I requested an aisle seat, but the boarding pass showed a window seat.",
            "The airport agent changed the seat assignment before boarding.",
            "Seat issue resolved at the airport.",
        ),
        (
            "invoice",
            "Duplicate itinerary receipt",
            "I need a replacement receipt for my travel expense claim.",
            "A copy of the existing itinerary receipt was sent; no payment or refund was made.",
            "Receipt copy supplied.",
        ),
        (
            "accessibility",
            "Assistance confirmation",
            "Was my airport assistance request passed to the departure team?",
            "The departure team acknowledged the request in this support case.",
            "Assistance request acknowledged; this is not a cabin upgrade.",
        ),
        (
            "meal",
            "Meal preference enquiry",
            "My meal preference was not visible on the itinerary.",
            "The agent recorded the preference and explained that fulfilment was not guaranteed.",
            "Preference recorded; no guarantee of meal availability.",
        ),
        (
            "check_in",
            "Online check-in problem",
            "Online check-in displayed an error for my booking.",
            "An airport agent completed check-in after verifying the booking reference.",
            "Check-in completed; no fare change.",
        ),
        (
            "schedule",
            "Departure time clarification",
            "Two emails showed different departure times.",
            "The agent checked the booking record and explained the revised departure time.",
            "Schedule clarification supplied.",
        ),
    ]
    for index in range(200):
        number = index // 2 + 1 if index < 198 else 1
        customer_id = f"CUST-{number:04d}"
        recent = index % 2 == 1 and index < 198
        booking = customers.execute(
            "SELECT * FROM bookings WHERE customer_id = ? ORDER BY booked_at "
            + ("DESC" if recent else "ASC")
            + " LIMIT 1",
            (customer_id,),
        ).fetchone()
        category, subject, opening, response, resolution = topics[
            (number + index) % len(topics)
        ]
        status = "resolved"
        opened = (
            NOW - timedelta(days=6)
            if recent
            else datetime.fromisoformat(booking["departure_at"]) + timedelta(days=1)
        )
        refund = refund_status(number) if recent else None
        if recent and number <= 5:
            category, subject = "upgrade", "Eleventh-booking upgrade enquiry"
            opening = "Can you check whether my next flight qualifies for the business-class upgrade?"
            response = "Eligibility must be checked against the booking history and AIR-UPGRADE-01. This conversation does not apply an upgrade."
            status, resolution = "open", None
        if refund:
            category, subject = "refund", "Refund request and payment status"
            opening = "Please check the refund for this booking and tell me whether the money has been paid."
            response = {
                "requested": "The refund request is recorded and awaiting review. Nothing has been paid.",
                "approved": "The refund has been approved but payment has not been recorded.",
                "rejected": "The voluntary refund request was rejected because this booking uses the Saver fare.",
                "paid": "The refund payment is recorded as completed; the refund ledger contains the amount and date.",
            }[refund]
            status = "pending" if refund in {"requested", "approved"} else "resolved"
            resolution = None if status == "pending" else response
        if recent and number == 12:
            response = "I previously said your Saver fare would definitely be refunded. That statement was incorrect; review the later refund decision and AIR-REFUND-01."
            resolution = "Refund rejected under Saver voluntary-cancellation rule; earlier promise corrected."
        if recent and number == 11:
            category, subject, status, resolution = (
                "refund",
                "Missing fare information",
                "open",
                None,
            )
            opening = "Can you confirm whether my unused ticket is refundable?"
            response = "The imported booking has no fare type. We need the fare record before deciding eligibility."
        case_id = f"CASE-{index + 1:04d}"
        insert(
            connection,
            "support_cases",
            (
                case_id,
                customer_id,
                booking["booking_id"],
                category,
                subject,
                stamp(opened),
                status,
                resolution,
            ),
        )
        messages = [
            opening,
            f"I located booking {booking['booking_id']} for {booking['origin']} to {booking['destination']}. I will review the case without changing the booking.",
            f"That is the booking I mean. Please keep this enquiry linked to customer {customer_id}.",
            response,
            "Thank you. Please keep the explanation on this case so the next agent can review it.",
        ]
        for position, text in enumerate(messages):
            insert(
                connection,
                "case_messages",
                (
                    f"MSG-{index + 1:04d}-{position + 1}",
                    case_id,
                    stamp(
                        opened
                        + timedelta(
                            hours=position,
                            days=2 if refund == "paid" and position >= 3 else 0,
                        )
                    ),
                    "customer" if position % 2 == 0 else "agent",
                    text,
                ),
            )
        if refund:
            insert(
                connection,
                "refund_records",
                (
                    f"REF-{number:04d}",
                    case_id,
                    customer_id,
                    booking["booking_id"],
                    refund,
                    booking["amount_paid_cents"],
                    booking["amount_paid_cents"] if refund == "paid" else 0,
                    "AUD",
                    stamp(opened + timedelta(hours=1)),
                    stamp(opened + timedelta(days=2)) if refund == "paid" else None,
                    resolution or response,
                ),
            )


def make_policies() -> list[dict]:
    texts = [
        (
            "AIR-UPGRADE-01",
            "Eleventh qualifying booking upgrade",
            "A customer receives a one-time economy-to-business upgrade on exactly their eleventh qualifying booking. Count confirmed and completed bookings in booked_at order, using booking_id to break ties; exclude cancelled bookings. The booking_history qualifying_booking_number is this ordinal at the dataset as_of time. Ten prior qualifying bookings plus the target booking make eleven. Do not count only completed flights. The target must be confirmed, depart after as_of, have economy cabin, and have upgrade_applied=0. The twelfth or later booking does not qualify, even if the benefit was not used. If upgrade_applied=1, report that it was already applied; do not award it again. An existing business booking needs no economy-to-business upgrade. Assume upgrade seat capacity is available in this fictional dataset. Eligibility is not proof that a change was executed; these MCP tools cannot change bookings. Cite the booking and this policy when explaining a decision.",
        ),
        (
            "AIR-REFUND-01",
            "Refund eligibility and duplicate payment rules",
            "Apply these rules at the dataset as_of time to the identified booking. First inspect CRM refund records: paid means the recorded refund has already been paid and no duplicate refund is due; requested and approved mean a request exists, not that payment occurred. You may still explain policy eligibility for a pending request, but do not propose a second request. An airline-cancelled booking qualifies for a full refund of amount_paid_cents, including Saver fares. This exception takes precedence over the voluntary Saver restriction. Otherwise a completed flight is not refundable under this mock policy. An unused Flex booking is fully refundable before departure, including one cancelled by the customer before departure. An unused Saver booking is not refundable for voluntary cancellation. A booking at or after departure has no voluntary refund entitlement under this mock policy. Missing fare information prevents deciding voluntary eligibility; ask for the missing record. Do not infer Flex from a customer's previous tickets. All amounts are AUD cents; divide by 100 for dollars. Prior refunds on other bookings do not by themselves disqualify the customer. Partial refunds, fees and compensation are outside this fixture. A support promise does not override these rules or establish payment. These read-only tools cannot issue refunds.",
        ),
        (
            "AIR-FARES-01",
            "Booking fields, fares and mock scope",
            "Southern Cross Air is fictional and these are not Qantas policies. Each booking represents one passenger on one direct flight, with no connections, group tickets, loyalty points or codeshares. Flex and Saver are fare types; economy and business are cabins. A business cabin alone does not identify a Flex fare. confirmed means an upcoming booked flight; completed means travel already occurred; cancelled means the ticket is excluded from upgrade booking counts. A null fare_type means unknown. Booking counts and qualifying ordinals are calculated from the customer database, not from support cases. Use the fixed as_of timestamp supplied by tools instead of today's wall-clock date. All prices and refunds use integer AUD cents. All profiles are synthetic.",
        ),
        (
            "AIR-RECORDS-01",
            "Customer identity and support record authority",
            "Use customer_id to connect customer, booking and CRM records; use booking_id for booking-specific refund checks. Names are not unique. If a name search returns multiple customers, request a customer ID or matching contact detail before selecting a record. The customer database owns profile, booking and applied-upgrade facts. The CRM owns cases, message history and recorded refund status. Messages are dated statements, including customer claims and agent mistakes; they are not a payment ledger or policy override. A resolved case can have a rejected refund and does not imply money was paid. requested, approved, rejected and paid are different refund states; only paid with a paid_at timestamp and amount_paid_cents establishes payment in this mock. No matching cases means no recorded matching CRM history, not that a customer does not exist. Report conflicts or missing evidence explicitly. A tool error is not an empty search result. No tools in this environment can change records or contact a customer.",
        ),
    ]
    return [
        {
            "policy_id": key,
            "title": title,
            "version": "1.0",
            "effective_from": "2026-01-01",
            "applies_at": AS_OF,
            "text": text,
        }
        for key, title, text in texts
    ]


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        customers = stack.enter_context(closing(sqlite3.connect(":memory:")))
        support = stack.enter_context(closing(sqlite3.connect(":memory:")))
        customers.row_factory = sqlite3.Row
        customers.executescript(CUSTOMER_SCHEMA)
        support.executescript(SUPPORT_SCHEMA)
        make_customers(customers)
        make_support(support, customers)
        counts = {}
        for name, connection, tables in (
            ("customers", customers, ("customers", "bookings")),
            ("support", support, ("support_cases", "case_messages", "refund_records")),
        ):
            connection.commit()
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError(f"Invalid foreign key references in {name}")
            (DATA / f"{name}.sql").write_text("\n".join(connection.iterdump()) + "\n")
            counts.update(
                {
                    table: connection.execute(
                        f"SELECT count(*) FROM {table}"
                    ).fetchone()[0]
                    for table in tables
                }
            )
        policies = make_policies()
        (DATA / "policies.json").write_text(json.dumps(policies, indent=2) + "\n")
        (DATA / "manifest.json").write_text(
            json.dumps(
                {
                    "dataset_version": DATASET_VERSION,
                    "as_of": AS_OF,
                    "synthetic": True,
                    "counts": counts | {"policies": len(policies)},
                },
                indent=2,
            )
            + "\n"
        )
    print(json.dumps(counts | {"policies": len(policies)}, sort_keys=True))


if __name__ == "__main__":
    main()
