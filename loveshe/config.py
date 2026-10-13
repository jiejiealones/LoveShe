"""Local model configuration; environment variables override the private file."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


ENV_KEYS = {"backend": "LOVESHE_BACKEND", "api_base": "LOVESHE_API_BASE",
            "model": "LOVESHE_MODEL", "api_key": "LOVESHE_API_KEY"}  # pragma: allowlist secret -- environment variable name


def config_path():
    from .core import data_path
    return data_path() / "model_config.json"


def validate(settings, require_ready=False):
    if settings["backend"] not in {"api", "codex"}:
        raise ValueError("模型入口必须是 api 或 codex。")
    base = settings["api_base"]
    if base:
        parsed = urlsplit(base)
        if (not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("API 地址不能包含凭证、查询参数或片段。")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("外部 API 地址必须使用 HTTPS。")
    if require_ready and (not settings["model"] or (settings["backend"] == "api" and (not base or not settings["api_key"]))):
        raise ValueError("尚未配置模型。请运行 python -m loveshe.configure，然后重启服务。")
    return settings


def model_settings():
    path = config_path()
    settings = {"backend": "api", "api_base": "", "model": "", "api_key": ""}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            raise ValueError("本机模型配置无法读取，请重新运行 python -m loveshe.configure。") from None
        if not isinstance(raw, dict) or set(raw) - set(ENV_KEYS) or any(not isinstance(v, str) for v in raw.values()):
            raise ValueError("本机模型配置格式无效。")
        settings.update(raw)
    for name, env in ENV_KEYS.items():
        if env in os.environ:
            settings[name] = os.environ[env]
    return validate({k: v.strip() for k, v in settings.items()})


def public_model_info():
    settings = model_settings()
    return {"backend": settings["backend"], "model": settings["model"] or "未配置",
            "endpoint": "当前 ChatGPT 账号 · Codex CLI" if settings["backend"] == "codex" else settings["api_base"] or "未配置",
            "configured": bool(settings["model"] and (settings["backend"] == "codex" or (settings["api_base"] and settings["api_key"]))),
            "external": True}


def save_settings(settings):
    if set(settings) != set(ENV_KEYS) or any(not isinstance(v, str) for v in settings.values()):
        raise ValueError("模型配置字段无效。")
    validate(settings, require_ready=True)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
