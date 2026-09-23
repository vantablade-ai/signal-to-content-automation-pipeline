import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_run_inspect_resume(tmp_path):
    base = [sys.executable, str(ROOT / "scripts/run_pipeline.py"), "--run-id", "cli",
            "--runs-dir", str(tmp_path)]
    assert subprocess.run(base, cwd=ROOT, capture_output=True, check=False).returncode == 0
    inspect = subprocess.run([sys.executable, str(ROOT / "scripts/inspect_run.py"), "cli",
                              "--runs-dir", str(tmp_path)], cwd=ROOT, capture_output=True, text=True, check=False)
    assert inspect.returncode == 0 and "PACKAGE" in inspect.stdout
    resume = subprocess.run([sys.executable, str(ROOT / "scripts/resume_run.py"), "cli",
                             "--runs-dir", str(tmp_path)], cwd=ROOT, capture_output=True, text=True, check=False)
    assert resume.returncode == 0 and "reused': 10" in resume.stdout
