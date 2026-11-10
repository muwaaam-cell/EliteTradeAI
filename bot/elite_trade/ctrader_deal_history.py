"""Account-scoped, duplicate-safe cTrader deal-history persistence.

Raw broker monetary values are retained with their moneyDigits metadata.
This module deliberately does not guess net P&L or commission conversions.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Mapping


class DealHistoryError(RuntimeError):
    """Raised when broker deal-history data is invalid or incomplete."""


def _value(message: object, name: str, default=None):
    try:
        return getattr(message, name)
    except (AttributeError, TypeError):
        return default


def _present(message: object, name: str) -> bool:
    has_field = getattr(message, "HasField", None)
    if callable(has_field):
        try:
            return bool(has_field(name))
        except (ValueError, TypeError):
            pass
    return _value(message, name) is not None


def _plain(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "ListFields"):
        return message_to_dict(value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def message_to_dict(message: object) -> dict:
    """Convert populated protobuf fields into JSON-safe values."""
    result = {}
    list_fields = getattr(message, "ListFields", None)

    if callable(list_fields):
        for descriptor, value in list_fields():
            name = getattr(descriptor, "name", None)
            if name:
                result[name] = _plain(value)
        return result

    descriptor = getattr(message, "DESCRIPTOR", None)
    fields = getattr(descriptor, "fields", ())
    for field in fields:
        name = getattr(field, "name", None)
        if name and _present(message, name):
            result[name] = _plain(_value(message, name))
    return result


def deal_to_record(deal: object) -> dict:
    """Extract identifiers and raw fields without inventing P&L semantics."""
    deal_id = _value(deal, "dealId")
    if deal_id is None or int(deal_id) <= 0:
        raise DealHistoryError("Broker deal is missing a valid dealId.")

    record = message_to_dict(deal)
    close_detail = _value(deal, "closePositionDetail")

    return {
        "deal_id": str(int(deal_id)),
        "order_id": str(_value(deal, "orderId", "") or ""),
        "position_id": str(_value(deal, "positionId", "") or ""),
        "symbol_id": int(_value(deal, "symbolId", 0) or 0),
        "execution_timestamp": int(
            _value(deal, "executionTimestamp", 0) or 0
        ),
        "create_timestamp": int(
            _value(deal, "createTimestamp", 0) or 0
        ),
        "trade_side": str(_value(deal, "tradeSide", "")),
        "deal_status": str(_value(deal, "dealStatus", "")),
        "volume": str(_value(deal, "volume", "") or ""),
        "filled_volume": str(_value(deal, "filledVolume", "") or ""),
        "execution_price": (
            float(_value(deal, "executionPrice"))
            if _present(deal, "executionPrice") else None
        ),
        "money_digits": int(_value(deal, "moneyDigits", 0) or 0),
        "commission_raw": str(_value(deal, "commission", "") or ""),
        "close_detail": (
            message_to_dict(close_detail)
            if close_detail is not None else None
        ),
        "raw_json": json.dumps(record, sort_keys=True, separators=(",", ":")),
    }


def normalize_close_position_cash(close_detail: Mapping[str, object]):
    """Normalize cTrader close cash components using the close-detail moneyDigits.

    Values are signed broker minor-unit integers in the account deposit
    currency. The outer deal commission is intentionally not added again:
    the close detail's commission and pnlConversionFee are the components
    used here. Missing required monetary fields or scale metadata are rejected.
    """
    from dataclasses import dataclass
    from decimal import Decimal

    @dataclass(frozen=True)
    class ClosePositionCashOutcome:
        money_digits: int
        gross_profit: Decimal
        swap: Decimal
        commission: Decimal
        pnl_conversion_fee: Decimal
        net_pnl: Decimal

    if not isinstance(close_detail, Mapping):
        raise DealHistoryError("A broker closePositionDetail mapping is required.")
    digits = close_detail.get("moneyDigits")
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 18:
        raise DealHistoryError("Close-detail moneyDigits is missing or invalid.")
    scale = Decimal(10) ** digits

    def amount(field: str, *, optional: bool = False) -> Decimal:
        raw = close_detail.get(field)
        if raw is None and optional:
            raw = 0
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise DealHistoryError(f"Close-detail {field} must be a signed integer.")
        return Decimal(raw) / scale

    # balance is a broker-required close-detail field; validate its presence,
    # but do not mistake an account balance for a trade P&L component.
    amount("balance")
    gross = amount("grossProfit")
    swap = amount("swap")
    commission = amount("commission")
    conversion_fee = amount("pnlConversionFee", optional=True)
    return ClosePositionCashOutcome(
        money_digits=digits,
        gross_profit=gross,
        swap=swap,
        commission=commission,
        pnl_conversion_fee=conversion_fee,
        net_pnl=gross + swap + commission + conversion_fee,
    )


class CTraderDealHistoryStore:
    """SQLite deal store with account/deal uniqueness and safe page tracking."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            self.path, timeout=20, check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self):
        with self._lock, self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS ctrader_deals (
                    account_id TEXT NOT NULL,
                    deal_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    position_id TEXT NOT NULL,
                    symbol_id INTEGER NOT NULL,
                    execution_timestamp INTEGER NOT NULL,
                    create_timestamp INTEGER NOT NULL,
                    trade_side TEXT NOT NULL,
                    deal_status TEXT NOT NULL,
                    volume TEXT NOT NULL,
                    filled_volume TEXT NOT NULL,
                    execution_price REAL,
                    money_digits INTEGER NOT NULL,
                    commission_raw TEXT NOT NULL,
                    close_detail_json TEXT,
                    raw_json TEXT NOT NULL,
                    first_seen_epoch REAL NOT NULL,
                    PRIMARY KEY (account_id, deal_id)
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_ctrader_deals_account_time
                ON ctrader_deals(account_id, execution_timestamp)
            """)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS ctrader_deal_history_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    requested_from_ms INTEGER NOT NULL,
                    requested_to_ms INTEGER NOT NULL,
                    received_count INTEGER NOT NULL DEFAULT 0,
                    stored_count INTEGER NOT NULL DEFAULT 0,
                    has_more INTEGER NOT NULL,
                    complete INTEGER NOT NULL DEFAULT 0,
                    received_epoch REAL NOT NULL
                )
            """)

    def ingest_page(
        self,
        *,
        account_id: int,
        response: object,
        requested_from_ms: int,
        requested_to_ms: int,
    ) -> dict:
        """Persist one authenticated account's response page atomically."""
        account = int(account_id)
        if account <= 0:
            raise DealHistoryError("A positive authenticated account ID is required.")

        response_account = _value(response, "ctidTraderAccountId")
        if response_account is None or int(response_account) != account:
            raise DealHistoryError(
                "Deal-history response account does not match the selected account."
            )

        start = int(requested_from_ms)
        end = int(requested_to_ms)
        if start < 0 or end <= start:
            raise DealHistoryError("Invalid deal-history time range.")

        deals = tuple(_value(response, "deal", ()) or ())
        has_more = bool(_value(response, "hasMore", False))
        stored = 0
        now = time.time()

        with self._lock, self._db:
            for deal in deals:
                item = deal_to_record(deal)
                detail = item["close_detail"]
                cursor = self._db.execute("""
                    INSERT OR IGNORE INTO ctrader_deals (
                        account_id, deal_id, order_id, position_id,
                        symbol_id, execution_timestamp, create_timestamp,
                        trade_side, deal_status, volume, filled_volume,
                        execution_price, money_digits, commission_raw,
                        close_detail_json, raw_json, first_seen_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(account), item["deal_id"], item["order_id"],
                    item["position_id"], item["symbol_id"],
                    item["execution_timestamp"], item["create_timestamp"],
                    item["trade_side"], item["deal_status"], item["volume"],
                    item["filled_volume"], item["execution_price"],
                    item["money_digits"], item["commission_raw"],
                    json.dumps(detail, sort_keys=True) if detail is not None else None,
                    item["raw_json"], now,
                ))
                stored += max(cursor.rowcount, 0)

            self._db.execute("""
                INSERT INTO ctrader_deal_history_runs (
                    account_id, requested_from_ms, requested_to_ms,
                    received_count, stored_count, has_more, complete,
                    received_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(account), start, end, len(deals), stored,
                int(has_more), int(not has_more), now,
            ))

        return {
            "account_id": account,
            "received": len(deals),
            "newly_stored": stored,
            "duplicates_ignored": len(deals) - stored,
            "has_more": has_more,
            "page_complete": not has_more,
        }

    def deals(
        self,
        *,
        account_id: int,
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> tuple[dict, ...]:
        account = int(account_id)
        if account <= 0:
            raise DealHistoryError("A positive account ID is required.")

        sql = """
            SELECT * FROM ctrader_deals
            WHERE account_id = ?
        """
        params: list[object] = [str(account)]

        if from_ms is not None:
            sql += " AND execution_timestamp >= ?"
            params.append(int(from_ms))
        if to_ms is not None:
            sql += " AND execution_timestamp <= ?"
            params.append(int(to_ms))

        sql += " ORDER BY execution_timestamp, deal_id"

        with self._lock:
            rows = self._db.execute(sql, params).fetchall()

        return tuple(dict(row) for row in rows)

    def close(self):
        with self._lock:
            self._db.close()


def build_deal_list_request(
    *,
    sdk: Mapping[str, object],
    account_id: int,
    from_timestamp_ms: int,
    to_timestamp_ms: int,
    max_rows: int = 1000,
):
    """Build one cTrader deal-list request; caller owns response pagination."""
    account = int(account_id)
    start = int(from_timestamp_ms)
    end = int(to_timestamp_ms)
    rows = int(max_rows)

    if account <= 0:
        raise DealHistoryError("A positive authenticated account ID is required.")
    if start < 0 or end <= start:
        raise DealHistoryError("Invalid deal-history time range.")
    if rows <= 0:
        raise DealHistoryError("max_rows must be positive.")

    request_type = sdk.get("ProtoOADealListReq")
    if request_type is None:
        raise DealHistoryError("cTrader SDK has no ProtoOADealListReq registered.")

    request = request_type()
    request.ctidTraderAccountId = account
    request.fromTimestamp = start
    request.toTimestamp = end
    request.maxRows = rows
    return request
