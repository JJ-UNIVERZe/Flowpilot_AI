"""Local SQLite sample order database for the FlowPilot demonstration.

Stage 3 stored orders with delivery information. Stage 4 adds refund and
payment details. All records are fictional; no real customer data is used.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

# Keep orders.db beside this Python file, regardless of the current directory.
DATABASE_PATH = Path(__file__).with_name("orders.db")

# Customer-safe error text. These messages never contain SQL, file paths, or
# raw SQLite output, so they are safe to show in the terminal and in a reply.
DATABASE_UNAVAILABLE_TEXT = "The sample order database is not available right now."
DATABASE_CREATE_FAILED_TEXT = "The sample order database could not be prepared."


class DatabaseError(RuntimeError):
    """Raised when the local sample database cannot be opened or read.

    It subclasses RuntimeError, so any older code that catches RuntimeError
    keeps working. The message is always safe to show to a customer: it never
    exposes SQL statements, file paths, or the raw SQLite error text.
    """

# Each sample order carries the Stage 3 delivery fields plus the Stage 4
# refund/payment fields:
#   (order_id, customer_name, product_name, order_status, payment_status,
#    delivery_date, refund_status, refund_amount, payment_method,
#    transaction_status)
SAMPLE_ORDERS = [
    ("ORD1042", "Alex Example", "Wireless Headphones", "Shipped", "Paid",
     "2026-11-03", "No Refund Requested", 0.00, "Visa ending 4242",
     "Payment Successful"),
    ("ORD2050", "Jamie Sample", "Desk Lamp", "Processing", "Paid",
     "2026-11-08", "Refund Requested", 49.99, "Mastercard ending 8813",
     "Payment Successful"),
    ("ORD3187", "Taylor Fiction", "Coffee Maker", "Delivered", "Paid",
     "2026-10-24", "Refund Processing", 89.50, "PayPal ending 7702",
     "Payment Successful"),
    ("ORD4261", "Morgan Demo", "Travel Backpack", "Delayed", "Paid",
     "2026-11-06", "Refund Completed", 120.00, "American Express ending 1005",
     "Payment Successful"),
    ("ORD5734", "Riley Placeholder", "Bluetooth Speaker", "Processing", "Pending",
     "2026-11-10", "No Refund Requested", 0.00, "Bank Transfer",
     "Payment Pending"),
    ("ORD6842", "Casey Fictional", "Mechanical Keyboard", "Shipped", "Failed",
     "2026-11-12", "No Refund Requested", 0.00, "Visa ending 3319",
     "Payment Failed"),
    ("ORD7391", "Fictional Example", "Fictional Desk Organizer", "Delivered", "Paid",
     "2026-11-01", "No Refund Requested", 0.00, "Visa ending 1111",
     "Payment Successful"),
]

# Allowed values for an approval decision. The same list is enforced by a
# CHECK constraint, so an invalid status cannot be stored even if some future
# caller forgets to validate it in Python.
APPROVAL_STATUSES = ("Pending", "Approved", "Rejected")

# Audit log for the human-in-the-loop review step. CREATE TABLE IF NOT EXISTS
# means existing logs are kept: this never drops or rewrites history.
APPROVAL_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS approval_logs (
        approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_message TEXT NOT NULL,
        generated_response TEXT NOT NULL,
        approval_status TEXT NOT NULL
            CHECK (approval_status IN ('Pending', 'Approved', 'Rejected')),
        reviewed_at TEXT NOT NULL
    )
"""
APPROVAL_NO_UPDATE_TRIGGER_SQL = """
    CREATE TRIGGER IF NOT EXISTS approval_logs_no_update
    BEFORE UPDATE ON approval_logs
    BEGIN
        SELECT RAISE(ABORT, 'approval audit records are immutable');
    END
"""
APPROVAL_NO_DELETE_TRIGGER_SQL = """
    CREATE TRIGGER IF NOT EXISTS approval_logs_no_delete
    BEFORE DELETE ON approval_logs
    BEGIN
        SELECT RAISE(ABORT, 'approval audit records are immutable');
    END
"""

# New Stage 4 columns and the SQLite type used when adding them to an
# existing database. The defaults keep old rows readable until they are
# backfilled with sample values below.
NEW_COLUMNS = [
    ("refund_status", "TEXT NOT NULL DEFAULT 'No Refund Requested'"),
    ("refund_amount", "REAL NOT NULL DEFAULT 0.0"),
    ("payment_method", "TEXT NOT NULL DEFAULT 'Unknown'"),
    ("transaction_status", "TEXT NOT NULL DEFAULT 'Unknown'"),
    ("customer_id", "TEXT"),
    ("items", "TEXT"),
    ("amount", "REAL"),
    ("order_date", "TEXT"),
]

AGENT_ORDER_SEEDS = (
    ("ORD1042", "CUST1001", '[{"name":"Wireless Headphones","category":"electronics"}]',
     129.99, "2026-10-25"),
    ("ORD2050", "CUST1002", '[{"name":"Desk Lamp","category":"home"}]',
     49.99, "2026-11-01"),
    ("ORD3187", "CUST1003", '[{"name":"Coffee Maker","category":"home"}]',
     89.50, "2026-10-10"),
    ("ORD4261", "CUST1004", '[{"name":"Travel Backpack","category":"accessories"}]',
     120.00, "2026-10-01"),
    ("ORD5734", "CUST1005", '[{"name":"Bluetooth Speaker","category":"electronics"}]',
     64.99, "2026-11-04"),
    ("ORD6842", "CUST1006", '[{"name":"Mechanical Keyboard","category":"electronics"}]',
     99.00, "2026-11-05"),
    ("ORD7391", "CUST1007", '[{"name":"Fictional Desk Organizer","category":"home"}]',
     75.00, "2026-10-20"),
)
REFUND_SEEDS = (
    ("ORD2050", "CUST1002", "Requested", 49.99, "Fictional request is under review."),
    ("ORD3187", "CUST1003", "Processing", 89.50, "Fictional refund is processing."),
    ("ORD4261", "CUST1004", "Completed", 120.00, "Fictional refund completed."),
)


def _migrate_schema(connection: sqlite3.Connection) -> None:
    """Add any missing Stage 4 columns without dropping existing records."""
    existing_columns = {
        row[1]  # row[1] is the column name in PRAGMA table_info output
        for row in connection.execute("PRAGMA table_info(orders)").fetchall()
    }
    for column_name, column_definition in NEW_COLUMNS:
        if column_name not in existing_columns:
            # ALTER TABLE keeps the existing rows; new columns get the default.
            connection.execute(
                f"ALTER TABLE orders ADD COLUMN {column_name} {column_definition}"
            )


def _ensure_approval_audit_integrity(connection: sqlite3.Connection) -> None:
    """Create the audit table and prevent ordinary updates or deletes."""
    connection.execute(APPROVAL_TABLE_SQL)
    connection.execute(APPROVAL_NO_UPDATE_TRIGGER_SQL)
    connection.execute(APPROVAL_NO_DELETE_TRIGGER_SQL)


def create_database() -> None:
    """Create the orders table, migrate older databases, and add sample data."""
    try:
        # closing() really closes the file handle. "with sqlite3.connect(...)"
        # alone only commits, which would leave the file locked on Windows.
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    customer_name TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    order_status TEXT NOT NULL,
                    payment_status TEXT NOT NULL,
                    delivery_date TEXT NOT NULL,
                    refund_status TEXT NOT NULL DEFAULT 'No Refund Requested',
                    refund_amount REAL NOT NULL DEFAULT 0.0,
                    payment_method TEXT NOT NULL DEFAULT 'Unknown',
                    transaction_status TEXT NOT NULL DEFAULT 'Unknown'
                )
                """
            )
            # Stage 4 migration: an orders.db created in Stage 3 only has the
            # first six columns, so add the refund/payment columns in place.
            _migrate_schema(connection)

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS refunds (
                    refund_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL UNIQUE,
                    customer_id TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('Requested', 'Processing', 'Completed', 'Rejected')),
                    amount REAL NOT NULL CHECK (amount >= 0),
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (order_id) REFERENCES orders(order_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_call_logs (
                    tool_call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    result_status TEXT NOT NULL,
                    duration_ms REAL NOT NULL CHECK (duration_ms >= 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Stage 6 (approval): add the audit table to new and existing
            # databases. Existing approval rows are left untouched.
            _ensure_approval_audit_integrity(connection)

            # Fresh databases get the full sample rows here. Existing rows are
            # left untouched by INSERT OR IGNORE.
            connection.executemany(
                """
                INSERT OR IGNORE INTO orders
                    (order_id, customer_name, product_name, order_status,
                     payment_status, delivery_date, refund_status,
                     refund_amount, payment_method, transaction_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                SAMPLE_ORDERS,
            )
            connection.executemany(
                """
                UPDATE orders
                SET customer_id = ?, items = ?, amount = ?, order_date = ?
                WHERE order_id = ? AND customer_id IS NULL
                """,
                [
                    (customer_id, items, amount, order_date, order_id)
                    for order_id, customer_id, items, amount, order_date
                    in AGENT_ORDER_SEEDS
                ],
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO refunds
                    (order_id, customer_id, status, amount, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                REFUND_SEEDS,
            )

            # Seed rows only when absent. Existing order, refund, and payment
            # facts are read-only and must never be overwritten by startup.
    except sqlite3.Error as error:
        # The technical detail stays on the exception chain for debugging; the
        # message itself is safe to show to a customer.
        raise DatabaseError(DATABASE_CREATE_FAILED_TEXT) from error


def _table_exists(connection: sqlite3.Connection) -> bool:
    """True when the orders table exists in this database file."""
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
    ).fetchone()
    return row is not None


def ensure_database_ready() -> None:
    """Make sure orders.db and its orders table exist before a lookup.

    Three situations are handled here:
        1. The file is missing      -> create it with the sample records.
        2. The file exists but has no orders table (for example an empty or
           brand-new file)      -> create the table in place.
        3. The file cannot be read  -> raise DatabaseError, which the workflow
           reports as a database problem and never as an unknown order.

    Creating the table only ADDS data. Existing records are never dropped.
    """
    if not DATABASE_PATH.exists():
        create_database()
        return

    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            if _table_exists(connection):
                return
    except sqlite3.Error as error:
        raise DatabaseError(DATABASE_UNAVAILABLE_TEXT) from error

    # File exists but the table does not: initialize it without deleting data.
    create_database()


def _fetch_one(query: str, parameters: tuple) -> dict | None:
    """Run one SELECT and return a dictionary, or None when no row matched.

    A missing row is a normal answer (the order ID is simply not in the
    database) and returns None. A database problem raises DatabaseError
    instead, so the two cases can never be confused.
    """
    ensure_database_ready()
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(query, parameters).fetchone()
            return dict(row) if row is not None else None
    except sqlite3.Error as error:
        raise DatabaseError(DATABASE_UNAVAILABLE_TEXT) from error


def get_order(order_id: str) -> dict | None:
    """Return one order as a dictionary, or None when its ID is unknown."""
    return _fetch_one(
        """
        SELECT order_id, customer_name, product_name, order_status,
               payment_status, delivery_date
        FROM orders
        WHERE order_id = ?
        """,
        (order_id,),
    )


def get_refund_status(order_id: str) -> dict | None:
    """Return refund status and amount for an order, or None if it is unknown.

    The returned dictionary contains:
        order_id, refund_status, refund_amount (float)
    """
    return _fetch_one(
        """
        SELECT order_id, refund_status, refund_amount
        FROM orders
        WHERE order_id = ?
        """,
        (order_id,),
    )


def get_payment_status(order_id: str) -> dict | None:
    """Return payment method and transaction status, or None if unknown.

    The returned dictionary contains:
        order_id, payment_method, transaction_status
    """
    return _fetch_one(
        """
        SELECT order_id, payment_method, transaction_status
        FROM orders
        WHERE order_id = ?
        """,
        (order_id,),
    )


# ---------------------------------------------------------------------------
# STAGE 6 — APPROVAL AUDIT LOG
# ---------------------------------------------------------------------------
def _ensure_approval_table() -> None:
    """Create the approval_logs table when it is missing (older databases)."""
    ensure_database_ready()   # makes sure orders.db itself exists first
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            _ensure_approval_audit_integrity(connection)
    except sqlite3.Error as error:
        raise DatabaseError(DATABASE_UNAVAILABLE_TEXT) from error


def save_approval(customer_message: str, generated_response: str,
                  approval_status: str, reviewed_at: str) -> int:
    """Insert one review decision and return the new approval_id.

    Every value is passed as a parameter placeholder (?), so message text can
    never be treated as SQL.
    """
    if approval_status not in APPROVAL_STATUSES:
        raise ValueError(f"Invalid approval status: {approval_status!r}")

    _ensure_approval_table()
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO approval_logs
                    (customer_message, generated_response,
                     approval_status, reviewed_at)
                VALUES (?, ?, ?, ?)
                """,
                (customer_message, generated_response,
                 approval_status, reviewed_at),
            )
            return int(cursor.lastrowid)
    except sqlite3.Error as error:
        raise DatabaseError(DATABASE_UNAVAILABLE_TEXT) from error


def get_approval(approval_id: int) -> dict | None:
    """Return one audit-log record as a dictionary, or None if not found.

    The returned dictionary contains:
        approval_id, customer_message, generated_response,
        approval_status, reviewed_at
    """
    _ensure_approval_table()
    return _fetch_one(
        """
        SELECT approval_id, customer_message, generated_response,
               approval_status, reviewed_at
        FROM approval_logs
        WHERE approval_id = ?
        """,
        (approval_id,),
    )
