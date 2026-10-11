"""Fictional offline-first demo, isolated from the normal workspace database."""

import argparse
import os
from pathlib import Path

from .core import add_memory, data_path, import_json, open_db


def seed(root, examples):
    db = open_db(root)
    try:
        for cid, name in [("demo-a", "示例联系人 A"), ("demo-b", "示例联系人 B")]:
            import_json(db, examples / f"{cid}.json", cid, name)
            if not db.execute("SELECT 1 FROM memories WHERE contact_id=?", (cid,)).fetchone():
                add_memory(db, cid, "background", "完全虚构的演示联系人，不对应真实人物。", "fact", [])
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="LoveShe 虚构演示（不自动调用模型）")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parent.parent
    examples = project / "examples"
    if not examples.is_dir():
        raise SystemExit("请从完整源码工程运行演示，或自行导入示例 JSON。")
    root = project / "data" / "demo"
    for key in ("LOVESHE_BACKEND", "LOVESHE_API_BASE", "LOVESHE_MODEL", "LOVESHE_API_KEY"):
        os.environ.pop(key, None)
    os.environ["LOVESHE_DATA_DIR"] = str(root)
    seed(root, examples)
    from .server import serve
    import webbrowser
    server = serve(root, args.port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"虚构演示：{url}；数据：{data_path()}；不会自动调用模型。", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    # The demo deliberately does not start automation.
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.app.pool.shutdown(wait=False)


if __name__ == "__main__":
    main()
