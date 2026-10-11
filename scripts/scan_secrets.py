"""Scan only public source with detect-secrets; disable network verification."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from check_release import ROOT, check


def main():
    files = check()
    with tempfile.TemporaryDirectory(prefix="loveshe-public-scan-") as temporary:
        directory = Path(temporary)
        for relative in files:
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        result = subprocess.run([sys.executable, "-m", "detect_secrets", "scan", "--all-files", "--no-verify", "."],
                                cwd=directory, capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise SystemExit("Secret scanner failed. Install development tool: python -m pip install detect-secrets==1.5.0")
        report = json.loads(result.stdout)
        findings = report.get("results", {})
        if findings:
            # Print locations only; never echo suspected credentials.
            for name, items in findings.items():
                for item in items:
                    print(f"Potential secret: {name}:{item['line_number']} ({item['type']})")
            raise SystemExit(1)
        print(f"PASS: detect-secrets {report['version']}; {len(files)} public files; no findings; network verification disabled.")


if __name__ == "__main__":
    main()
