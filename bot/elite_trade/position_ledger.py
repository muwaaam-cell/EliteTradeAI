"""Durable, fail-closed broker position ledger.

Local SL/TP intent is stored separately from broker-confirmed SL/TP. Only a
fresh, explicitly complete broker snapshot may update confirmed fields.
Positions omitted from a complete snapshot become UNKNOWN, never CLOSED.
"""
from __future__ import annotations

import math
import sqlite3
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BrokerPositionSnapshot:
    broker_position_id: str
    symbol: str
    side: str
    volume_units: Decimal
    entry_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    is_open: bool


@dataclass(frozen=True)
class PositionRecord:
    broker_position_id: str
    symbol: str
    side: str
    volume_units: Decimal
    entry_price: Decimal
    status: PositionStatus
    local_stop_loss: Decimal | None
    local_take_profit: Decimal | None
    broker_stop_loss: Decimal | None
    broker_take_profit: Decimal | None
    updated_at_epoch: float
    last_snapshot_epoch: float | None


def _text(value: object, field: str, limit: int = 128) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{field} must be a non-empty string of at most {limit} characters.")
    return value.strip()


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite decimal.")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal.") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{field} must be finite" + (" and positive." if positive else "."))
    return result


def _optional_price(value: object, field: str) -> Decimal | None:
    return None if value is None else _decimal(value, field, positive=True)


def _timestamp(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite non-negative timestamp.")
    return float(value)


class PositionLedger:
    """SQLite WAL position state with explicit full-snapshot reconciliation."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS positions (
                    broker_position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    volume_units TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED','UNKNOWN')),
                    local_stop_loss TEXT,
                    local_take_profit TEXT,
                    broker_stop_loss TEXT,
                    broker_take_profit TEXT,
                    updated_at_epoch REAL NOT NULL,
                    last_snapshot_epoch REAL
                )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")
        if self.database != ":memory:":
            try:
                Path(self.database).chmod(0o600)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        if self.database != ":memory:":
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _record(row: sqlite3.Row | None) -> PositionRecord | None:
        if row is None:
            return None
        dec = lambda value: Decimal(value) if value is not None else None
        return PositionRecord(
            broker_position_id=row["broker_position_id"],
            symbol=row["symbol"],
            side=row["side"],
            volume_units=Decimal(row["volume_units"]),
            entry_price=Decimal(row["entry_price"]),
            status=PositionStatus(row["status"]),
            local_stop_loss=dec(row["local_stop_loss"]),
            local_take_profit=dec(row["local_take_profit"]),
            broker_stop_loss=dec(row["broker_stop_loss"]),
            broker_take_profit=dec(row["broker_take_profit"]),
            updated_at_epoch=row["updated_at_epoch"],
            last_snapshot_epoch=row["last_snapshot_epoch"],
        )

    def record_local_intent(
        self,
        broker_position_id: str,
        symbol: str,
        side: str,
        volume_units: Decimal,
        entry_price: Decimal,
        *,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        now_epoch: float,
    ) -> None:
        """Store local intent only; never promote it to broker-confirmed OPEN."""
        position_id = _text(broker_position_id, "broker_position_id")
        instrument = _text(symbol, "symbol", 64)
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL.")
        volume = _decimal(volume_units, "volume_units", positive=True)
        entry = _decimal(entry_price, "entry_price", positive=True)
        sl = _optional_price(stop_loss, "stop_loss")
        tp = _optional_price(take_profit, "take_profit")
        now = _timestamp(now_epoch, "now_epoch")

        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM positions WHERE broker_position_id=?", (position_id,)
            ).fetchone()
            if row is not None and row["status"] == PositionStatus.CLOSED.value:
                db.execute("ROLLBACK")
                raise ValueError("A broker-confirmed closed position cannot be reopened by local intent.")
            if row is None:
                db.execute(
                    """INSERT INTO positions (
                        broker_position_id,symbol,side,volume_units,entry_price,status,
                        local_stop_loss,local_take_profit,broker_stop_loss,
                        broker_take_profit,updated_at_epoch,last_snapshot_epoch
                    ) VALUES (?,?,?,?,?,'UNKNOWN',?,?,NULL,NULL,?,NULL)""",
                    (position_id, instrument, side, str(volume), str(entry), 
                     str(sl) if sl is not None else None,
                     str(tp) if tp is not None else None, now),
                )
            else:
                db.execute(
                    """UPDATE positions SET symbol=?,side=?,volume_units=?,entry_price=?,
                       local_stop_loss=?,local_take_profit=?,updated_at_epoch=?
                       WHERE broker_position_id=?""",
                    (instrument, side, str(volume), str(entry),
                     str(sl) if sl is not None else None,
                     str(tp) if tp is not None else None, now, position_id),
                )
            db.execute("COMMIT")

    @staticmethod
    def _validate_snapshot(snapshots: tuple[BrokerPositionSnapshot, ...]) -> None:
        seen: set[str] = set()
        for snap in snapshots:
            position_id = _text(snap.broker_position_id, "broker_position_id")
            if position_id in seen:
                raise ValueError("Duplicate broker position ID in snapshot.")
            seen.add(position_id)
            _text(snap.symbol, "symbol", 64)
            if snap.side not in {"BUY", "SELL"}:
                raise ValueError("Snapshot side must be BUY or SELL.")
            _decimal(snap.volume_units, "volume_units", positive=True)
            _decimal(snap.entry_price, "entry_price", positive=True)
            _optional_price(snap.stop_loss, "stop_loss")
            _optional_price(snap.take_profit, "take_profit")
            if not isinstance(snap.is_open, bool):
                raise ValueError("Snapshot is_open must be boolean.")

    def reconcile(
        self,
        snapshots: list[BrokerPositionSnapshot] | tuple[BrokerPositionSnapshot, ...],
        *,
        complete: bool,
        observed_at_epoch: float,
        now_epoch: float,
        max_age_seconds: float = 5.0,
    ) -> None:
        """Apply an all-or-nothing complete snapshot from the authenticated broker.

        An empty list is valid only when complete=True and represents zero
        currently open broker positions. Previously OPEN local rows omitted
        from it become UNKNOWN pending per-position/history confirmation.
        """
        if complete is not True:
            raise ValueError("Partial broker snapshots cannot be reconciled.")
        observed = _timestamp(observed_at_epoch, "observed_at_epoch")
        now = _timestamp(now_epoch, "now_epoch")
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be finite and positive.")
        if observed > now or now - observed > max_age_seconds:
            raise ValueError("Broker position snapshot is stale or from the future.")
        if not isinstance(snapshots, (list, tuple)):
            raise ValueError("snapshots must be a list or tuple.")
        batch = tuple(snapshots)
        self._validate_snapshot(batch)

        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT MAX(last_snapshot_epoch) FROM positions").fetchone()[0]
            if previous is not None and observed < previous:
                db.execute("ROLLBACK")
                raise ValueError("Snapshot is older than the last applied broker snapshot.")
            # Missing rows are ambiguous: clear stale confirmed stops and mark UNKNOWN.
            db.execute(
                """UPDATE positions SET status='UNKNOWN',broker_stop_loss=NULL,
                   broker_take_profit=NULL,updated_at_epoch=?
                   WHERE status='OPEN'""",
                (now,),
            )
            for snap in batch:
                position_id = _text(snap.broker_position_id, "broker_position_id")
                symbol = _text(snap.symbol, "symbol", 64)
                volume = _decimal(snap.volume_units, "volume_units", positive=True)
                entry = _decimal(snap.entry_price, "entry_price", positive=True)
                sl = _optional_price(snap.stop_loss, "stop_loss")
                tp = _optional_price(snap.take_profit, "take_profit")
                status = PositionStatus.OPEN.value if snap.is_open else PositionStatus.CLOSED.value
                db.execute(
                    """INSERT INTO positions (
                        broker_position_id,symbol,side,volume_units,entry_price,status,
                        local_stop_loss,local_take_profit,broker_stop_loss,
                        broker_take_profit,updated_at_epoch,last_snapshot_epoch
                    ) VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?,?)
                    ON CONFLICT(broker_position_id) DO UPDATE SET
                        symbol=excluded.symbol,side=excluded.side,
                        volume_units=excluded.volume_units,entry_price=excluded.entry_price,
                        status=excluded.status,broker_stop_loss=excluded.broker_stop_loss,
                        broker_take_profit=excluded.broker_take_profit,
                        updated_at_epoch=excluded.updated_at_epoch,
                        last_snapshot_epoch=excluded.last_snapshot_epoch""",
                    (position_id, symbol, snap.side, str(volume), str(entry), status,
                     str(sl) if sl is not None else None,
                     str(tp) if tp is not None else None, now, observed),
                )
            # Persist the snapshot watermark even when the complete snapshot is empty.
            db.execute(
                """CREATE TABLE IF NOT EXISTS position_reconcile_meta (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    last_snapshot_epoch REAL NOT NULL
                )"""
            )
            db.execute(
                """INSERT INTO position_reconcile_meta(singleton,last_snapshot_epoch)
                   VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET
                   last_snapshot_epoch=excluded.last_snapshot_epoch""",
                (observed,),
            )
            db.execute("COMMIT")

    def get(self, broker_position_id: str) -> PositionRecord | None:
        position_id = _text(broker_position_id, "broker_position_id")
        with self._connect() as db:
            return self._record(db.execute(
                "SELECT * FROM positions WHERE broker_position_id=?", (position_id,)
            ).fetchone())

    def list_open(self) -> list[PositionRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM positions WHERE status='OPEN' ORDER BY broker_position_id"
            ).fetchall()
            return [self._record(row) for row in rows]

    def list_unknown(self) -> list[PositionRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM positions WHERE status='UNKNOWN' ORDER BY broker_position_id"
            ).fetchall()
            return [self._record(row) for row in rows]
