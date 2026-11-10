"""Conservative bot-owned position protection management for cTrader demo."""
from __future__ import annotations

import sqlite3
from math import isfinite
import threading
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable

from elite_trade.ctrader_order_requests import (
    PositionCloseSpec,
    PositionProtectionSpec,
    build_close_position_request,
    build_position_protection_request,
)
from elite_trade.position_ledger import PositionLedger, PositionStatus


class PositionManagementError(RuntimeError):
    """Raised when broker state is insufficient for safe position management."""


class CTraderPositionManager:
    """Move existing bot-owned stops to break-even/trailing levels.

    Routine management only tightens stops; it never closes or reverses
    positions. An explicit close-all command has a separate idempotent method.
    Both paths require a matching local order-ledger entry and the XAUUSD/EURUSD
    allowlist, so manual positions are excluded. Fresh broker state is mandatory.
    """

    def __init__(
        self, *,
        transport: object,
        position_ledger: PositionLedger,
        order_ledger: object,
        instruments: Callable[[], Iterable[object]],
        quote_provider: Callable[[str], object | None],
        state_path: str | Path,
        now_epoch: Callable[[], float],
        on_issue: Callable[[str], None],
        max_snapshot_age_seconds: float = 5.0,
        max_quote_age_seconds: float = 3.0,
    ):
        if getattr(transport, "environment", None) != "demo":
            raise ValueError("Position manager is restricted to cTrader demo.")
        if not isinstance(position_ledger, PositionLedger):
            raise TypeError("A durable PositionLedger is required.")
        if position_ledger.bound_account_id is None:
            raise ValueError("Position ledger must be bound to the broker account.")
        if getattr(order_ledger, "bound_account_id", None) != position_ledger.bound_account_id:
            raise ValueError("Order ledger must be bound to the same broker account.")
        for name, callback in (
            ("instruments", instruments), ("quote_provider", quote_provider),
            ("now_epoch", now_epoch), ("on_issue", on_issue),
        ):
            if not callable(callback):
                raise TypeError(f"{name} must be callable.")
        self.transport = transport
        self.position_ledger = position_ledger
        self.order_ledger = order_ledger
        self.instruments = instruments
        self.quote_provider = quote_provider
        self.now_epoch = now_epoch
        self.on_issue = on_issue
        self.max_snapshot_age_seconds = float(max_snapshot_age_seconds)
        self.max_quote_age_seconds = float(max_quote_age_seconds)
        if self.max_snapshot_age_seconds <= 0 or self.max_quote_age_seconds <= 0:
            raise ValueError("Freshness limits must be positive.")
        self.account_id = position_ledger.bound_account_id
        self._lock = threading.RLock()
        self._db_path = str(state_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._db_path, timeout=20, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        with self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS position_close_requests (
                    account_id TEXT NOT NULL,
                    position_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    requested_epoch REAL NOT NULL,
                    PRIMARY KEY (account_id, position_id, request_id)
                )
            """)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS position_management_state (
                    account_id TEXT NOT NULL,
                    position_id TEXT NOT NULL,
                    initial_risk_distance TEXT NOT NULL,
                    pending_stop TEXT,
                    pending_since REAL,
                    PRIMARY KEY (account_id, position_id)
                )
            """)

    def _state(self, position_id: str, initial_risk: Decimal) -> tuple[Decimal, Decimal | None]:
        with self._lock, self._db:
            row = self._db.execute(
                """SELECT initial_risk_distance,pending_stop
                   FROM position_management_state
                   WHERE account_id=? AND position_id=?""",
                (str(self.account_id), position_id),
            ).fetchone()
            if row is None:
                self._db.execute(
                    """INSERT INTO position_management_state
                       (account_id,position_id,initial_risk_distance)
                       VALUES (?,?,?)""",
                    (str(self.account_id), position_id, str(initial_risk)),
                )
                return initial_risk, None
            return Decimal(row["initial_risk_distance"]), (
                Decimal(row["pending_stop"]) if row["pending_stop"] is not None else None
            )

    def _close_pending(self, position_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                """SELECT 1 FROM position_close_requests
                   WHERE account_id=? AND position_id=?""",
                (str(self.account_id), position_id),
            ).fetchone()
        return row is not None

    def _set_pending(self, position_id: str, stop: Decimal) -> None:
        with self._lock, self._db:
            self._db.execute(
                """UPDATE position_management_state
                   SET pending_stop=?,pending_since=?
                   WHERE account_id=? AND position_id=?""",
                (str(stop), float(self.now_epoch()), str(self.account_id), position_id),
            )

    def _clear_pending(self, position_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                """UPDATE position_management_state
                   SET pending_stop=NULL,pending_since=NULL
                   WHERE account_id=? AND position_id=?""",
                (str(self.account_id), position_id),
            )

    def _instrument(self, symbol: str):
        matches = [
            item for item in self.instruments()
            if getattr(item, "symbol", "").upper() == symbol.upper()
            and getattr(item, "is_tradable", False) is True
        ]
        if len(matches) != 1:
            raise PositionManagementError("Broker instrument metadata is missing or ambiguous.")
        return matches[0]

    def manage_positions(self) -> int:
        if (
            getattr(self.transport, "authenticated_account_id", None) != self.account_id
            or getattr(getattr(self.transport, "state", None), "value", getattr(self.transport, "state", None)) != "authenticated"
        ):
            raise PositionManagementError("Authenticated demo account is unavailable.")
        now = float(self.now_epoch())
        if not isfinite(now) or now < 0:
            raise PositionManagementError("Local position-management clock is invalid.")
        amended = 0
        for position in self.position_ledger.list_open():
            if position.status is not PositionStatus.OPEN:
                continue
            # Explicit symbol allowlist and local order ownership protect manual positions.
            if position.symbol.upper() not in {"XAUUSD", "EURUSD"}:
                continue
            records = self.order_ledger.list_by_broker_position_id(
                position.broker_position_id
            )
            owned = any(
                getattr(item, "symbol", "").upper() == position.symbol.upper()
                and getattr(item, "strategy_mode", None) in {"SCALPING", "INTRADAY"}
                and getattr(item, "initial_cash_risk", None) is not None
                for item in records
            )
            if not owned or self._close_pending(position.broker_position_id):
                continue
            if (
                position.last_snapshot_epoch is None
                or now < position.last_snapshot_epoch
                or now - position.last_snapshot_epoch > self.max_snapshot_age_seconds
            ):
                reconcile = getattr(self.transport, "request_positions_reconcile", None)
                if callable(reconcile) and not getattr(
                    self.transport, "position_reconciliation_in_flight", False
                ):
                    try:
                        reconcile()
                    except Exception as exc:
                        self.on_issue(
                            "Stale position snapshot; reconciliation request failed: "
                            + " ".join(str(exc).split())[:160]
                        )
                raise PositionManagementError(
                    f"Position {position.broker_position_id} has no fresh broker reconciliation."
                )
            if position.broker_stop_loss is None or position.broker_take_profit is None:
                raise PositionManagementError(
                    f"Bot position {position.broker_position_id} lacks broker-confirmed SL/TP."
                )
            instrument = self._instrument(position.symbol)
            market = self.quote_provider(position.symbol)
            quote = getattr(market, "quote", None)
            if quote is None:
                raise PositionManagementError("Fresh executable quote is unavailable.")
            age = now - float(quote.received_at_epoch)
            if not isfinite(age) or age < 0 or age > self.max_quote_age_seconds:
                raise PositionManagementError("Position-management quote is stale.")

            initial_risk = abs(position.entry_price - position.broker_stop_loss)
            if initial_risk <= 0:
                raise PositionManagementError("Initial stop distance cannot be recovered.")
            risk_distance, pending = self._state(
                position.broker_position_id, initial_risk
            )
            if pending is not None:
                if position.broker_stop_loss == pending:
                    self._clear_pending(position.broker_position_id)
                else:
                    # A send is never repeated while broker confirmation is uncertain.
                    request_reconcile = getattr(self.transport, "request_positions_reconcile", None)
                    if callable(request_reconcile):
                        try:
                            if not getattr(self.transport, "position_reconciliation_in_flight", False):
                                request_reconcile()
                        except Exception as exc:
                            self.on_issue(
                                "Position reconciliation request failed: "
                                + " ".join(str(exc).split())[:160]
                            )
                    self.on_issue(
                        f"Position {position.broker_position_id} has an unconfirmed stop amendment; awaiting reconciliation."
                    )
                    continue

            if position.side == "BUY":
                current_price = Decimal(str(quote.bid))
                profit_distance = current_price - position.entry_price
            elif position.side == "SELL":
                current_price = Decimal(str(quote.ask))
                profit_distance = position.entry_price - current_price
            else:
                raise PositionManagementError("Broker position side is invalid.")
            r_multiple = profit_distance / risk_distance
            if r_multiple < Decimal("1"):
                continue

            if r_multiple < Decimal("2"):
                desired = position.entry_price
            elif position.side == "BUY":
                desired = max(position.entry_price, current_price - risk_distance)
            else:
                desired = min(position.entry_price, current_price + risk_distance)
            desired = instrument.round_price(desired)
            existing = position.broker_stop_loss
            tighter = desired > existing if position.side == "BUY" else desired < existing
            if not tighter:
                continue

            minimum_distance = (
                Decimal(getattr(instrument, "sl_distance", 0))
                * Decimal(instrument.price_precision_quantum)
            )
            distance_to_market = (
                current_price - desired if position.side == "BUY"
                else desired - current_price
            )
            if distance_to_market < minimum_distance or distance_to_market <= 0:
                continue

            raw_position_id = int(position.broker_position_id)
            request = build_position_protection_request(
                PositionProtectionSpec(
                    account_id=self.account_id,
                    position_id=raw_position_id,
                    stop_loss=float(desired),
                    take_profit=float(position.broker_take_profit),
                )
            )
            self._set_pending(position.broker_position_id, desired)
            try:
                self.transport.send_demo_position_protection_request(request)
                amended += 1
                reconcile = getattr(self.transport, "request_positions_reconcile", None)
                if callable(reconcile):
                    reconcile()
            except Exception as exc:
                self.on_issue(
                    f"Position {position.broker_position_id} stop amendment uncertain: "
                    + " ".join(str(exc).split())[:180]
                )
                reconcile = getattr(self.transport, "request_positions_reconcile", None)
                if callable(reconcile):
                    try:
                        reconcile()
                    except Exception as reconcile_exc:
                        self.on_issue(
                            "Follow-up position reconciliation failed: "
                            + " ".join(str(reconcile_exc).split())[:160]
                        )
        return amended

    def close_all_bot_positions(self, *, request_id: str | None = None) -> int:
        """Send one close request for each fresh, bot-owned allowlisted position.

        Requests are durably reserved before send and never automatically
        repeated after uncertainty. Manual/unowned positions are untouched.
        """
        if (
            getattr(self.transport, "authenticated_account_id", None) != self.account_id
            or getattr(getattr(self.transport, "state", None), "value", getattr(self.transport, "state", None)) != "authenticated"
        ):
            raise PositionManagementError("Authenticated demo account is unavailable.")
        now = float(self.now_epoch())
        if not isfinite(now) or now < 0:
            raise PositionManagementError("Local close-all clock is invalid.")
        request_token = (
            str(request_id).strip() if request_id is not None
            else f"local-{int(now * 1000000)}"
        )
        if not request_token or len(request_token) > 128:
            raise PositionManagementError("Close-all request ID is invalid.")
        sent = 0
        for position in self.position_ledger.list_open():
            if position.symbol.upper() not in {"XAUUSD", "EURUSD"}:
                continue
            records = self.order_ledger.list_by_broker_position_id(
                position.broker_position_id
            )
            if not any(
                getattr(item, "symbol", "").upper() == position.symbol.upper()
                and getattr(item, "strategy_mode", None) in {"SCALPING", "INTRADAY"}
                for item in records
            ):
                continue
            if (
                position.last_snapshot_epoch is None
                or now < position.last_snapshot_epoch
                or now - position.last_snapshot_epoch > self.max_snapshot_age_seconds
            ):
                raise PositionManagementError(
                    f"Close-all blocked: position {position.broker_position_id} has stale broker state."
                )
            volume_cents = position.volume_units * Decimal("100")
            if volume_cents <= 0 or volume_cents != volume_cents.to_integral_value():
                raise PositionManagementError(
                    f"Position {position.broker_position_id} volume cannot be represented exactly."
                )
            position_id = int(position.broker_position_id)
            request = build_close_position_request(PositionCloseSpec(
                account_id=self.account_id,
                position_id=position_id,
                volume_units=int(volume_cents),
            ))
            with self._lock, self._db:
                cursor = self._db.execute(
                    """INSERT OR IGNORE INTO position_close_requests
                       (account_id,position_id,request_id,requested_epoch)
                       VALUES (?,?,?,?)""",
                    (str(self.account_id), position.broker_position_id, request_token, now),
                )
                if cursor.rowcount != 1:
                    continue
            try:
                self.transport.send_demo_close_position_request(request)
                sent += 1
                reconcile = getattr(self.transport, "request_positions_reconcile", None)
                if callable(reconcile):
                    reconcile()
            except Exception as exc:
                self.on_issue(
                    f"Close request for bot position {position.broker_position_id} is uncertain: "
                    + " ".join(str(exc).split())[:180]
                )
                reconcile = getattr(self.transport, "request_positions_reconcile", None)
                if callable(reconcile) and not getattr(
                    self.transport, "position_reconciliation_in_flight", False
                ):
                    try:
                        reconcile()
                    except Exception as reconcile_exc:
                        self.on_issue(
                            "Close-request reconciliation failed: "
                            + " ".join(str(reconcile_exc).split())[:160]
                        )
        return sent

    def close(self) -> None:
        with self._lock:
            self._db.close()
