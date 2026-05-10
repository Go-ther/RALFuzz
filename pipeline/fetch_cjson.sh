#!/usr/bin/env bash
set -euo pipefail

VERSION="${CJSON_VERSION:-v1.7.19}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${CJSON_OUTPUT_DIR:-${REPO_ROOT}/api/cJSON}"
ARCHIVE_URL="https://github.com/DaveGamble/cJSON/archive/refs/tags/${VERSION}.zip"

export VERSION OUTPUT_DIR ARCHIVE_URL REPO_ROOT

python_bin="${PYTHON_BIN:-python3}"
if ! command -v "${python_bin}" >/dev/null 2>&1; then
  python_bin="python"
fi

"${python_bin}" - <<'PY'
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

version = os.environ["VERSION"]
archive_url = os.environ["ARCHIVE_URL"]
repo_root = Path(os.environ["REPO_ROOT"]).resolve()
output_dir = Path(os.environ["OUTPUT_DIR"]).resolve()
api_root = (repo_root / "api").resolve()

try:
    output_dir.relative_to(api_root)
except ValueError as exc:
    raise SystemExit(f"OUTPUT_DIR must be under {api_root}") from exc

required = ["cJSON.c", "cJSON.h", "cJSON_Utils.c", "cJSON_Utils.h"]
with tempfile.TemporaryDirectory(prefix="ralfuzz-cjson-") as temp:
    temp_path = Path(temp)
    zip_path = temp_path / "cjson.zip"
    extract_dir = temp_path / "extract"
    extract_dir.mkdir()

    print(f"Downloading cJSON {version} from {archive_url}")
    urllib.request.urlretrieve(archive_url, zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)

    dirs = [path for path in extract_dir.iterdir() if path.is_dir()]
    if not dirs:
        raise SystemExit("Downloaded archive did not contain a source directory.")
    source_dir = dirs[0]

    output_dir.mkdir(parents=True, exist_ok=True)
    for name in required:
        src = source_dir / name
        if not src.exists():
            raise SystemExit(f"Missing expected file in cJSON archive: {name}")
        shutil.copy2(src, output_dir / name)

print(f"Fetched cJSON {version} into {output_dir}")
print("cJSON is an external MIT-licensed target input from https://github.com/DaveGamble/cJSON")
PY
