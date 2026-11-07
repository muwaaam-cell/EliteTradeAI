"""Fail-closed CPI event guard and surprise classifier.

This module is deliberately independent of order execution. It consumes a
normalised, trusted calendar event supplied by a future calendar adapter.
It never infers BUY/SELL from CPI: market structure and the normal pre-trade
risk gate must still approve any entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal


Surprise = Literal["HOTTER_THAN_FORECAST", "COOLER_THAN_FORECAST", "IN_LINE", "UNKNOWN"]


@dataclass(frozen=True)
class CPIEvent:
    scheduled_at: datetime
    currency: str = "USD"
    impact: str = "high"
    actual: Decimal | None = None
    forecast: Decimal | None = None
    previous: Decimal | None = None
    source: str = ""

    def __post_init__(self) -> None:
        if self.scheduled_at.tzinfo is None or self.scheduled_at.utcoffset() is None:
            raise ValueError("CPI scheduled_at must be timezone-aware.")
        if self.currency != "USD":
            raise ValueError("Only USD CPI events are supported.")
        if self.impact.lower() != "high":
            raise ValueError("CPI event must be marked high impact.")
        for name in ("actual", "forecast", "previous"):
            value = getattr(self, name)
            if value is not None:
                try:
                    parsed = Decimal(str(value))
                except (InvalidOperation, ValueError):
                    raise ValueError(f"{name} must be a finite decimal.") from None
                if not parsed.is_finite():
                    raise ValueError(f"{name} must be a finite decimal.")
                object.__setattr__(self, name, parsed)


@dataclass(frozen=True)
class CPIEntryDecision:
    allow_new_entry: bool
    reason: str
    surprise: Surprise
    surprise_value: Decimal | None
    minutes_from_release: float


def classify_surprise(
    actual: Decimal | None,
    forecast: Decimal | None,
    *,
    tolerance: Decimal = Decimal("0.05"),
) -> tuple[Surprise, Decimal | None]:
    """Classify the CPI print; never translate the result into a trade side."""
    if tolerance < 0 or not tolerance.is_finite():
        raise ValueError("tolerance must be a finite non-negative decimal.")
    if actual is None or forecast is None:
        return "UNKNOWN", None
    difference = actual - forecast
    if difference > tolerance:
        return "HOTTER_THAN_FORECAST", difference
    if difference < -tolerance:
        return "COOLER_THAN_FORECAST", difference
    return "IN_LINE", difference


def evaluate_cpi_entry(
    event: CPIEvent,
    *,
    now: datetime,
    post_release_confirmation_passed: bool = False,
    blackout_before: timedelta = timedelta(minutes=15),
    immediate_cooldown: timedelta = timedelta(minutes=5),
    confirmation_window: timedelta = timedelta(minutes=15),
) -> CPIEntryDecision:
    """Fail-closed CPI gate for new entries only; existing positions stay managed.

    Blocks from 15 minutes before release through the first 5 minutes after.
    From minute 5 through minute 15, an explicit post-release confirmation is
    required. After minute 15, missing actual CPI data still blocks entries.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    if blackout_before < timedelta(0) or immediate_cooldown < timedelta(0):
        raise ValueError("CPI guard windows cannot be negative.")
    if confirmation_window < immediate_cooldown:
        raise ValueError("confirmation_window cannot be shorter than cooldown.")

    minutes = (now - event.scheduled_at).total_seconds() / 60.0
    surprise, difference = classify_surprise(event.actual, event.forecast)

    if minutes < -blackout_before.total_seconds() / 60.0:
        return CPIEntryDecision(True, "Outside CPI blackout window.", surprise, difference, minutes)
    if minutes < 0:
        return CPIEntryDecision(False, "High-impact US CPI release is approaching; new entries blocked.", surprise, difference, minutes)
    if minutes < immediate_cooldown.total_seconds() / 60.0:
        return CPIEntryDecision(False, "CPI just released; wait at least 5 minutes and let volatility settle.", surprise, difference, minutes)
    if minutes <= confirmation_window.total_seconds() / 60.0:
        if event.actual is None:
            return CPIEntryDecision(False, "CPI actual is not recorded; fail-closed.", surprise, difference, minutes)
        if not post_release_confirmation_passed:
            return CPIEntryDecision(False, "Wait for post-release breakout/retest confirmation; CPI surprise alone is not a trade signal.", surprise, difference, minutes)
        return CPIEntryDecision(True, "CPI data recorded and post-release confirmation supplied; normal risk checks still required.", surprise, difference, minutes)
    if event.actual is None:
        return CPIEntryDecision(False, "CPI actual remains missing after release; fail-closed.", surprise, difference, minutes)
    return CPIEntryDecision(True, "CPI blackout and confirmation window passed; normal strategy and risk checks still required.", surprise, difference, minutes)
