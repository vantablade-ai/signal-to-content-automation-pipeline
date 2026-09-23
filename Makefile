.PHONY: install test check demo run-demo inspect-demo demo-media

install:
	python3 -m venv .venv
	.venv/bin/python -m pip install -r requirements-dev.txt

test:
	.venv/bin/python -m pytest -q

check:
	.venv/bin/ruff check .
	.venv/bin/python -m compileall -q app tests scripts
	.venv/bin/python -m pytest -q

demo:
	.venv/bin/python scripts/demo.py

run-demo:
	.venv/bin/python scripts/run_pipeline.py --run-id demo

inspect-demo:
	.venv/bin/python scripts/inspect_run.py demo

demo-media:
	.venv/bin/python scripts/run_pipeline.py --run-id media-demo --renderer ffmpeg
