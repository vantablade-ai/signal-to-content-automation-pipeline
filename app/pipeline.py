from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.hashing import canonical_json, fingerprint, sha256_bytes
from app.models import (
    ArtifactRecord,
    ContentBrief,
    Eligibility,
    RankedCandidate,
    RunManifest,
    ScriptArtifact,
    Signal,
    StageRecord,
    StageStatus,
)
from app.providers.mock import MockContentProvider
from app.sources import JsonFixtureSource, RSSFixtureSource

PIPELINE_VERSION = "1.0.0"
STAGES = ("INGEST", "NORMALIZE", "DEDUPLICATE", "RANK", "BRIEF", "SCRIPT", "TTS",
          "CAPTIONS", "RENDER", "PACKAGE")
DEPENDENCIES = {
    "INGEST": (), "NORMALIZE": ("INGEST",), "DEDUPLICATE": ("NORMALIZE",),
    "RANK": ("DEDUPLICATE",), "BRIEF": ("RANK",), "SCRIPT": ("BRIEF",),
    "TTS": ("SCRIPT",), "CAPTIONS": ("SCRIPT",), "RENDER": ("TTS", "CAPTIONS"),
    "PACKAGE": ("RANK", "BRIEF", "SCRIPT", "TTS", "CAPTIONS", "RENDER"),
}
STAGE_FILES = {"INGEST": "ingest.json", "NORMALIZE": "normalized_signals.json",
              "DEDUPLICATE": "deduplication.json", "RANK": "rankings.json",
              "BRIEF": "brief.json", "SCRIPT": "script.json", "TTS": "audio.wav",
              "CAPTIONS": "captions.srt", "RENDER": "render.json", "PACKAGE": "package.json"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def atomic_json(path: Path, data: Any) -> None:
    atomic_bytes(path, canonical_json(data) + b"\n")


def semantic_hash(value: Any) -> str:
    return fingerprint(value)


def normalize(raw: dict[str, Any]) -> Signal:
    title = " ".join(str(raw.get("title", "")).split())
    body = " ".join(str(raw.get("body", "")).split())
    ref = " ".join(str(raw.get("source_reference", "")).split())
    if not title or not body or not ref:
        raise ValueError("required title, body and source_reference must be non-empty")
    source_type = str(raw["source_type"]).strip().lower()
    tags = tuple(sorted({" ".join(str(t).strip().lower().split()) for t in raw.get("tags", ()) if str(t).strip()}))
    published = raw.get("published_at")
    if published:
        published = str(published).strip()
        try:
            parsed = datetime.fromisoformat(published)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            published = parsed.astimezone(UTC).isoformat()
        except ValueError:
            pass
    payload = {"source_type": source_type, "source_reference": ref, "title": title,
               "body": body, "published_at": published}
    signal_id = f"sig_{fingerprint([source_type, ref])[:12]}"
    return Signal(signal_id=signal_id, source_type=source_type, source_reference=ref,
                  title=title, body=body, author_reference=raw.get("author_reference"),
                  published_at=published, tags=tags,
                  source_metadata={str(k): str(v)[:200] for k, v in list(raw.get("source_metadata", {}).items())[:8]},
                  content_hash=fingerprint(payload))


def is_eligible(signal: Signal) -> Eligibility:
    reasons = []
    if len(signal.body) < 35:
        reasons.append("content_too_short")
    if signal.source_type not in {"json", "rss"}:
        reasons.append("unsupported_source_type")
    if not signal.source_reference:
        reasons.append("missing_source_attribution")
    return Eligibility(signal_id=signal.signal_id, eligible=not reasons, reasons=tuple(reasons))


def deduplicate(signals: list[Signal]) -> dict[str, Any]:
    kept, duplicates, exclusions, seen = [], [], [], {}
    for signal in signals:
        eligibility = is_eligible(signal)
        if not eligibility.eligible:
            exclusions.append({"signal_id": signal.signal_id, "eligible": False,
                               "reasons": list(eligibility.reasons)})
            continue
        key = fingerprint([signal.title.lower(), signal.body.lower()])
        exact_ref = next((s for s in kept if s.source_reference == signal.source_reference), None)
        prior = exact_ref or seen.get(key)
        if prior:
            reason = "canonical_source_reference" if exact_ref else "normalized_content_hash"
            duplicates.append({"kept_signal_id": prior.signal_id, "duplicate_signal_id": signal.signal_id,
                               "reason": reason, "matching_fingerprint": key,
                               "matching_reference": signal.source_reference if exact_ref else None})
            continue
        kept.append(signal)
        seen[key] = signal
    return {"kept": [s.model_dump(mode="json") for s in kept],
            "duplicates": duplicates, "exclusions": exclusions}


def rank_signals(signals: list[Signal], provider: MockContentProvider) -> list[RankedCandidate]:
    results = []
    for signal in signals:
        classification = provider.classify(signal)
        # Deterministic, bounded weighted score: relevance 35%, content 30%, evidence 25%, novelty 10%.
        parts = (classification.relevance * .35, classification.content_potential * .30,
                 classification.evidence_quality * .25, classification.novelty * .10)
        results.append(RankedCandidate(signal_id=signal.signal_id, classification=classification,
                                       relevance_component=parts[0], content_component=parts[1],
                                       evidence_component=parts[2], novelty_component=parts[3],
                                       ranking_score=round(sum(parts), 4)))
    return sorted(results, key=lambda r: (-r.ranking_score, r.signal_id))


class Pipeline:
    """Filesystem-backed deterministic runner. All provenance hashes are integrity/cache keys."""

    def __init__(self, runs_dir: Path, run_id: str, json_source: Path, rss_source: Path,
                 provider: MockContentProvider | None = None, renderer: str = "mock",
                 render_config: dict[str, Any] | None = None, fail_render_once: bool = False,
                 stage_configurations: dict[str, dict[str, Any]] | None = None):
        self.root = runs_dir / run_id
        self.run_id = run_id
        self.json_source, self.rss_source = json_source, rss_source
        self.provider = provider or MockContentProvider()
        self.renderer = renderer
        self.render_config = render_config or {"template": "solid-v1"}
        self.fail_render_once = fail_render_once
        self.stage_configurations = stage_configurations or {}
        self.manifest_path = self.root / "manifest.json"
        self.manifest: RunManifest
        self.executed = 0
        self.reused = 0
        self.created = 0

    def _init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            self.manifest = RunManifest.model_validate_json(self.manifest_path.read_text())
            if self.manifest.run_id != self.run_id:
                raise ValueError("run id does not match manifest")
        else:
            now = utc_now()
            self.manifest = RunManifest(run_id=self.run_id, created_at=now, updated_at=now,
                source_configuration={}, stage_records={s: StageRecord(stage=s) for s in STAGES})
        self._save()

    def _save(self) -> None:
        self.manifest.updated_at = utc_now()
        atomic_json(self.manifest_path, self.manifest.model_dump(mode="json"))

    def _source_payload(self) -> tuple[list[dict[str, Any]], list[Signal]]:
        raw = [r.model_dump(mode="json") for source in
               (JsonFixtureSource(self.json_source), RSSFixtureSource(self.rss_source))
               for r in source.load()]
        return raw, [normalize(row) for row in raw]

    def _dependency_hashes(self, stage: str) -> dict[str, str]:
        out = {}
        for dep in DEPENDENCIES[stage]:
            record = self.manifest.stage_records[dep]
            for artifact_id in record.output_artifacts:
                artifact = self.manifest.artifacts.get(artifact_id)
                if artifact:
                    out[artifact_id] = artifact.sha256
        return out

    def _fingerprint(self, stage: str, config: Any) -> str:
        return fingerprint({"stage": stage, "stage_version": "1.0.0", "config": config,
                           "dependencies": self._dependency_hashes(stage)})

    def _artifact_valid(self, artifact_id: str) -> bool:
        item = self.manifest.artifacts.get(artifact_id)
        if not item:
            return False
        path = self.root / item.path
        return path.is_file() and sha256_bytes(path.read_bytes()) == item.sha256

    def _run_stage(self, stage: str, config: Any, fn, *, artifact_type: str | None = None,
                   media_type: str | None = None) -> Any:
        rec = self.manifest.stage_records[stage]
        deps = DEPENDENCIES[stage]
        blocked = [dep for dep in deps if self.manifest.stage_records[dep].status not in
                   {StageStatus.SUCCEEDED}]
        if blocked:
            rec.status, rec.blocked_by = StageStatus.BLOCKED, blocked
            rec.error = {"type": "dependency_unavailable", "message": ",".join(blocked)}
            self._save()
            return None
        config = {"stage": config, "stage_configuration": self.stage_configurations.get(stage, {})}
        fp = self._fingerprint(stage, config)
        if (rec.status == StageStatus.SUCCEEDED and rec.fingerprint == fp and rec.output_artifacts
                and all(self._artifact_valid(a) for a in rec.output_artifacts)):
            rec.reused = True
            rec.reuse_count += 1
            rec.last_reused_at = utc_now()
            self.reused += 1
            result = self._read_stage(stage)
            self._save()
            print(f"[{stage}] REUSED")
            return result
        rec.status, rec.fingerprint = StageStatus.RUNNING, fp
        rec.started_at, rec.finished_at = utc_now(), None
        rec.error, rec.blocked_by, rec.reused = None, [], False
        rec.input_artifacts = list(self._dependency_hashes(stage))
        rec.provider = self.provider.name if stage in {"RANK", "BRIEF", "SCRIPT"} else None
        rec.provider_version = self.provider.version if rec.provider else None
        self._save()
        try:
            result = fn()
            filename = STAGE_FILES[stage]
            target = self.root / filename
            if isinstance(result, bytes):
                payload = result
            elif isinstance(result, str):
                payload = result.encode("utf-8")
            else:
                payload = canonical_json(result) + b"\n"
            atomic_bytes(target, payload)
            aid = f"{stage.lower()}_{fingerprint([stage, fp])[:12]}"
            record = ArtifactRecord(artifact_id=aid, artifact_type=artifact_type or stage.lower(),
                path=filename, sha256=sha256_bytes(payload), size_bytes=len(payload),
                created_at=utc_now(), producer_stage=stage, producer_version="1.0.0",
                media_type=media_type)
            self.manifest.artifacts[aid] = record
            rec.output_artifacts = [aid]
            if stage == "RENDER" and isinstance(result, dict) and result.get("path") == "final.mp4":
                media_path = self.root / "final.mp4"
                media_hash = sha256_bytes(media_path.read_bytes())
                media_id = "render_media_" + media_hash[:12]
                self.manifest.artifacts[media_id] = ArtifactRecord(
                    artifact_id=media_id, artifact_type="rendered_media", path="final.mp4",
                    sha256=media_hash, size_bytes=media_path.stat().st_size, created_at=utc_now(),
                    producer_stage="RENDER", producer_version="1.0.0", media_type="video/mp4")
                rec.output_artifacts.append(media_id)
            rec.status, rec.finished_at = StageStatus.SUCCEEDED, utc_now()
            rec.error = None
            self.executed += 1
            self.created += 1
            self._save()
            print(f"[{stage}] SUCCEEDED")
            return result
        except Exception as exc:  # noqa: BLE001 - a stage boundary must persist any provider/tool failure
            rec.status, rec.finished_at = StageStatus.FAILED, utc_now()
            rec.error = {"type": type(exc).__name__, "message": str(exc)[:500]}
            self.manifest.failures.append({"stage": stage, **rec.error})
            self._save()
            print(f"[{stage}] FAILED: {type(exc).__name__}: {str(exc)[:160]}")
            return None

    def _read_stage(self, stage: str):
        path = self.root / STAGE_FILES[stage]
        if stage == "TTS":
            return path.read_bytes()
        if stage == "CAPTIONS":
            return path.read_text(encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8"))

    def run(self) -> dict[str, Any]:
        self._init()
        raw_config = {"json": {"path": str(self.json_source), "sha256": sha256_bytes(self.json_source.read_bytes())},
                      "rss": {"path": str(self.rss_source), "sha256": sha256_bytes(self.rss_source.read_bytes())}}
        self.manifest.source_configuration = raw_config
        provider_settings = {"name": self.provider.name, "version": self.provider.version,
                             "behavior": getattr(self.provider, "behavior", "default")}
        self.manifest.provider_metadata = provider_settings
        raw, normalized = self._source_payload()
        ingest = self._run_stage("INGEST", raw_config, lambda: raw, artifact_type="raw_signals")
        if ingest is None: ingest = raw if self.manifest.stage_records["INGEST"].status == StageStatus.SUCCEEDED else None
        normalized_dump = [s.model_dump(mode="json") for s in normalized]
        norm = self._run_stage("NORMALIZE", {"normalizer": "1.0"}, lambda: normalized_dump,
                               artifact_type="canonical_signals")
        if norm is None and self.manifest.stage_records["NORMALIZE"].status == StageStatus.SUCCEEDED: norm = self._read_stage("NORMALIZE")
        norm_signals = [Signal.model_validate(x) for x in (norm or [])]
        dedup = self._run_stage("DEDUPLICATE", {"min_body_chars": 35, "algorithm": "exact-v1"},
                                lambda: deduplicate(norm_signals), artifact_type="deduplication_report")
        if dedup is None and self.manifest.stage_records["DEDUPLICATE"].status == StageStatus.SUCCEEDED: dedup = self._read_stage("DEDUPLICATE")
        kept = [Signal.model_validate(x) for x in (dedup or {}).get("kept", [])]
        rank_cfg = {"weights": [0.35, 0.30, 0.25, 0.10], **provider_settings}
        ranked = self._run_stage("RANK", rank_cfg,
             lambda: [r.model_dump(mode="json") for r in rank_signals(kept, self.provider)],
             artifact_type="rankings")
        if ranked is None and self.manifest.stage_records["RANK"].status == StageStatus.SUCCEEDED: ranked = self._read_stage("RANK")
        rankings = [RankedCandidate.model_validate(x) for x in (ranked or [])]
        if rankings:
            selected = rankings[0].signal_id
            self.manifest.selected_signal_id = selected
            signal = next(s for s in kept if s.signal_id == selected)
            brief = self._run_stage("BRIEF", {**provider_settings, "selected_signal": selected},
                lambda: self.provider.build_brief(signal, rankings[0].classification).model_dump(mode="json"), artifact_type="brief")
            if brief is None and self.manifest.stage_records["BRIEF"].status == StageStatus.SUCCEEDED: brief = self._read_stage("BRIEF")
        else:
            brief = None
            self.manifest.stage_records["BRIEF"].status = StageStatus.BLOCKED
            self.manifest.stage_records["BRIEF"].blocked_by = ["RANK"]
        brief_model = ContentBrief.model_validate(brief) if brief else None
        script = self._run_stage("SCRIPT", provider_settings,
            lambda: self.provider.build_script(brief_model).model_dump(mode="json"), artifact_type="script") if brief_model else None
        if script is None and self.manifest.stage_records["SCRIPT"].status == StageStatus.SUCCEEDED: script = self._read_stage("SCRIPT")
        script_model = ScriptArtifact.model_validate(script) if script else None
        audio = self._run_stage("TTS", {"tts": "synthetic-tone-v1", "sample_rate": 8000},
            lambda: self._make_wav(script_model.estimated_duration_seconds), artifact_type="synthetic_audio", media_type="audio/wav") if script_model else None
        captions = self._run_stage("CAPTIONS", {"captioner": "deterministic-srt-v1"},
            lambda: self._captions(script_model), artifact_type="captions", media_type="application/x-subrip") if script_model else None
        if captions is None and self.manifest.stage_records["CAPTIONS"].status == StageStatus.SUCCEEDED: captions = self._read_stage("CAPTIONS")
        if audio is None and self.manifest.stage_records["TTS"].status == StageStatus.SUCCEEDED: audio = self._read_stage("TTS")
        render_config = {"renderer": self.renderer, **self.render_config}
        render = self._run_stage("RENDER", render_config,
            lambda: self._render(audio, captions), artifact_type="render_descriptor", media_type="application/json") if audio is not None and captions is not None else None
        if render is None:
            render_record = self.manifest.stage_records["RENDER"]
            if render_record.status != StageStatus.FAILED:
                render_record.status = StageStatus.BLOCKED
                render_record.blocked_by = ["TTS", "CAPTIONS"]
            package_record = self.manifest.stage_records["PACKAGE"]
            package_record.status = StageStatus.BLOCKED
            package_record.blocked_by = ["RENDER"]
            self._save()
        else:
            self._run_stage("PACKAGE", {"package_schema": "1.0"},
                lambda: self._package(selected if rankings else None), artifact_type="package")
        for stage in STAGES:
            rec = self.manifest.stage_records[stage]
            unavailable = [dep for dep in DEPENDENCIES[stage]
                           if self.manifest.stage_records[dep].status != StageStatus.SUCCEEDED]
            if rec.status == StageStatus.PENDING and unavailable:
                rec.status = StageStatus.BLOCKED
                rec.blocked_by = unavailable
                rec.error = {"type": "dependency_unavailable", "message": ",".join(unavailable)}
        self.manifest.completion_state = "SUCCEEDED" if all(
            self.manifest.stage_records[s].status == StageStatus.SUCCEEDED for s in STAGES
        ) else "INCOMPLETE"
        self._save()
        return {"executed": self.executed, "reused": self.reused, "artifacts_created": self.created,
                "completion_state": self.manifest.completion_state, "selected_signal_id": self.manifest.selected_signal_id,
                "duplicates": len((dedup or {}).get("duplicates", []))}

    @staticmethod
    def _make_wav(duration: int) -> bytes:
        rate, count = 8000, duration * 8000
        import io
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
            wav.writeframes(b"".join(struct.pack("<h", 700 if (i // 80) % 2 else -700) for i in range(count)))
        return output.getvalue()

    @staticmethod
    def _captions(script: ScriptArtifact) -> str:
        words = script.caption_text.split()
        chunks = [" ".join(words[i:i + 8]) for i in range(0, len(words), 8)]
        duration = script.estimated_duration_seconds
        rows = []
        for i, text in enumerate(chunks):
            start = duration * i / len(chunks)
            end = duration * (i + 1) / len(chunks)
            rows.append(f"{i + 1}\n{Pipeline._srt_time(start)} --> {Pipeline._srt_time(end)}\n{text}")
        return "\n\n".join(rows) + "\n"

    @staticmethod
    def _srt_time(seconds: float) -> str:
        ms = round(seconds * 1000)
        h, ms = divmod(ms, 3_600_000); m, ms = divmod(ms, 60_000); s, ms = divmod(ms, 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    def _render(self, audio: bytes, captions: str) -> dict[str, Any]:
        if self.fail_render_once:
            marker = self.root / ".render-failed-once"
            if not marker.exists():
                atomic_bytes(marker, b"failed")
                raise RuntimeError("simulated render failure")
        if self.renderer == "mock":
            return {"renderer": "mock", "label": "synthetic placeholder", "audio_sha256": sha256_bytes(audio),
                    "caption_sha256": sha256_bytes(captions.encode()), "container": "json-placeholder"}
        if self.renderer != "ffmpeg":
            raise ValueError("renderer must be mock or ffmpeg")
        executable = shutil.which("ffmpeg")
        if not executable:
            raise RuntimeError("FFmpeg unavailable: install ffmpeg or select --renderer mock")
        wav_path, mp4_path = self.root / "audio.wav", self.root / "final.mp4"
        proc = subprocess.run([executable, "-y", "-f", "lavfi", "-i", "color=c=0x182433:s=640x360:r=24",
             "-i", str(wav_path), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", str(mp4_path)], capture_output=True, text=True, check=False)
        if proc.returncode or not mp4_path.is_file():
            raise RuntimeError(f"FFmpeg exit={proc.returncode}: {proc.stderr[-300:]}")
        payload = mp4_path.read_bytes()
        return {"renderer": "ffmpeg", "path": "final.mp4", "sha256": sha256_bytes(payload),
                "exit_code": proc.returncode, "stdout": proc.stdout[-300:], "stderr": proc.stderr[-300:]}

    def _package(self, selected: str | None) -> dict[str, Any]:
        refs = {}
        for stage in ("BRIEF", "SCRIPT", "TTS", "CAPTIONS", "RENDER"):
            ids = self.manifest.stage_records[stage].output_artifacts
            key = "audio" if stage == "TTS" else stage.lower()
            refs[key] = [self.manifest.artifacts[x].model_dump(mode="json")
                         for x in ids if x in self.manifest.artifacts]
        return {"run_id": self.run_id, "pipeline_version": PIPELINE_VERSION,
                "selected_signal_id": selected, "artifacts": refs,
                "publishing_capability": False, "package_boundary": "local_artifact_only"}


def stage_summary(manifest_path: Path) -> str:
    m = RunManifest.model_validate_json(manifest_path.read_text())
    return "\n".join(f"{name:12} {m.stage_records[name].status.value:10}" +
                     (" reused" if m.stage_records[name].reused else "") for name in STAGES)
