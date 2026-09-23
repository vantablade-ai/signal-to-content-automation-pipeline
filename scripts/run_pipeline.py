import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import Pipeline
from app.providers.mock import MockContentProvider


def main() -> int:
    p = argparse.ArgumentParser(description="Run the offline signal-to-content pipeline")
    p.add_argument("--run-id", default="demo")
    p.add_argument("--json-source", type=Path, default=Path("examples/signals.json"))
    p.add_argument("--rss-source", type=Path, default=Path("examples/signals.xml"))
    p.add_argument("--runs-dir", type=Path, default=Path("runs"))
    p.add_argument("--provider", choices=["mock"], default="mock")
    p.add_argument("--provider-behavior", choices=["valid", "malformed", "error", "low_confidence"], default="valid")
    p.add_argument("--renderer", choices=["mock", "ffmpeg"], default="mock")
    args = p.parse_args()
    result = Pipeline(args.runs_dir, args.run_id, args.json_source, args.rss_source,
                      MockContentProvider(args.provider_behavior), args.renderer).run()
    print(f"Run {args.run_id}: {result}")
    return 0 if result["completion_state"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
