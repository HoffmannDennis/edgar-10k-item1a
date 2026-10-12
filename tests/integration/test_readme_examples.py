"""The README quickstart stays executable and written for public readers."""

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_quickstart_example_runs_offline_and_matches_the_readme_line():
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "edgar_10k_item1a.cli", "extract", "--input", "examples/synthetic-10k.htm"],
        cwd=ROOT, capture_output=True, text=True, check=True, env={"PYTHONPATH": str(ROOT / "src")})
    line = completed.stderr.strip()
    assert line == "present_primary tier=primary-canonical quality=PASS_SHORT flags=- chars=1852"
    assert line in (ROOT / "README.md").read_text()
    assert '"text_status": "present_primary"' in completed.stdout
    assert "Investing in our common stock involves risks." in completed.stdout


def test_readme_is_written_for_public_readers():
    readme = (ROOT / "README.md").read_text()
    assert not re.search(r"(?i)\bprivate\b", readme)
    assert not re.search(r"/home/|/Users/", readme)
