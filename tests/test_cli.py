import os
import shutil
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


def test_pytest_invocations_collect_from_repository_root_without_pythonpath():
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    commands = [[sys.executable, "-m", "pytest"], [shutil.which("pytest") or "pytest"]]
    for command in commands:
        result = subprocess.run(command + ["--collect-only", "-q", "tests/test_pipeline.py"],
                                cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
