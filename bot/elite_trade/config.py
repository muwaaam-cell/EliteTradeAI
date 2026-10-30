"""Configuration and fail-closed execution permissions."""
from dataclasses import dataclass
import os


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    ctrader_client_id: str
    ctrader_client_secret: str
    ctrader_redirect_uri: str
    ctrader_env: str
    live_trading_enabled: bool
    spotware_approved: bool
    risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_open_positions: int
    max_consecutive_losses: int
    telegram_bot_token: str
    telegram_chat_id: str

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.getenv("CTRADER_ENV", "demo").strip().lower()
        if env not in {"demo", "live"}:
            raise ValueError("CTRADER_ENV must be 'demo' or 'live'.")

        settings = cls(
            ctrader_client_id=os.getenv("CTRADER_CLIENT_ID", "").strip(),
            ctrader_client_secret=os.getenv("CTRADER_CLIENT_SECRET", "").strip(),
            ctrader_redirect_uri=os.getenv("CTRADER_REDIRECT_URI", "").strip(),
            ctrader_env=env,
            live_trading_enabled=_bool("LIVE_TRADING_ENABLED"),
            spotware_approved=_bool("SPOTWARE_APPROVED"),
            risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", "0.25")),
            max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "1.0")),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "2")),
            max_consecutive_losses=int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3")),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        )
        if not 0 < settings.risk_per_trade_pct <= 1:
            raise ValueError("RISK_PER_TRADE_PCT must be > 0 and <= 1.")
        if not 0 < settings.max_daily_loss_pct <= 5:
            raise ValueError("MAX_DAILY_LOSS_PCT must be > 0 and <= 5.")
        if settings.max_open_positions < 1 or settings.max_consecutive_losses < 1:
            raise ValueError("Position and loss limits must be positive.")
        return settings

    @property
    def live_execution_permitted(self) -> bool:
        # Fail closed. These flags alone do not replace integration testing
        # and explicit user approval in the eventual release process.
        return (
            self.ctrader_env == "live"
            and self.live_trading_enabled
            and self.spotware_approved
            and bool(self.ctrader_client_id)
            and bool(self.ctrader_client_secret)
            and bool(self.ctrader_redirect_uri)
        )
