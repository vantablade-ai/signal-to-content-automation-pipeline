import hashlib
import json
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import Signal
from app.pipeline import (
    DEPENDENCIES,
    STAGES,
    Pipeline,
    deduplicate,
    normalize,
    rank_signals,
)
from app.providers.mock import MockContentProvider
from app.sources import JsonFixtureSource, RSSFixtureSource

ROOT = Path(__file__).resolve().parents[1]
JSON = ROOT / "examples/signals.json"
RSS = ROOT / "examples/signals.xml"


def run(tmp_path, **kwargs):
    return Pipeline(tmp_path, "test", JSON, RSS, **kwargs)


def test_sources_and_canonical_normalization():
    rows = JsonFixtureSource(JSON).load() + RSSFixtureSource(RSS).load()
    assert len(rows) == 8
    one = normalize(rows[0].model_dump())
    assert one.title == "Retries amplify API timeouts"
    assert one.source_reference.startswith("fixture://")
    a = normalize({**rows[0].model_dump(), "source_metadata": {"z": "1", "a": "2"}})
    b = normalize({**rows[0].model_dump(), "source_metadata": {"a": "2", "z": "1"}})
    assert a.content_hash == b.content_hash


def test_invalid_required_signal_rejected():
    with pytest.raises(ValidationError):
        Signal(signal_id="x", source_type="json", source_reference="", title="", body="", content_hash="x")


def test_dedup_filter_and_ranking_are_deterministic():
    rows = [normalize(x.model_dump()) for x in JsonFixtureSource(JSON).load() + RSSFixtureSource(RSS).load()]
    report = deduplicate(rows)
    assert len(report["duplicates"]) == 1
    assert any("content_too_short" in row["reasons"] for row in report["exclusions"])
    kept = [Signal.model_validate(x) for x in report["kept"]]
    assert [x.signal_id for x in rank_signals(kept, MockContentProvider())] == [
        x.signal_id for x in rank_signals(kept, MockContentProvider())]
    assert "PUBLISH" not in STAGES and "UPLOAD" not in STAGES and "POST" not in STAGES


def test_unchanged_run_reuses_every_stage(tmp_path):
    result = run(tmp_path).run()
    assert result["completion_state"] == "SUCCEEDED"
    again = run(tmp_path).run()
    assert again["executed"] == 0 and again["reused"] == len(STAGES)
    assert (tmp_path / "test/audio.wav").exists()
    manifest = json.loads((tmp_path / "test/manifest.json").read_text())
    assert all(rec["status"] == "SUCCEEDED" for rec in manifest["stage_records"].values())
    assert all(a["sha256"] for a in manifest["artifacts"].values())


def test_cached_ingest_and_normalize_skip_transformations(tmp_path, monkeypatch):
    run(tmp_path).run()

    def unexpected(*args, **kwargs):
        raise AssertionError("cached stage transformation should not execute")

    monkeypatch.setattr(JsonFixtureSource, "load", unexpected)
    monkeypatch.setattr(RSSFixtureSource, "load", unexpected)
    monkeypatch.setattr("app.pipeline.normalize", unexpected)
    result = run(tmp_path).run()
    assert result["executed"] == 0
    assert result["reused"] == len(STAGES)


def test_prior_success_then_rank_failure_blocks_and_discards_descendants(tmp_path, monkeypatch):
    run(tmp_path).run()
    monkeypatch.setattr("app.pipeline.render", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("stale media must not be rendered")))

    result = run(tmp_path, provider=MockContentProvider("error")).run()
    manifest = json.loads((tmp_path / "test/manifest.json").read_text())
    records = manifest["stage_records"]
    assert result["completion_state"] == "INCOMPLETE"
    assert records["RANK"]["status"] == "FAILED"
    assert records["BRIEF"]["status"] == "BLOCKED"
    for stage in ("SCRIPT", "TTS", "CAPTIONS", "RENDER", "PACKAGE"):
        assert records[stage]["status"] == "BLOCKED"
        assert records[stage]["output_artifacts"] == []
    assert not any(item["producer_stage"] in {"BRIEF", "SCRIPT", "TTS", "CAPTIONS", "RENDER", "PACKAGE"}
                   for item in manifest["artifacts"].values())
    assert (tmp_path / "test/audio.wav").exists()  # retained on disk, but no longer active in the manifest


def test_prior_success_then_zero_ranked_signals_blocks_descendants(tmp_path):
    run(tmp_path).run()
    empty_json = tmp_path / "empty.json"
    empty_rss = tmp_path / "empty.xml"
    empty_json.write_text("[]")
    empty_rss.write_text("<rss><channel /></rss>")

    result = Pipeline(tmp_path, "test", empty_json, empty_rss).run()
    manifest = json.loads((tmp_path / "test/manifest.json").read_text())
    records = manifest["stage_records"]
    assert result["completion_state"] == "INCOMPLETE"
    assert records["RANK"]["status"] == "SUCCEEDED"
    assert json.loads((tmp_path / "test/rankings.json").read_text()) == []
    for stage in ("BRIEF", "SCRIPT", "TTS", "CAPTIONS", "RENDER", "PACKAGE"):
        assert records[stage]["status"] == "BLOCKED"
        assert records[stage]["output_artifacts"] == []


def test_rebuilt_artifacts_all_match_their_current_files(tmp_path):
    run(tmp_path).run()
    run(tmp_path, stage_configurations={"SCRIPT": {"prompt_revision": "v2"}}).run()
    manifest = json.loads((tmp_path / "test/manifest.json").read_text())
    for artifact in manifest["artifacts"].values():
        path = tmp_path / "test" / artifact["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]


def test_render_failure_resume_preserves_upstream(tmp_path):
    run(tmp_path, render_config={"template": "failure"}, fail_render_once=True).run()
    manifest_path = tmp_path / "test/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["stage_records"]["RENDER"]["status"] == "FAILED"
    assert manifest["stage_records"]["PACKAGE"]["status"] == "BLOCKED"
    assert manifest["stage_records"]["SCRIPT"]["status"] == "SUCCEEDED"
    resumed = run(tmp_path, render_config={"template": "failure"}, fail_render_once=True).run()
    assert resumed["completion_state"] == "SUCCEEDED"
    assert resumed["executed"] == 2
    assert resumed["reused"] == 8


def test_corrupt_artifact_rebuilds_it_and_descendants(tmp_path):
    run(tmp_path).run()
    (tmp_path / "test/script.json").write_text("corrupt")
    outcome = run(tmp_path).run()
    assert outcome["completion_state"] == "SUCCEEDED"
    assert outcome["executed"] >= 1


def test_missing_artifact_is_rebuilt(tmp_path):
    run(tmp_path).run()
    (tmp_path / "test/captions.srt").unlink()
    outcome = run(tmp_path).run()
    assert outcome["completion_state"] == "SUCCEEDED"
    assert outcome["executed"] >= 1


def test_render_config_narrow_invalidation(tmp_path):
    run(tmp_path).run()
    outcome = run(tmp_path, render_config={"template": "v2"}).run()
    assert outcome["executed"] == 2
    assert outcome["reused"] == 8


def test_script_stage_configuration_invalidates_only_descendants(tmp_path):
    run(tmp_path).run()
    outcome = run(tmp_path, stage_configurations={"SCRIPT": {"prompt_revision": "v2"}}).run()
    assert outcome["executed"] == 5
    assert outcome["reused"] == 5


def test_source_change_invalidates_from_ingest(tmp_path):
    run(tmp_path).run()
    changed = tmp_path / "new.json"
    data = json.loads(JSON.read_text())
    data.append({"source_reference": "fixture://new", "title": "A new engineering signal",
                 "body": "A new synthetic signal describes a clear integration issue and a repeatable diagnostic check.",
                 "tags": ["test"]})
    changed.write_text(json.dumps(data))
    outcome = Pipeline(tmp_path, "test", changed, RSS).run()
    assert outcome["executed"] == len(STAGES)


def test_invalid_provider_result_fails_at_rank(tmp_path):
    result = run(tmp_path, provider=MockContentProvider("error")).run()
    assert result["completion_state"] == "INCOMPLETE"
    manifest = json.loads((tmp_path / "test/manifest.json").read_text())
    assert manifest["stage_records"]["RANK"]["status"] == "FAILED"
    assert manifest["stage_records"]["INGEST"]["status"] == "SUCCEEDED"


def test_malformed_provider_schema_fails_safely(tmp_path):
    run(tmp_path, provider=MockContentProvider("malformed")).run()
    manifest = json.loads((tmp_path / "test/manifest.json").read_text())
    assert manifest["stage_records"]["RANK"]["status"] == "FAILED"
    assert manifest["stage_records"]["RANK"]["error"]["type"] == "ValidationError"


def test_audio_caption_and_package_artifacts(tmp_path):
    run(tmp_path).run()
    audio_path = tmp_path / "test/audio.wav"
    with wave.open(str(audio_path), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getframerate() == 8000
        assert audio.getnframes() > 0
    captions = (tmp_path / "test/captions.srt").read_text()
    assert captions.startswith("1\n") and " --> " in captions and captions.strip().endswith(".")
    package = json.loads((tmp_path / "test/package.json").read_text())
    assert package["selected_signal_id"]
    assert package["artifacts"]["brief"] and package["artifacts"]["script"]
    assert package["artifacts"]["audio"][0]["sha256"]


def test_explicit_ffmpeg_unavailable_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("app.media.shutil.which", lambda _: None)
    result = run(tmp_path, renderer="ffmpeg").run()
    assert result["completion_state"] == "INCOMPLETE"
    manifest = json.loads((tmp_path / "test/manifest.json").read_text())
    assert manifest["stage_records"]["RENDER"]["status"] == "FAILED"
    assert "FFmpeg unavailable" in manifest["stage_records"]["RENDER"]["error"]["message"]


def test_stage_graph_has_explicit_dependencies():
    assert DEPENDENCIES["RENDER"] == ("TTS", "CAPTIONS")
    assert DEPENDENCIES["PACKAGE"][-1] == "RENDER"
