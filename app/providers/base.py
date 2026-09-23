from typing import Protocol

from app.models import Classification, ContentBrief, ScriptArtifact, Signal


class ContentProvider(Protocol):
    name: str
    version: str

    def classify(self, signal: Signal) -> Classification: ...
    def build_brief(self, signal: Signal, classification: Classification) -> ContentBrief: ...
    def build_script(self, brief: ContentBrief) -> ScriptArtifact: ...
