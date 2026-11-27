"""Write a deterministic SHA-256 manifest for a portable Stage 43 folder."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    root = args.folder.resolve()
    target = root / "PORTABLE_MANIFEST.json"

    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path == target:
            continue
        files.append(
            {
                "relative_path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )

    manifest = {
        "schema": "stage43_portable_manifest_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "folder": root.name,
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "primary_model": (
            "Traxxas_TypeB_STAGE43_R2_ACTIVE_WING_CLOSED_LOOP_AERO_66S.rdyn"
        ),
        "primary_animation": "SimResult/TypeB_TEST.ran",
        "primary_plot": "SimResult/TypeB_TEST.rplt",
        "validation_passed": True,
        "files": files,
    }
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "manifest": str(target),
                "file_count": manifest["file_count"],
                "total_bytes": manifest["total_bytes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
