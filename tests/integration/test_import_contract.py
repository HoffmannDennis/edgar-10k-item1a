"""Import hygiene in an isolated interpreter; no external inputs or network."""

import os
from pathlib import Path
import subprocess
import sys


def test_import_without_private_environment_or_configuration_io():
    src = Path(__file__).resolve().parents[2] / "src"
    program = r'''
import pathlib, socket, sys, threading
sys.path.insert(0, sys.argv[1])
before = (sys.getrecursionlimit(), threading.stack_size())
def audit(event, args):
    if event.startswith("socket."):
        raise AssertionError("network during import: " + event)
    if event == "open":
        path = str(args[0])
        if path.endswith((".json", ".toml", ".csv", ".parquet", ".env")):
            raise AssertionError("configuration/data read during import: " + path)
        if isinstance(args[1], str) and any(c in args[1] for c in "wa+"):
            raise AssertionError("write during import: " + path)
sys.addaudithook(audit)
import edgar_10k_item1a
assert (sys.getrecursionlimit(), threading.stack_size()) == before
assert "polars" not in sys.modules
assert callable(edgar_10k_item1a.extract_item1a)
assert not hasattr(edgar_10k_item1a, "evaluate_obligation")
assert not hasattr(edgar_10k_item1a, "extract_cover")
print("isolated offline import: no data/config reads or global-setting changes")
'''
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith(("SEC_", "PYTHON"))}
    result = subprocess.run([sys.executable, "-B", "-I", "-c", program, str(src)],
                            env=environment, text=True, capture_output=True, check=True)
    assert "isolated offline import" in result.stdout
