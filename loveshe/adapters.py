"""Data ingress contracts shared by import, future listeners, and OCR."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol
import json
import os
import re
import shutil
import subprocess


@dataclass(frozen=True)
class IngressBatch:
    contact_id: str
    source: str
    event_id: str
    messages: list[dict]
    user_confirmed: bool = False


class LiveSource(Protocol):
    """Future Windows listener/OCR implementations emit batches to the service.

    OCR must require contact confirmation and mark uncertain timestamps/senders.
    Sources never call models or write memories themselves.
    """
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def poll(self) -> list[IngressBatch]: ...


CAPABILITIES = [
    {"id": "weflow", "name": "微信手动更新", "enabled": True},
    {"id": "listener", "name": "增量监听（轮询）", "enabled": True},
    {"id": "ocr", "name": "读屏 / OCR", "enabled": False},
    {"id": "overlay", "name": "悬浮窗", "enabled": False},
    {"id": "auto_memory", "name": "自动写入与压缩记忆", "enabled": True},
]


class WeFlow:
    def __init__(self, root: Path):
        self.root = root

    def run(self, args: list[str], timeout=180):
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise ValueError("未找到 npm，请先安装 Node.js。")
        result = subprocess.run(
            [npm, "exec", "--yes", "--package=weflow-cli@1.9.0", "--", "weflow-cli", *args],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise ValueError("WeFlow 未能完成操作。请确认微信已登录且初始化成功，再重试。")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ValueError("WeFlow 返回了无法识别的结果。") from None
        if data.get("success") is False:
            raise ValueError("WeFlow 报告失败，请重新检查本机微信连接。")
        return data

    def sessions(self):
        data = self.run(["sessions", "-n", "1000", "--json"])
        return [{"id": s["username"], "name": s.get("displayName") or s["username"]}
                for s in data.get("sessions", []) if s.get("type") == 0]

    def incremental(self, external_id: str, start: int, limit=500):
        if not re.fullmatch(r"[\w@.\-]+", external_id):
            raise ValueError("无效的微信联系人标识。")
        data = self.run(["messages", external_id, "-s", str(start), "-n", str(limit),
                         "--json", "--non-interactive"])
        rows = data.get("messages")
        if not isinstance(rows, list):
            raise ValueError("微信增量读取返回格式无效。")
        # Full pages must not advance a cursor: use the complete export path.
        if len(rows) >= limit:
            raise OverflowError("增量窗口超过单批上限，需要完整导出补齐。")
        return {"source": "weflow-live", "messages": rows}

    def export(self, external_id: str, start: str, end: str) -> Path:
        if not re.fullmatch(r"[\w@.\-]+", external_id):
            raise ValueError("无效的微信联系人标识。")
        if date.fromisoformat(start) > date.fromisoformat(end):
            raise ValueError("开始日期不能晚于结束日期。")
        target = self.root / "raw"
        target.mkdir(parents=True, exist_ok=True)
        result = self.run(["export", external_id, "json", "--from", start, "--to", end,
                           "--contract", "weflow-v1", "--output", str(target),
                           "--json", "--non-interactive"], timeout=300)
        path = Path(result.get("path", "")).resolve()
        if not path.is_relative_to(target.resolve()) or not path.is_file():
            raise ValueError("导出器未返回有效的本地文件。")
        return path
