import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_rule_sources", ROOT / "tools/verify_rule_sources.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def test_source_identity_missing_valid_and_changed(tmp_path):
    source = {"id": "test", "filename": "synthetic_source.txt",
              "sha256": hashlib.sha256(b"synthetic").hexdigest()}
    manifest = {"manifest_kind": "test_only", "manifest_version": "test", "sources": [source]}
    assert not verifier.verify(tmp_path, manifest)["all_sources_match"]
    (tmp_path / source["filename"]).write_bytes(b"synthetic")
    assert verifier.verify(tmp_path, manifest)["all_sources_match"]
    (tmp_path / source["filename"]).write_bytes(b"changed")
    assert not verifier.verify(tmp_path, manifest)["all_sources_match"]


def test_bundled_manifest_scope_and_corrected_locator():
    manifest = json.loads(verifier.MANIFEST.read_bytes())
    assert manifest["manifest_kind"] == "bundled"
    assert len(manifest["sources"]) == 3
    post = manifest["decision_dependencies"]["R7_ANNUAL_ITEM1A_REQUIRED_from_2008_02_04"]
    assert "release_33_8876" in post
    locators = manifest["sources"][2]["locators"]
    assert any("page 210: Form 10-K" in locator for locator in locators)
    assert manifest["currency"]["assessed_through_with_src_exception"] == "2008-02-04"
