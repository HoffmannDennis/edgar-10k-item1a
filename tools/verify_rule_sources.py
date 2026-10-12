"""Standalone source identity check; no obligation decision or downloads."""

import argparse
import hashlib
import json
from pathlib import Path


MANIFEST = Path(__file__).resolve().parents[1] / "src/edgar_10k_item1a/obligation/source_manifest.json"


def verify(sources: Path, manifest: dict) -> dict:
    rows = []
    for source in manifest["sources"]:
        filename = source["filename"]
        if Path(filename).name != filename:
            raise ValueError("manifest filenames must be single path components")
        path = sources / filename
        observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append({"id": source["id"], "filename": filename,
                     "expected_sha256": source["sha256"], "observed_sha256": observed,
                     "status": "ok" if observed == source["sha256"] else "mismatch"})
    return {"manifest_kind": manifest["manifest_kind"],
            "manifest_version": manifest["manifest_version"], "sources": rows,
            "all_sources_match": all(row["status"] == "ok" for row in rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    args = parser.parse_args()
    raw = MANIFEST.read_bytes()
    receipt = verify(args.sources, json.loads(raw))
    receipt["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["all_sources_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
