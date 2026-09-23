import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import STAGES, Pipeline


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="signal-pipeline-demo-") as temp:
        runs = Path(temp)
        print("Signal-to-Content Automation Pipeline — demo")
        first = Pipeline(runs, "proof", root / "examples/signals.json", root / "examples/signals.xml").run()
        assert first["completion_state"] == "SUCCEEDED" and first["duplicates"] == 1
        second = Pipeline(runs, "proof", root / "examples/signals.json", root / "examples/signals.xml").run()
        assert second["executed"] == 0 and second["reused"] == len(STAGES)
        failed = Pipeline(runs, "proof", root / "examples/signals.json", root / "examples/signals.xml",
                          render_config={"template": "failure-demo"}, fail_render_once=True).run()
        assert failed["completion_state"] == "INCOMPLETE"
        resumed = Pipeline(runs, "proof", root / "examples/signals.json", root / "examples/signals.xml",
                           render_config={"template": "failure-demo"}, fail_render_once=True).run()
        assert resumed["completion_state"] == "SUCCEEDED"
        assert resumed["reused"] >= 8
        print("All pipeline invariants passed.")
        print(f"Initial artifacts: {len(list((runs / 'proof').iterdir()))}; cached rerun reused {second['reused']} stages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
