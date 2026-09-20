#!/usr/bin/env python3
"""Recreate, never relocate, the local venv from an offline package snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import tarfile
import venv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    target = root / ".venv"
    info = json.loads((root / "ENVIRONMENT.json").read_text())
    if sys.version_info[:2] != (3, 12) or platform.machine() != info["architecture"]:
        raise SystemExit("需要与记录兼容的 Python 3.12 和本机架构；没有修改环境。")
    if target.exists():
        raise SystemExit(".venv 已存在；为保护环境，不覆盖。请在新的恢复目录操作。")
    if hashlib.sha256(args.packages.read_bytes()).hexdigest() != info["packageArchiveSha256"]:
        raise SystemExit("离线依赖包校验不符；没有修改环境。")
    with tarfile.open(args.packages) as archive:
        members = archive.getmembers()
        if any(m.issym() or m.islnk() or m.name.startswith("/") or ".." in Path(m.name).parts for m in members):
            raise SystemExit("依赖归档存在不允许的路径或链接。")
        venv.EnvBuilder(with_pip=False, symlinks=False).create(target)
        site = target / "lib/python3.12/site-packages"
        archive.extractall(site, filter="data")
    print(f"已在新路径创建独立环境：{target}；没有安装新版本或修改全局环境。")


if __name__ == "__main__":
    main()
