"""Durable, duplicate-resistant order ledger for broker execution.

The ledger records intent before any network submission. An uncertain broker
response must be marked UNKNOWN and reconciled with the broker; UNKNOWN orders
are never eligible for automatic resubmission.
"""
from __future__ import annotations

from contextlib import closing
import sqlite3
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

PENDING: Final = "PENDING"
SUBMITTED: Final = "SUBMITTED"
FILLED: Final = "FILLED"
REJECTED: Final = "REJECTED"
CANCELLED: Final = "CANCELLED"
UNKNOWN: Final = "UNKNOWN"
PARTIALLY_FILLED: Final = "PARTIALLY_FILLED"
TERMINAL_STATES: Final = frozenset({FILLED, REJECTED, CANCELLED})
ALL_STATES: Final = frozenset({PENDING, SUBMITTED, PARTIALLY_FILLED, FILLED, REJECTED, CANCELLED, UNKNOWN})
RECONCILED_STATES: Final = frozenset({SUBMITTED, FILLED, REJECTED, CANCELLED})


@dataclass(frozen=True)
class OrderRecord:
    client_order_id: str
    symbol: str
    side: str
    volume_units: int
    filled_volume_units: int
    status: str
    created_at: int
    updated_at: int
    broker_order_id: str | None = None
    last_error: str | None = None
    broker_position_id: str | None = None
    strategy_mode: str | None = None
    initial_cash_risk: Decimal | None = None


class OrderLedger:
    """SQLite WAL ledger; reserve is the mandatory pre-submission operation."""

    def __init__(self, database: str | Path, *, account_id: int | None = None) -> None:
        self.database = str(database)
        if account_id is not None and (
            isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0
        ):
            raise ValueError("account_id must be a positive integer when supplied.")
        self._account_id = account_id
        self._lock = threading.RLock()
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS orders (
                    client_order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    volume_units INTEGER NOT NULL CHECK(volume_units > 0),
                    filled_volume_units INTEGER NOT NULL DEFAULT 0
                        CHECK(filled_volume_units >= 0 AND filled_volume_units <= volume_units),
                    status TEXT NOT NULL CHECK(status IN
                        ('PENDING','SUBMITTED','PARTIALLY_FILLED','FILLED','REJECTED','CANCELLED','UNKNOWN')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    broker_order_id TEXT UNIQUE,
                    broker_position_id TEXT,
                    last_error TEXT,
                    strategy_mode TEXT,
                    initial_cash_risk TEXT
                )"""
            )
            schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='orders'"
            ).fetchone()["sql"]
            if "PARTIALLY_FILLED" not in schema or "filled_volume_units" not in schema:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """CREATE TABLE orders_v2 (
                        client_order_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                        volume_units INTEGER NOT NULL CHECK(volume_units > 0),
                        filled_volume_units INTEGER NOT NULL DEFAULT 0
                            CHECK(filled_volume_units >= 0 AND filled_volume_units <= volume_units),
                        status TEXT NOT NULL CHECK(status IN
                            ('PENDING','SUBMITTED','PARTIALLY_FILLED','FILLED','REJECTED','CANCELLED','UNKNOWN')),
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        broker_order_id TEXT UNIQUE,
                        broker_position_id TEXT,
                        last_error TEXT,
                        strategy_mode TEXT,
                        initial_cash_risk TEXT
                    )"""
                )
                db.execute(
                    """INSERT INTO orders_v2
                       (client_order_id,symbol,side,volume_units,filled_volume_units,status,
                        created_at,updated_at,broker_order_id,last_error)
                       SELECT client_order_id,symbol,side,volume_units,0,status,
                              created_at,updated_at,broker_order_id,last_error FROM orders"""
                )
                db.execute("DROP TABLE orders")
                db.execute("ALTER TABLE orders_v2 RENAME TO orders")
                db.execute("COMMIT")
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(orders)").fetchall()
            }
            if "broker_position_id" not in columns:
                db.execute("ALTER TABLE orders ADD COLUMN broker_position_id TEXT")
            if "strategy_mode" not in columns:
                db.execute("ALTER TABLE orders ADD COLUMN strategy_mode TEXT")
            if "initial_cash_risk" not in columns:
                db.execute("ALTER TABLE orders ADD COLUMN initial_cash_risk TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS order_ledger_account (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    account_id INTEGER NOT NULL CHECK(account_id>0)
                )"""
            )
            stored_account = db.execute(
                "SELECT account_id FROM order_ledger_account WHERE singleton=1"
            ).fetchone()
            if stored_account is None and account_id is not None:
                db.execute(
                    "INSERT INTO order_ledger_account(singleton,account_id) VALUES(1,?)",
                    (account_id,),
                )
            elif stored_account is not None and (
                account_id is None or stored_account["account_id"] != account_id
            ):
                raise ValueError(
                    "Order database is bound to a different cTrader account; use its original account ID."
                )
            self._account_id = stored_account["account_id"] if stored_account is not None else account_id
        try:
            Path(self.database).chmod(0o600)
        except OSError:
            pass

    @property
    def bound_account_id(self) -> int | None:
        """Account identity permanently bound to this database, if configured."""
        return self._account_id

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _text(value: object, field: str, *, max_length: int = 256) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > max_length:
            raise ValueError(f"{field} must be a non-empty string of at most {max_length} characters.")
        return value.strip()

    @staticmethod
    def _record(row: sqlite3.Row | None) -> OrderRecord | None:
        if row is None:
            return None
        return OrderRecord(
            client_order_id=row["client_order_id"],
            symbol=row["symbol"],
            side=row["side"],
            volume_units=row["volume_units"],
            filled_volume_units=row["filled_volume_units"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            broker_order_id=row["broker_order_id"],
            last_error=row["last_error"],
            broker_position_id=row["broker_position_id"],
            strategy_mode=row["strategy_mode"],
            initial_cash_risk=(
                Decimal(row["initial_cash_risk"])
                if row["initial_cash_risk"] is not None else None
            ),
        )

    def reserve(self, client_order_id: str, intent: object, *, now: int) -> bool:
        """Atomically reserve a unique client ID before a single submit attempt.

        Returns False for any existing ID, including UNKNOWN. This method never
        overwrites or resets an existing order.
        """
        order_id = self._text(client_order_id, "client_order_id")
        symbol = self._text(getattr(intent, "symbol", None), "symbol", max_length=64)
        side_value = getattr(intent, "side", None)
        side = getattr(side_value, "value", side_value)
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL.")
        volume = getattr(intent, "volume_units", None)
        if isinstance(volume, bool) or not isinstance(volume, int) or volume <= 0:
            raise ValueError("volume_units must be a positive integer.")
        strategy_mode = getattr(intent, "strategy_mode", None)
        initial_cash_risk = getattr(intent, "initial_cash_risk", None)
        if strategy_mode is None and initial_cash_risk is None:
            risk_text = None
        else:
            if strategy_mode not in {"SCALPING", "INTRADAY"} or initial_cash_risk is None:
                raise ValueError("strategy_mode and initial_cash_risk must be supplied together.")
            try:
                risk_value = Decimal(str(initial_cash_risk))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("initial_cash_risk must be a finite positive decimal.") from exc
            if not risk_value.is_finite() or risk_value <= 0:
                raise ValueError("initial_cash_risk must be a finite positive decimal.")
            risk_text = str(risk_value)
        if isinstance(now, bool) or not isinstance(now, int) or now < 0:
            raise ValueError("now must be a non-negative integer timestamp.")
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """INSERT OR IGNORE INTO orders
                   (client_order_id,symbol,side,volume_units,status,created_at,updated_at,
                    strategy_mode,initial_cash_risk)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (order_id, symbol, side, volume, PENDING, now, now,
                 strategy_mode, risk_text),
            )
            inserted = cursor.rowcount == 1
            db.execute("COMMIT")
            return inserted

    def _transition(
        self,
        client_order_id: str,
        *,
        allowed_from: frozenset[str],
        status: str,
        now: int,
        broker_order_id: str | None = None,
        error: str | None = None,
    ) -> bool:
        order_id = self._text(client_order_id, "client_order_id")
        if status not in ALL_STATES:
            raise ValueError("Unsupported order status.")
        if isinstance(now, bool) or not isinstance(now, int) or now < 0:
            raise ValueError("now must be a non-negative integer timestamp.")
        broker_id = None if broker_order_id is None else self._text(
            broker_order_id, "broker_order_id"
        )
        safe_error = None if error is None else self._text(error, "error", max_length=1000)
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, broker_order_id FROM orders WHERE client_order_id=?",
                (order_id,),
            ).fetchone()
            if row is None or row["status"] not in allowed_from:
                db.execute("ROLLBACK")
                return False
            if row["broker_order_id"] and broker_id and row["broker_order_id"] != broker_id:
                db.execute("ROLLBACK")
                raise ValueError("Broker order ID cannot be changed once recorded.")
            db.execute(
                """UPDATE orders SET status=?,updated_at=?,
                   broker_order_id=COALESCE(broker_order_id,?),
                   last_error=?,
                   filled_volume_units=CASE WHEN ?='FILLED' THEN volume_units
                                            ELSE filled_volume_units END
                   WHERE client_order_id=?""",
                (status, now, broker_id, safe_error, status, order_id),
            )
            db.execute("COMMIT")
            return True

    def mark_submitted(self, client_order_id: str, broker_order_id: str, *, now: int) -> bool:
        """Record a confirmed broker acknowledgement; not for uncertain responses."""
        return self._transition(
            client_order_id, allowed_from=frozenset({PENDING}),
            status=SUBMITTED, now=now, broker_order_id=broker_order_id,
        )

    def mark_partial_fill(
        self,
        client_order_id: str,
        filled_volume_units: int,
        broker_order_id: str,
        *,
        now: int,
        broker_position_id: str | None = None,
    ) -> bool:
        """Record broker-confirmed cumulative fill and its position correlation."""
        order_id = self._text(client_order_id, "client_order_id")
        broker_id = self._text(broker_order_id, "broker_order_id")
        if isinstance(filled_volume_units, bool) or not isinstance(filled_volume_units, int) or filled_volume_units <= 0:
            raise ValueError("filled_volume_units must be a positive integer.")
        if isinstance(now, bool) or not isinstance(now, int) or now < 0:
            raise ValueError("now must be a non-negative integer timestamp.")
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,volume_units,filled_volume_units,broker_order_id FROM orders WHERE client_order_id=?",
                (order_id,),
            ).fetchone()
            if row is None or row["status"] not in {SUBMITTED, PARTIALLY_FILLED}:
                db.execute("ROLLBACK")
                return False
            if row["broker_order_id"] and row["broker_order_id"] != broker_id:
                db.execute("ROLLBACK")
                raise ValueError("Broker order ID cannot be changed once recorded.")
            if filled_volume_units < row["filled_volume_units"] or filled_volume_units > row["volume_units"]:
                db.execute("ROLLBACK")
                raise ValueError("Cumulative filled volume is decreasing or exceeds requested volume.")
            status = FILLED if filled_volume_units == row["volume_units"] else PARTIALLY_FILLED
            position_id = (
                self._text(broker_position_id, "broker_position_id")
                if broker_position_id is not None else None
            )
            db.execute(
                """UPDATE orders SET status=?,filled_volume_units=?,updated_at=?,
                   broker_order_id=COALESCE(broker_order_id,?),
                   broker_position_id=COALESCE(broker_position_id,?)
                   WHERE client_order_id=?""",
                (status, filled_volume_units, now, broker_id, position_id, order_id),
            )
            db.execute("COMMIT")
            return True

    def resolve_unknown_partial_fill(
        self,
        client_order_id: str,
        filled_volume_units: int,
        broker_order_id: str,
        *,
        now: int,
        broker_position_id: str | None = None,
    ) -> bool:
        """Resolve an uncertain send from an authenticated cumulative fill event."""
        order_id = self._text(client_order_id, "client_order_id")
        broker_id = self._text(broker_order_id, "broker_order_id")
        if isinstance(filled_volume_units, bool) or not isinstance(filled_volume_units, int) or filled_volume_units <= 0:
            raise ValueError("filled_volume_units must be a positive integer.")
        if isinstance(now, bool) or not isinstance(now, int) or now < 0:
            raise ValueError("now must be a non-negative integer timestamp.")
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,volume_units,broker_order_id FROM orders WHERE client_order_id=?",
                (order_id,),
            ).fetchone()
            if row is None or row["status"] != UNKNOWN:
                db.execute("ROLLBACK")
                return False
            if row["broker_order_id"] and row["broker_order_id"] != broker_id:
                db.execute("ROLLBACK")
                raise ValueError("Broker order ID cannot be changed once recorded.")
            if filled_volume_units > row["volume_units"]:
                db.execute("ROLLBACK")
                raise ValueError("Recovered fill exceeds the requested volume.")
            status = FILLED if filled_volume_units == row["volume_units"] else PARTIALLY_FILLED
            position_id = (
                self._text(broker_position_id, "broker_position_id")
                if broker_position_id is not None else None
            )
            db.execute(
                """UPDATE orders SET status=?,filled_volume_units=?,updated_at=?,
                   broker_order_id=COALESCE(broker_order_id,?),
                   broker_position_id=COALESCE(broker_position_id,?),last_error=NULL
                   WHERE client_order_id=?""",
                (status, filled_volume_units, now, broker_id, position_id, order_id),
            )
            db.execute("COMMIT")
            return True

    def mark_unknown(self, client_order_id: str, *, now: int, error: str | None = None) -> bool:
        """Record timeout/uncertain outcome. The ID remains permanently reserved."""
        return self._transition(
            client_order_id, allowed_from=frozenset({PENDING, SUBMITTED, PARTIALLY_FILLED}),
            status=UNKNOWN, now=now, error=error,
        )

    def mark_terminal(
        self,
        client_order_id: str,
        status: str,
        *,
        now: int,
        error: str | None = None,
        broker_order_id: str | None = None,
    ) -> bool:
        """Record a definitive terminal broker event; UNKNOWN requires reconciliation."""
        if status not in TERMINAL_STATES:
            raise ValueError("status must be FILLED, REJECTED, or CANCELLED.")
        return self._transition(
            client_order_id, allowed_from=frozenset({PENDING, SUBMITTED, PARTIALLY_FILLED}),
            status=status, now=now, broker_order_id=broker_order_id, error=error,
        )

    def resolve_unknown(
        self,
        client_order_id: str,
        broker_status: str,
        *,
        now: int,
        broker_order_id: str | None = None,
    ) -> bool:
        """Resolve UNKNOWN only from an explicit broker-reconciliation result."""
        if broker_status not in RECONCILED_STATES:
            raise ValueError("UNKNOWN can only be resolved to a broker-confirmed state.")
        return self._transition(
            client_order_id, allowed_from=frozenset({UNKNOWN}),
            status=broker_status, now=now, broker_order_id=broker_order_id,
        )

    def get(self, client_order_id: str) -> OrderRecord | None:
        order_id = self._text(client_order_id, "client_order_id")
        with closing(self._connect()) as db, db:
            return self._record(db.execute(
                "SELECT * FROM orders WHERE client_order_id=?", (order_id,)
            ).fetchone())

    def list_in_flight(self) -> list[OrderRecord]:
        """Return PENDING, SUBMITTED and UNKNOWN records for recovery/reconciliation."""
        with closing(self._connect()) as db, db:
            rows = db.execute(
                """SELECT * FROM orders WHERE status IN ('PENDING','SUBMITTED','PARTIALLY_FILLED','UNKNOWN')
                   ORDER BY created_at,client_order_id"""
            ).fetchall()
            return [self._record(row) for row in rows if row is not None]


    def get_by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None:
        """Find a locally reserved order by its broker-assigned order ID."""
        broker_id = self._text(broker_order_id, "broker_order_id")
        with closing(self._connect()) as db, db:
            return self._record(db.execute(
                "SELECT * FROM orders WHERE broker_order_id=?", (broker_id,)
            ).fetchone())
