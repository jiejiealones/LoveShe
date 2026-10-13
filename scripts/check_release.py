"""Check public candidates, without reading ignored private directories."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = {".gitignore", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
              "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md", "pyproject.toml",
              "config.example.json", "启动LoveShe.cmd", "配置模型.cmd"}
FOLDERS = {"loveshe", "tests", "examples", "docs", "scripts", ".github", "ios"}
SUFFIXES = {".py", ".md", ".json", ".html", ".css", ".js", ".ps1", ".yml", ".yaml",
            ".swift", ".pbxproj", ".xcscheme", ".xcprivacy"}
PUBLIC_IMAGES = {"docs/assets/banner.svg", "docs/assets/workflow.svg",
                 "docs/assets/demo-overview.jpg", "docs/assets/demo-history.jpg",
                 "docs/assets/demo-replies.jpg", "docs/assets/demo-preview.jpg",
                 "ios/LoveShe/Assets.xcassets/AppIcon.appiconset/AppIcon.png"}


def allowed(path):
    parts = path.parts
    return (path.as_posix() in ROOT_FILES or path.as_posix() in PUBLIC_IMAGES or (parts[0] in FOLDERS and path.suffix in SUFFIXES
            and not any(p in {"__pycache__", ".pytest_cache", "xcuserdata", "DerivedData"} for p in parts)
            and path.name not in {"model_config.json", "migrate_project_data.py", "recover_reference_errors.py"}))


def public_files(root=ROOT):
    files = []
    for name in sorted(ROOT_FILES):
        if not (root / name).is_file():
            raise ValueError(f"Missing release file: {name}")
        files.append(Path(name))
    for folder in sorted(FOLDERS):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError(f"External or linked public file: {path.name}")
                relative = path.relative_to(root)
                if not allowed(relative):
                    raise ValueError(f"Unexpected public file: {relative.as_posix()}")
                files.append(relative)
    return sorted(files)


def check(root=ROOT):
    files = public_files(root)
    findings = []
    patterns = [r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                r"\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_]{30,}",
                r"[A-Za-z]:[/\\]Users[/\\][^\s/\\]+",
                r"\bwxid_[A-Za-z0-9]{8,}"]
    for relative in files:
        if relative.as_posix() in PUBLIC_IMAGES and relative.suffix == ".png":
            content = (root / relative).read_bytes()
            if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) > 2_000_000:
                findings.append(f"Invalid app icon: {relative}")
            continue
        if relative.as_posix() in PUBLIC_IMAGES and relative.suffix == ".jpg":
            content = (root / relative).read_bytes()
            if not content.startswith(b"\xff\xd8\xff") or len(content) > 2_000_000:
                findings.append(f"Invalid release screenshot: {relative}")
            # Binary screenshots need manual visual privacy review, not text scanning.
            continue
        try:
            content = (root / relative).read_text(encoding="utf-8-sig")
        except UnicodeError:
            findings.append(f"Non-text file: {relative}")
            continue
        if any(re.search(pattern, content) for pattern in patterns):
            findings.append(f"Potential private data: {relative}")
    if (root / ".git").exists():
        result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                                cwd=root, capture_output=True, check=True)
        for name in result.stdout.decode("utf-8").split("\0"):
            if name and not allowed(Path(name)):
                findings.append(f"Unexpected Git candidate: {name}")
    if findings:
        raise ValueError("\n".join(findings))
    return files


if __name__ == "__main__":
    try:
        files = check()
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None
    print(f"PASS: {len(files)} public files; private directories excluded. Screenshots require manual review; secret scanning still required.")
