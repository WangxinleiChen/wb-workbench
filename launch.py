#!/usr/bin/env python3
"""Open this copy of the local workbench, reusing its running server if possible."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser


def main():
    root = Path(__file__).resolve().parent
    data = str(root / "data")
    for port in range(8765, 8785):
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=0.5) as response:
                health = json.load(response)
            if health.get("application") == "WBWorkbench" and health.get("dataDir") == data:
                print(f"已打开本地工作台：{url}", flush=True)
                webbrowser.open(url)
                return 0
        except (OSError, ValueError, urllib.error.URLError):
            pass
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        except OSError:
            continue
        return subprocess.call([sys.executable, str(root / "server.py"), "--port", str(port), "--open"])
    print("本地端口 8765–8784 均不可用。请关闭旧服务，或用 server.py --port 指定其他端口。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
