import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import stage_summary


def main() -> int:
    p = argparse.ArgumentParser(description="Inspect a pipeline run")
    p.add_argument("run_id")
    p.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = p.parse_args()
    path = args.runs_dir / args.run_id / "manifest.json"
    if not path.exists():
        p.error(f"manifest not found: {path}")
    print(stage_summary(path))
    print(f"Manifest: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
