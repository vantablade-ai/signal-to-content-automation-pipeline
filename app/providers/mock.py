from app.hashing import fingerprint
from app.models import Classification, ContentBrief, ScriptArtifact, ScriptSection, Signal


class MockContentProvider:
    name = "mock"
    version = "1.0"

    def __init__(self, behavior: str = "valid"):
        self.behavior = behavior

    def classify(self, signal: Signal) -> Classification:
        if self.behavior == "error":
            raise RuntimeError("simulated provider error")
        if self.behavior == "malformed":
            return Classification.model_validate({"signal_id": signal.signal_id, "relevance": 500})
        text = f"{signal.title} {signal.body}".lower()
        category = "LOW_VALUE" if len(signal.body) < 50 else "TECHNICAL_INSIGHT"
        score = 18 if category == "LOW_VALUE" else min(92, 55 + len(set(text.split())) % 35)
        return Classification(
            signal_id=signal.signal_id, category=category, relevance=score,
            novelty=65, evidence_quality=72, content_potential=78 if category != "LOW_VALUE" else 20,
            confidence=0.35 if self.behavior == "low_confidence" else 0.86,
            reason="Deterministic fixture heuristic based on supplied signal text.",
        )

    def build_brief(self, signal: Signal, classification: Classification) -> ContentBrief:
        return ContentBrief(
            brief_id="brief_" + fingerprint([signal.signal_id, classification.model_dump()])[:12],
            signal_id=signal.signal_id, topic=signal.title,
            core_claim=f"The signal describes: {signal.body[:180]}",
            supporting_points=(signal.body[:180],), source_references=(signal.source_reference,),
            target_audience="Software and operations practitioners",
            angle="Explain the observed workflow issue and a practical diagnostic question.",
            key_takeaways=("Separate observed facts from assumptions.", "Inspect the handoff where the issue appears."),
            limitations=("Based on one synthetic fixture signal.",),
            unsupported_claims_to_avoid=("Do not imply prevalence or root cause beyond the supplied signal.",),
            recommended_format="short_explainer",
        )

    def build_script(self, brief: ContentBrief) -> ScriptArtifact:
        text = f"{brief.core_claim} {brief.key_takeaways[0]}"
        return ScriptArtifact(
            script_id="script_" + fingerprint([brief.brief_id, text])[:12],
            brief_id=brief.brief_id, title=brief.topic[:120],
            hook=f"A useful engineering signal: {brief.topic}",
            sections=(ScriptSection(heading="What we know", text=brief.core_claim),
                      ScriptSection(heading="What to check", text=brief.key_takeaways[0])),
            voiceover_text=text, caption_text=text, source_references=brief.source_references,
            estimated_duration_seconds=max(8, min(600, len(text.split()) // 2)),
        )
