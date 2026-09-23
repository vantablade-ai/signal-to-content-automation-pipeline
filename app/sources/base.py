from pathlib import Path
from typing import Protocol

from app.models import RawSignal


class SignalSource(Protocol):
    name: str
    path: Path

    def load(self) -> list[RawSignal]: ...
