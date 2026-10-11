"""Interactive, local-only configuration. Never tests or sends a model request."""

from getpass import getpass
from .config import save_settings


def main():
    print("LoveShe 模型配置；只写入本机，不连接模型。")
    backend = input("入口 api / codex [api]：").strip() or "api"
    if backend not in {"api", "codex"}:
        raise SystemExit("入口必须是 api 或 codex。")
    base = input("兼容 Chat Completions 的 API 地址（通常以 /v1 结尾）：").strip() if backend == "api" else ""
    model = input("模型名称（按服务商或本机 CLI 可用名称填写）：").strip()
    key = getpass("API 密钥（输入不显示）：").strip() if backend == "api" else ""
    try:
        path = save_settings({"backend": backend, "api_base": base, "model": model, "api_key": key})
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"配置已保存到 {path}。请重启 LoveShe。环境变量会覆盖此配置。")


if __name__ == "__main__":
    main()
