#!/usr/bin/env python3
"""Fixed-port development entry; never reuse a Standard process or data path."""
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import webbrowser


def main():
    root = Path(__file__).resolve().parent
    data = root / 'data'
    url = 'http://127.0.0.1:8766'
    print('WB Workbench v1.1.0-dev · 开发版 / 背景校正功能测试', flush=True)
    try:
        with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
            health = json.load(response)
        if health.get('application') != 'WBWorkbenchDev' or health.get('dataDir') != str(data):
            raise SystemExit('8766 已被其他服务占用；未连接或停止其他服务。')
        webbrowser.open(url)
        return 0
    except (OSError, ValueError, urllib.error.URLError):
        pass
    os.chdir(root)
    os.execv(sys.executable, [sys.executable, str(root / 'server.py'), '--port', '8766', '--data-dir', str(data), '--open', '--verbose'])


if __name__ == '__main__':
    raise SystemExit(main())
