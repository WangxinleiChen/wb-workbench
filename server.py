#!/usr/bin/env python3
"""WB Workbench: loopback-only, persistent image measurement workbench."""
from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image, ImageDraw, ImageFont

import analysis
import exports

ROOT = Path(__file__).resolve().parent
VERSION = "1.0.0"
ROLES = ("pho", "total")
MAX_UPLOAD = 65 * 1024 * 1024
METHOD = ("矩形选区的原始像素积分；局部背景假设近似恒定。暗条带净信号 = "
          "条带像素数 × 背景均值 − 条带像素和；亮条带取反。R=P净/T净；"
          "相对值=R/本次实验指定对照R的算术均值。双图确认只表示人工复核，不证明实验线性或跨膜可比性。")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.images_dir = self.directory / "images"
        self.images_dir.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.path = self.directory / "state.json"
        self.audit_path = self.directory / "audit.jsonl"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"version": 1, "experiments": []}

    def persist(self, action, exp, details=None):
        exp["updatedAt"] = now()
        exp["revision"] += 1
        event = {"time": now(), "experimentId": exp["id"], "revision": exp["revision"],
                 "action": action, "details": details or {}}
        # Write state atomically. Every state contains the event sequence so an interrupted
        # JSONL append cannot make the saved project lose its own history.
        self.state.setdefault("audit", []).append(event)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(json_bytes(self.state))
        os.replace(tmp, self.path)
        with self.audit_path.open("ab") as stream:
            stream.write(json_bytes(event) + b"\n")

    def get(self, eid):
        for exp in self.state["experiments"]:
            if exp["id"] == eid:
                return exp
        raise KeyError("实验不存在")

    def create(self, name, sample_count=6, control_count=3):
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("请填写 1–120 字的实验名称")
        n, controls = int(sample_count), int(control_count)
        if not 1 <= n <= 32 or not 0 <= controls <= n:
            raise ValueError("泳道数需为 1–32，对照数不能超过泳道数")
        samples = [{"id": uid("s"), "name": f"Ctr{i+1}" if i < controls else f"Trt{i-controls+1}",
                    "group": "Ctr" if i < controls else "Trt", "control": i < controls} for i in range(n)]
        exp = {"id": uid("exp"), "name": name.strip(), "createdAt": now(), "updatedAt": now(),
               "revision": 0, "notes": "", "samples": samples,
               "images": {r: None for r in ROLES}, "rois": {r: [] for r in ROLES},
               "settings": {r: {"polarity": "dark", "region": None} for r in ROLES}}
        self.state["experiments"].append(exp)
        self.persist("创建实验", exp, {"samples": samples})
        return exp

    def image_path(self, image):
        p = self.images_dir / image["id"] / "original"
        if not p.is_file():
            raise ValueError("原图文件缺失，请从备份恢复此分析项目")
        return p

    def upload(self, exp, role, filename, payload):
        if role not in ROLES:
            raise ValueError("未知图像角色")
        if not payload or len(payload) > 45 * 1024 * 1024:
            raise ValueError("单张图像需小于 45 MB")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".tif", ".tiff", ".jpg", ".jpeg", ".png", ".bmp"}:
            raise ValueError("支持 TIFF、JPG、PNG 和 BMP 文件")
        iid = uid("img")
        folder = self.images_dir / iid
        folder.mkdir()
        original = folder / "original"
        original.write_bytes(payload)
        try:
            metadata = analysis.write_preview(original, folder / "preview.png")
            if metadata["format"] not in {"TIFF", "JPEG", "PNG", "BMP"}:
                raise ValueError("文件内容不是支持的图像格式")
        except Exception:
            # Only these just-created files are removed on an invalid upload.
            for p in folder.iterdir():
                p.unlink()
            folder.rmdir()
            raise
        image = {**metadata, "id": iid, "filename": Path(filename).name,
                 "previewUrl": f"/images/{iid}/preview.png", "originalUrl": f"/images/{iid}/original"}
        before = exp["images"][role]
        exp["images"][role] = image
        exp["rois"][role] = []
        exp["settings"][role]["region"] = None
        self.persist("导入图像", exp, {"role": role, "previousImage": before, "image": image})
        return exp

    def update(self, exp, incoming):
        if incoming.get("revision") != exp["revision"]:
            raise Conflict("这条实验记录已更新。请刷新后重试，避免覆盖新的修改。")
        name = incoming.get("name", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("实验名称需为 1–120 字")
        samples = incoming.get("samples", [])
        if not isinstance(samples, list) or not 1 <= len(samples) <= 32:
            raise ValueError("实验需要 1–32 个样本")
        ids = set()
        clean_samples = []
        for s in samples:
            sid = s.get("id")
            if not isinstance(sid, str) or sid in ids or not sid or len(sid) > 80:
                raise ValueError("样本编号重复或无效")
            ids.add(sid)
            sn, group = str(s.get("name", "")).strip(), str(s.get("group", "")).strip()
            if not sn or len(sn) > 100 or len(group) > 100:
                raise ValueError("请填写样本名称；名称与分组最多 100 字")
            clean_samples.append({"id": sid, "name": sn, "group": group, "control": bool(s.get("control"))})
        notes = incoming.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValueError("实验备注最多 10000 字")
        candidate = copy.deepcopy(exp)
        candidate.update(name=name.strip(), notes=notes, samples=clean_samples)
        old_sample_ids = {s["id"] for s in exp["samples"]}
        for role in ROLES:
            image = exp["images"][role]
            setting = incoming.get("settings", {}).get(role, exp["settings"][role])
            polarity = setting.get("polarity", "dark")
            if polarity not in ("dark", "bright"):
                raise ValueError("条带极性只能是 dark 或 bright")
            region = setting.get("region")
            if region is not None:
                if not image:
                    raise ValueError("请先导入图像")
                region = analysis.validate_rect(region, image["width"], image["height"])
            clean_setting = {"polarity": polarity, "region": region}
            settings_changed = clean_setting != exp["settings"][role]
            old_rois = {r["sampleId"]: r for r in exp["rois"][role]}
            rois, seen = [], set()
            for roi in incoming.get("rois", {}).get(role, []):
                sid = roi.get("sampleId")
                if sid not in ids or sid in seen or not image:
                    raise ValueError("选区样本配对无效或重复")
                seen.add(sid)
                band = analysis.validate_rect(roi["band"], image["width"], image["height"])
                background = analysis.validate_rect(roi["background"], image["width"], image["height"])
                old = old_rois.get(sid)
                changed = not old or old["band"] != band or old["background"] != background
                confirmed = bool(roi.get("confirmed")) and not (changed or settings_changed or sid not in old_sample_ids)
                rois.append({"sampleId": sid, "band": band, "background": background, "confirmed": confirmed,
                             **({"suggestionNote": roi["suggestionNote"]} if roi.get("suggestionNote") else {})})
            candidate["rois"][role] = rois
            candidate["settings"][role] = clean_setting
        previous = {k: copy.deepcopy(exp[k]) for k in ("name", "notes", "samples", "settings", "rois")}
        exp.update(candidate)
        self.persist("编辑与复核", exp, {"before": previous,
                                         "after": {k: copy.deepcopy(exp[k]) for k in previous}})
        return exp

    def suggest(self, exp, role, request):
        if role not in ROLES or not exp["images"][role]:
            raise ValueError("请先导入该图像")
        image = exp["images"][role]
        region = analysis.validate_rect(request.get("region"), image["width"], image["height"])
        polarity = request.get("polarity", "dark")
        if polarity not in ("dark", "bright"):
            raise ValueError("未知条带极性")
        rois = analysis.suggest_rois(self.image_path(image), region, exp["samples"], polarity)
        exp["settings"][role] = {"polarity": polarity, "region": region}
        exp["rois"][role] = rois
        self.persist("生成候选选区", exp, {"role": role, "region": region, "polarity": polarity, "rois": rois})
        return exp

    def example(self):
        for exp in self.state["experiments"]:
            if exp.get("exampleKey") == "wbtest-v1":
                return exp
        paths = [ROOT / "examples" / n for n in ("PHO.png", "total.png")]
        if not all(p.exists() for p in paths):
            raise ValueError("未找到随软件附带的样例图像")
        exp = self.create("WBTEST · 截图样例")
        exp["exampleKey"] = "wbtest-v1"
        exp["notes"] = "用户提供的两张截图。每组三份不同样本；按同名样本配对。用于流程与测量复核，不是已验证的生物学定量结论。"
        for role, path in zip(ROLES, paths):
            self.upload(exp, role, path.name, path.read_bytes())
            # The user supplied cropped strips with a black frame. These broad analysis
            # regions exclude that frame; band positions are detected, not precomputed.
            region = ({"x": 16, "y": 15, "w": 423, "h": 43} if role == "pho"
                      else {"x": 13, "y": 15, "w": 426, "h": 36})
            self.suggest(exp, role, {"region": region, "polarity": "dark"})
        return exp

    def results(self, exp):
        maps = {role: {r["sampleId"]: r for r in exp["rois"][role]} for role in ROLES}
        rows = []
        for sample in exp["samples"]:
            row = {"recordId": exp["id"] + ":" + sample["id"], "experimentId": exp["id"],
                   "experimentName": exp["name"], "sampleId": sample["id"], "sampleName": sample["name"],
                   "group": sample["group"], "control": sample["control"], "ratio": None, "relative": None,
                   "confirmed": False, "status": "等待选区", "warnings": []}
            for role in ROLES:
                roi, image = maps[role].get(sample["id"]), exp["images"][role]
                row[role + "Roi"], row[role + "Image"], row[role] = roi, image, None
                if image:
                    row["warnings"].extend(image.get("warnings", []))
                if roi and image:
                    measured = analysis.measure_roi(self.image_path(image), roi, exp["settings"][role]["polarity"])
                    for other in exp["rois"][role]:
                        if other["sampleId"] == sample["id"]:
                            continue
                        if analysis.rects_overlap(roi["band"], other["band"]):
                            measured["valid"] = False
                            measured["warnings"].append("同图不同样本的条带框重叠，请调整")
                        if analysis.rects_overlap(roi["background"], other["band"]):
                            measured["valid"] = False
                            measured["warnings"].append("背景框覆盖其他样本的条带，请调整")
                    row[role] = measured
                    row["warnings"].extend(measured.get("warnings", []))
            p, t = row["pho"], row["total"]
            if p is not None and t is not None:
                row["confirmed"] = bool(row["phoRoi"]["confirmed"] and row["totalRoi"]["confirmed"])
                if not p["valid"] or not t["valid"]:
                    row["status"] = "选区或信号需复核"
                elif not row["confirmed"]:
                    row["status"] = "待人工确认"
                elif t["net"] <= 0 or p["net"] <= 0:
                    row["status"] = "净信号非正，需复核"
                else:
                    row["ratio"] = p["net"] / t["net"]
                    row["status"] = "已确认"
            row["warnings"] = list(dict.fromkeys(row["warnings"]))
            rows.append(row)
        controls = [r for r in rows if r["control"]]
        ready = bool(controls) and all(r["ratio"] is not None and r["ratio"] > 0 for r in controls)
        mean = sum(r["ratio"] for r in controls) / len(controls) if ready else None
        warnings = []
        if not controls:
            warnings.append("本次实验没有指定对照。保留图像信号比，相对对照值留空。")
        elif not ready:
            warnings.append("本次实验的对照记录尚未全部通过复核，相对值暂不计算。")
        for row in rows:
            if row["ratio"] is not None:
                if ready:
                    row["relative"] = row["ratio"] / mean
                else:
                    row["status"] = "已确认 · 对照待复核" if controls else "已确认 · 无对照"
            row["warnings"].extend(warnings)
        warnings.append("p 与 total 来自不同膜；当前未提供各膜上样/转膜归一化证据。人工确认不等于定量适用性已验证。")
        return {"rows": rows, "controlMean": mean, "controlReady": ready, "warnings": warnings, "method": METHOD}

    def annotated(self, exp, role):
        image = exp["images"][role]
        if not image:
            raise ValueError("图像尚未导入")
        preview = Image.open(self.images_dir / image["id"] / "preview.png").convert("RGB")
        scale = max(1, min(4, int(1400 / max(1, preview.width))))
        canvas = Image.new("RGB", (preview.width * scale, preview.height * scale + 74), "#f5f7f6")
        canvas.paste(preview.resize((preview.width * scale, preview.height * scale), Image.Resampling.NEAREST), (0, 45))
        draw = ImageDraw.Draw(canvas)
        font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
        try:
            font = ImageFont.truetype(font_path, 14)
        except OSError:
            font = ImageFont.load_default()
        draw.text((8, 6), f"{exp['id']} | {role.upper()} | {image['filename']}", font=font, fill="#263d3a")
        samples = {s["id"]: s for s in exp["samples"]}
        for i, roi in enumerate(exp["rois"][role]):
            for key, color in (("band", "#078477"), ("background", "#d98b2b")):
                r = roi[key]
                box = (r["x"]*scale, 45+r["y"]*scale, (r["x"]+r["w"])*scale-1, 45+(r["y"]+r["h"])*scale-1)
                draw.rectangle(box, outline=color, width=2)
            label = f"{i+1}:{samples[roi['sampleId']]['name']} {'OK' if roi['confirmed'] else 'DRAFT'}"
            draw.text((roi["band"]["x"]*scale, 27), label, font=font, fill="#078477")
        draw.text((8, canvas.height-23), "Teal: band | Amber: local background | Display only; original pixels retained.", font=font, fill="#415b56")
        output = io.BytesIO()
        canvas.save(output, format="PNG")
        return output.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "WBWorkbench/1.0"

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    @property
    def store(self):
        return self.server.store

    def send_data(self, data, content_type="application/json; charset=utf-8", code=200, filename=None):
        if not isinstance(data, bytes):
            data = json_bytes(data)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def guard(self, mutation=False):
        host = self.headers.get("Host", "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            raise ValueError("仅允许本机访问")
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            raise ValueError("拒绝跨站请求")
        if mutation and not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("请求需要 application/json")

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_UPLOAD:
            raise ValueError("请求过大")
        value = json.loads(self.rfile.read(length)) if length else {}
        if not isinstance(value, dict):
            raise ValueError("请求必须为 JSON 对象")
        return value

    def handle_error(self, exc):
        if isinstance(exc, Conflict):
            self.send_data({"error": str(exc)}, code=409)
        elif isinstance(exc, KeyError):
            self.send_data({"error": str(exc)}, code=404)
        elif isinstance(exc, (ValueError, TypeError, OSError)):
            self.send_data({"error": str(exc)}, code=400)
        else:
            import traceback
            traceback.print_exc()
            self.send_data({"error": "操作未完成，请查看本地服务日志。原图保持不变。"}, code=500)

    def do_GET(self):
        try:
            self.guard()
            url = urlparse(self.path)
            path = unquote(url.path)
            parts = path.strip("/").split("/")
            if path == "/api/health":
                return self.send_data({"ok": True, "application": "WBWorkbench", "version": VERSION,
                                       "dataDir": str(self.store.directory)})
            with self.store.lock:
                if path == "/api/state":
                    return self.send_data({"version": 1, "experiments": self.store.state["experiments"]})
                if path == "/api/export":
                    query = parse_qs(url.query)
                    form = query.get("format", ["zip"])[0]
                    eid = query.get("experiment", [None])[0]
                    experiments = [self.store.get(eid)] if eid else self.store.state["experiments"]
                    if not experiments:
                        raise ValueError("尚无实验可导出")
                    payload, content_type, extension = exports.build_export(self.store, experiments, form)
                    return self.send_data(payload, content_type, filename=f"wb-results-{dt.datetime.now():%Y%m%d-%H%M%S}.{extension}")
                if len(parts) >= 4 and parts[:2] == ["api", "experiments"]:
                    exp = self.store.get(parts[2])
                    if parts[3] == "results":
                        return self.send_data(self.store.results(exp))
                    if parts[3] == "history":
                        return self.send_data({"events": [e for e in self.store.state.get("audit", []) if e["experimentId"] == exp["id"]]})
                    if parts[3] == "annotated" and len(parts) == 5 and parts[4] in ROLES:
                        return self.send_data(self.store.annotated(exp, parts[4]), "image/png")
            if len(parts) == 3 and parts[0] == "images":
                iid, file = parts[1:]
                if not iid.startswith("img_") or not iid[4:].isalnum() or file not in ("original", "preview.png"):
                    raise ValueError("图像路径无效")
                target = self.store.images_dir / iid / file
                if not target.is_file():
                    raise KeyError("图像不存在")
                return self.send_data(target.read_bytes(), "image/png" if file == "preview.png" else "application/octet-stream",
                                      filename=f"{iid}-original" if file == "original" else None)
            if path.startswith("/api/"):
                raise KeyError("接口不存在")
            asset = "index.html" if path == "/" else path.removeprefix("/static/").lstrip("/")
            target = (ROOT / "static" / asset).resolve()
            if not target.is_relative_to(ROOT / "static") or not target.is_file():
                raise KeyError("页面不存在")
            return self.send_data(target.read_bytes(), mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self):
        self.mutate("POST")

    def do_PUT(self):
        self.mutate("PUT")

    def mutate(self, method):
        try:
            self.guard(mutation=True)
            request = self.read_json()
            parts = urlparse(self.path).path.strip("/").split("/")
            with self.store.lock:
                if method == "POST" and parts == ["api", "experiments"]:
                    result = self.store.create(request.get("name", "新实验"), request.get("sampleCount", 6), request.get("controlCount", 3))
                elif method == "POST" and parts == ["api", "examples"]:
                    result = self.store.example()
                elif len(parts) >= 3 and parts[:2] == ["api", "experiments"]:
                    exp = self.store.get(parts[2])
                    if method == "PUT" and len(parts) == 3:
                        result = self.store.update(exp, request.get("experiment", {}))
                    elif method == "POST" and len(parts) == 5 and parts[3] == "images":
                        try:
                            payload = base64.b64decode(request.get("data", ""), validate=True)
                        except Exception:
                            raise ValueError("图像编码无效") from None
                        result = self.store.upload(exp, parts[4], request.get("filename", ""), payload)
                    elif method == "POST" and len(parts) == 5 and parts[3] == "suggest":
                        result = self.store.suggest(exp, parts[4], request)
                    else:
                        raise KeyError("接口不存在")
                else:
                    raise KeyError("接口不存在")
                self.send_data(result)
        except Exception as exc:
            self.handle_error(exc)


def main():
    parser = argparse.ArgumentParser(description="WB Workbench 本地分析工具")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.store = Store(args.data_dir)
    server.verbose = args.verbose
    url = f"http://127.0.0.1:{args.port}"
    print(f"WB Workbench {VERSION}  {url}\n数据保存在 {server.store.directory}\n按 Ctrl+C 停止本地服务。", flush=True)
    if args.open:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
