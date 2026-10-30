"""Local persistent kill switch; broker-side position closure is not implemented."""
from pathlib import Path


class KillSwitch:
    def __init__(self, state_file: str = "state/KILL_SWITCH") -> None:
        self.state_file = Path(state_file)

    def is_active(self) -> bool:
        return self.state_file.exists()

    def activate(self, reason: str = "Manual emergency stop") -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(reason.strip() or "Emergency stop", encoding="utf-8")

    def reset(self, *, explicit_operator_confirmation: bool) -> None:
        if not explicit_operator_confirmation:
            raise PermissionError("Explicit operator confirmation is required to reset the kill switch.")
        self.state_file.unlink(missing_ok=True)
