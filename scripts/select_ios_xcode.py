"""Select an installed Xcode meeting App Store Connect's SDK requirement."""

import os
import re
import subprocess
from pathlib import Path


def main():
    candidates = []
    for application in Path("/Applications").glob("Xcode*.app"):
        developer = application / "Contents" / "Developer"
        if not developer.is_dir():
            continue
        command = subprocess.run([str(developer / "usr/bin/xcodebuild"), "-version"], capture_output=True, text=True)
        match = re.search(r"Xcode (\d+(?:\.\d+)*)", command.stdout)
        if command.returncode == 0 and match:
            version = tuple(int(part) for part in match[1].split("."))
            if version[0] >= 26:
                candidates.append((version, str(developer.resolve())))
    if not candidates:
        raise SystemExit("No Xcode 26+ installed. Use a macOS runner with the current iOS SDK.")
    version, developer = max(candidates)
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as destination:
        destination.write("DEVELOPER_DIR=" + developer + "\n")
    print("Selected Xcode " + ".".join(map(str, version)))


if __name__ == "__main__":
    main()
