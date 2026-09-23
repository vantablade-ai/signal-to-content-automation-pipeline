import json
from pathlib import Path

from app.models import RawSignal


class JsonFixtureSource:
    name = "json_fixture"

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[RawSignal]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise TypeError("JSON fixture root must be a list")
        rows = []
        for index, item in enumerate(data):
            try:
                rows.append(RawSignal.model_validate({"source_type": "json", **item}))
            except Exception as exc:
                raise ValueError(f"Invalid JSON signal at index {index}: {exc}") from exc
        return rows
