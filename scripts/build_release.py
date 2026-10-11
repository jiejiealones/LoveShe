"""Create an allowlisted source ZIP; never commit or upload."""

import hashlib
import json
import zipfile
from check_release import ROOT, check


def main():
    files = check()
    destination = ROOT / "release"
    destination.mkdir(exist_ok=True)
    target = destination / "LoveShe-0.1.0-source.zip"
    manifest = {}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            data = (ROOT / relative).read_bytes()
            archive.writestr("LoveShe-0.1.0/" + relative.as_posix(), data)
            manifest[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Created {target.name}: {len(files)} public files. No upload performed.")


if __name__ == "__main__":
    main()
