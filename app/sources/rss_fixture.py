import xml.etree.ElementTree as ET
from pathlib import Path

from app.models import RawSignal


class RSSFixtureSource:
    name = "rss_fixture"

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[RawSignal]:
        try:
            root = ET.parse(self.path).getroot()
        except (ET.ParseError, OSError) as exc:
            raise ValueError(f"Invalid RSS fixture: {exc}") from exc
        rows = []
        for item in root.findall(".//item"):
            rows.append(RawSignal(
                source_type="rss",
                source_reference=(item.findtext("guid") or item.findtext("link") or "").strip(),
                title=item.findtext("title") or "",
                body=item.findtext("description") or "",
                published_at=item.findtext("pubDate"),
                tags=tuple(c.text.strip() for c in item.findall("category") if c.text),
            ))
        return rows
