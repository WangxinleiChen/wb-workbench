"""Portable CSV/XLSX/ZIP exports with formula caches and original-image provenance.

OOXML is written with the standard library so the installed application needs no
spreadsheet runtime. Cached formula values are the same results as the UI.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import re
import zipfile
from xml.sax.saxutils import escape


SOFTWARE = "WB Workbench v1.1.0-dev"
DERIVED_FILES = ("manifest.json", "background.npy", "corrected.npy", "background.png", "corrected.png")
METHOD_HEADERS = ["定量模式", "背景来源", "背景贡献", "算法标识", "算法版本", "正式方法参数", "正式处理区域",
                  "正式派生结果键", "方法原图SHA256", "方法状态", "预览派生结果键", "预览参数", "预览处理区域",
                  "预览原图SHA256", "预览极性", "预览算法标识", "预览算法版本"]
MEASUREMENT_DEFINITION = (
    "模式 A（local）：亮条带净信号=条带原始像素和−条带像素数×局部背景均值（由背景框估计），暗条带取反。"
    "模式 B（model）：背景模型 B 与原图 I 使用相同原始强度单位；亮条带净信号=sum(I−B)，"
    "暗条带净信号=sum(B−I)，保留未截断数值。每幅图仅采用当前正式模式，模式 B 不再减局部背景框。"
    "R=P净/total净；相对值=R/本次实验指定对照 R 的算术均值。未确认或无效数值不发布比值。"
)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) if value is not None else None


def method_values(exp, row, role):
    """Describe the active calculation separately from a non-operative preview."""
    state = (exp.get("background") or {}).get(role) or {}
    measurement = row.get(role) or {}
    row_method = row.get(role + "Method")
    declared_mode = row_method.get("mode") if isinstance(row_method, dict) else row_method
    mode = state.get("mode") or declared_mode or measurement.get("method", "local")
    applied = state.get("applied") if "applied" in state else row.get(role + "Artifact")
    applied = applied or {}
    preview = state.get("preview") or {}
    image = row.get(role + "Image") or {}
    if mode == "model":
        source = measurement.get("backgroundSource") or applied.get("algorithm")
        algorithm = applied.get("algorithm") or source
        version = applied.get("algorithmVersion", measurement.get("algorithmVersion"))
        params = applied.get("params", measurement.get("parameters"))
        region = applied.get("region")
        key = applied.get("key")
        sha = applied.get("sourceSha256")
        status = ("正式应用" if measurement.get("rawSum") is not None else "正式应用；该样本不可测量") if applied else "模型未应用或已失效；不可计算"
    else:
        source, algorithm, version, params = "local-rectangle", "local-rectangle", 1, {}
        region, key, sha = None, None, image.get("sha256")
        status = "原方法生效"
    contribution = measurement.get("backgroundContribution")
    if contribution is None and mode == "local" and measurement.get("area") is not None and measurement.get("backgroundMean") is not None:
        contribution = measurement["area"] * measurement["backgroundMean"]
    return [mode, source, contribution, algorithm, version, json_text(params), json_text(region), key, sha, status,
            preview.get("key"), json_text(preview.get("params")), json_text(preview.get("region")),
            preview.get("sourceSha256"), preview.get("polarity"), preview.get("algorithm"), preview.get("algorithmVersion")]


def method_description(exp, result):
    row = result["rows"][0] if result["rows"] else {}
    states = []
    for role, label in (("pho", "P"), ("total", "total")):
        mode = method_values(exp, row, role)[0]
        states.append(label + "=" + ("模式 B（model）" if mode == "model" else "模式 A（local）"))
    return "当前正式模式：" + "；".join(states) + "。" + MEASUREMENT_DEFINITION


def clean(value):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))[:32000]


def numcol(number):
    result = ""
    while number:
        number, mod = divmod(number - 1, 26)
        result = chr(65 + mod) + result
    return result


def xmlcell(value, address, style=0):
    base = f'<c r="{address}" s="{style}"'
    if isinstance(value, tuple) and len(value) == 2:
        formula, cached = value
        if cached is None:
            return base + f' t="str"><f>{escape(formula)}</f><v></v></c>'
        return base + f'><f>{escape(formula)}</f><v>{cached:.15g}</v></c>'
    if value is None:
        return base + "/ >".replace(" ", "")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("导出中存在非有限数值")
        return base + f"><v>{value:.15g}</v></c>"
    text = "是" if value is True else "否" if value is False else clean(value)
    return base + f' t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def sheet_xml(rows, widths, freeze="D2"):
    content = []
    for i, values in enumerate(rows, 1):
        cells = []
        for j, value in enumerate(values, 1):
            style = 1 if i == 1 else 3 if isinstance(value, (float, tuple)) else 2
            cells.append(xmlcell(value, f"{numcol(j)}{i}", style))
        content.append(f'<row r="{i}" ht="{30 if i == 1 else 24}" customHeight="1">' + "".join(cells) + "</row>")
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane xSplit="3" ySplit="1" topLeftCell="' + freeze + '" activePane="bottomRight" state="frozen"/></sheetView></sheetViews>'
            '<sheetFormatPr defaultRowHeight="24"/><cols>' + cols + '</cols><sheetData>' + "".join(content) + '</sheetData>'
            f'<autoFilter ref="A1:{numcol(len(rows[0]))}{len(rows)}"/>'
            '<pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
            '<pageSetup orientation="landscape" paperSize="9" fitToWidth="1" fitToHeight="0"/></worksheet>')


STYLES = '''<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="0.000000"/></numFmts>
<fonts count="2"><font><sz val="11"/><color rgb="FF243D38"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF16766B"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''


def xlsx_bytes(store, experiments, analyses):
    headers = ["记录编号", "实验编号", "实验名称", "样本", "分组", "对照", "复核状态", "P 净信号", "total 净信号", "p/total 图像信号比", "本次对照 R 均值", "相对对照图像指标", "来源与复核说明", "数值发布条件满足", "P 定量模式", "total 定量模式", "P 方法状态", "total 方法状态"]
    main = [headers]
    details = [["记录编号", "图像角色", "样本", "原文件名", "图像编号", "SHA-256", "位深", "数值极性", "选区已确认", "条带框 x,y,w,h", "背景框 x,y,w,h", "条带像素数", "条带像素和", "背景均值", "背景像素数", "净信号", "端点像素数", "状态说明", "原图在归档内的位置"] + METHOD_HEADERS]
    methods = [["项目", "内容"], ["软件版本", SOFTWARE], ["导出时间 UTC", dt.datetime.now(dt.timezone.utc).isoformat()],
               ["测量定义", MEASUREMENT_DEFINITION],
               ["背景假设", "局部背景在条带区域内近似恒定。背景框应避开条带和污点；需人工复核。"],
               ["模式 A / local", "保留原有局部背景框扣除。亮条带：原始积分−面积×局部背景均值；暗条带取反。"],
               ["模式 B / model", "robust-quadratic-v1：有效分析区域内稳健拟合二维二次背景。亮条带 sum(I−B)，暗条带 sum(B−I)，保留负值；不再扣局部背景框。"],
               ["模型参数", "clipSigma 为迭代残差剔除阈值，以 MAD 稳健尺度的倍数表示，允许 1–6；坐标归一化，强度保持原始灰度单位。"],
               ["模型适用边界", "要求背景可由平滑二次曲面近似，且足够多像素代表背景。宽条带、密集信号、边缘或污点可能被错误计入背景；合成数值测试不是生物学定量验证。"],
               ["预览与正式方法", "预览参数与正式应用参数分别记录。预览不改正式结果。正式模型失效时净信号及比值留空；切换或修改正式方法后须重新确认。"],
               ["解释边界", "导出值为图像信号指标。截图/有损格式/未知处理历史及跨膜差异不能由人工确认消除。"],
               ["显示与测量", "PNG 预览仅供显示，不进入计算。模式 A 读取原文件；模式 B 使用原强度单位的 float64 背景和校正数组；16-bit 单通道不先降为 8-bit。"],
               ["缺失值", "空白代表未确认、无有效对照、缺失选区或数值异常；不是零。"],
               ["端点提示", "灰度端点计数描述当前文件，不证明原始采集已饱和或处于线性范围。"],
               ["背景方法依据", "https://imagej.net/ij/docs/menus/analyze.html"],
               ["显示与测量依据", "https://imagej.net/ij/docs/guide/146-28.html"],
               ["归档说明", "完整 ZIP 同时包含原图、带框预览、分析快照与审计记录。derived/ 是派生背景和校正结果，包含数组、显示 PNG、manifest 及使用索引；不是仪器原图。"]]
    row_offset = 2
    for exp, result in zip(experiments, analyses):
        controls = [row_offset + i for i, r in enumerate(result["rows"]) if r["control"]]
        refs = ",".join(f"J{i}" for i in controls)
        mean_formula = f'IF(COUNT({refs})={len(controls)},IF(MIN({refs})>0,AVERAGE({refs}),""),"")' if controls else None
        methods.extend([["实验", exp["id"] + " | " + exp["name"]], ["实验版本", exp["revision"]],
                        ["实验备注", exp.get("notes", "")], ["实验计算方法", method_description(exp, result)]])
        for row in result["rows"]:
            index = len(main) + 1
            net_links = {}
            role_methods = {}
            for role in ("pho", "total"):
                measurement, image, roi = row[role], row[role + "Image"], row[role + "Roi"]
                role_methods[role] = method_values(exp, row, role)
                di = len(details) + 1
                net = measurement.get("net") if measurement else None
                if measurement and net is not None and measurement.get("rawSum") is not None:
                    if role_methods[role][0] == "model":
                        formula = f'IF(H{di}="dark",V{di}-M{di},M{di}-V{di})'
                    else:
                        formula = f'IF(H{di}="dark",L{di}*N{di}-M{di},M{di}-L{di}*N{di})'
                    net_cell = (formula, net)
                    net_links[role] = (f"'测量明细'!P{di}", net)
                else:
                    net_cell, net_links[role] = None, None
                details.append([row["recordId"], role, row["sampleName"], image["filename"] if image else None,
                                image["id"] if image else None, image["sha256"] if image else None,
                                image["bitDepth"] if image else None, exp["settings"][role]["polarity"],
                                bool(roi and roi["confirmed"]), rect_text(roi["band"]) if roi else None,
                                rect_text(roi["background"]) if roi else None,
                                measurement.get("area") if measurement else None, measurement.get("rawSum") if measurement else None,
                                measurement.get("backgroundMean") if measurement else None, measurement.get("backgroundArea") if measurement else None,
                                net_cell, measurement.get("endpointCount") if measurement else None,
                                "；".join((image.get("warnings", []) if image else []) + (measurement.get("warnings", []) if measurement else [])),
                                f"originals/{image['id']}/{image['filename']}" if image else None] + role_methods[role])
            main.append([row["recordId"], exp["id"], exp["name"], row["sampleName"], row["group"], row["control"], row["status"],
                         net_links["pho"], net_links["total"],
                         (f'IF(AND(N{index}=1,H{index}>0,I{index}>0),H{index}/I{index},"")', row["ratio"]),
                         (mean_formula, result["controlMean"]) if mean_formula else None,
                         (f'IF(AND(ISNUMBER(J{index}),ISNUMBER(K{index}),K{index}>0),J{index}/K{index},"")', row["relative"]),
                         "；".join(row["warnings"] + result["warnings"]), 1 if row["ratio"] is not None else 0,
                         role_methods["pho"][0], role_methods["total"][0], role_methods["pho"][9], role_methods["total"][9]])
        row_offset += len(result["rows"])
    sheets = [("定量结果", main, [39, 24, 27, 18, 14, 8, 25, 20, 20, 23, 23, 23, 76, 20, 16, 16, 42, 42]),
              ("测量明细", details, [39, 12, 18, 24, 24, 67, 10, 12, 15, 24, 24, 17, 20, 18, 17, 20, 17, 76, 50] + [18, 28, 23, 28, 12, 35, 42, 67, 67, 42, 67, 35, 42, 67, 15, 28, 12]),
              ("方法与来源", methods, [24, 120])]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, 4)) + '</Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _, _) in enumerate(sheets, 1)) + '</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, 4)) + '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", STYLES)
        for i, (_, rows, widths) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(rows, widths))
    return output.getvalue()


def rect_text(rect):
    return ",".join(str(rect[k]) for k in ("x", "y", "w", "h"))


def csv_bytes(experiments, analyses):
    data = io.StringIO(newline="")
    writer = csv.writer(data)
    header = ["记录编号", "实验编号", "实验名称", "实验版本", "样本编号", "样本名称", "分组", "对照", "复核状态",
              "P净信号", "total净信号", "p/total图像信号比", "本次对照R均值", "相对对照图像指标"]
    suffixes = ["原文件名", "图像编号", "SHA256", "位深", "极性", "选区已确认", "条带框xywh", "背景框xywh", "条带像素数", "条带像素和", "背景均值", "背景像素数", "端点像素数"]
    writer.writerow(header + [r + "_" + s for r in ("P", "total") for s in suffixes] + ["来源与复核说明", "计算方法"] +
                    [r + "_" + s for r in ("P", "total") for s in METHOD_HEADERS])
    for exp, result in zip(experiments, analyses):
        for row in result["rows"]:
            vals = [row["recordId"], exp["id"], exp["name"], exp["revision"], row["sampleId"], row["sampleName"], row["group"],
                    "是" if row["control"] else "否", row["status"], row["pho"].get("net") if row["pho"] else None,
                    row["total"].get("net") if row["total"] else None, row["ratio"], result["controlMean"], row["relative"]]
            for role in ("pho", "total"):
                m, image, roi = row[role], row[role + "Image"], row[role + "Roi"]
                vals += [image.get(k) if image else None for k in ("filename", "id", "sha256", "bitDepth")]
                vals += [exp["settings"][role]["polarity"], "是" if roi and roi["confirmed"] else "否",
                         rect_text(roi["band"]) if roi else None, rect_text(roi["background"]) if roi else None]
                vals += [m.get(k) if m else None for k in ("area", "rawSum", "backgroundMean", "backgroundArea", "endpointCount")]
            vals += ["；".join(dict.fromkeys(row["warnings"] + result["warnings"])), method_description(exp, result)]
            for role in ("pho", "total"):
                vals += method_values(exp, row, role)
            # Prevent sample/file names being interpreted as Excel formulas on CSV open.
            vals = ["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in vals]
            writer.writerow(vals)
    return b"\xef\xbb\xbf" + data.getvalue().encode("utf-8")


def build_export(store, experiments, kind):
    if kind not in ("xlsx", "csv", "zip"):
        raise ValueError("导出格式应为 xlsx、csv 或 zip")
    analyses = [store.results(exp) for exp in experiments]
    if kind == "csv":
        return csv_bytes(experiments, analyses), "text/csv; charset=utf-8", "csv"
    xlsx = xlsx_bytes(store, experiments, analyses)
    if kind == "xlsx":
        return xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("results.xlsx", xlsx)
        z.writestr("results.csv", csv_bytes(experiments, analyses))
        snapshot = {"software": SOFTWARE, "exportedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "experiments": experiments, "analyses": analyses}
        z.writestr("provenance.json", json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False))
        ids = {e["id"] for e in experiments}
        events = [e for e in store.state.get("audit", []) if e["experimentId"] in ids]
        z.writestr("audit.jsonl", "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")
        archived = set()
        derived_keys = set()
        derived_usage = []
        for exp in experiments:
            for role in ("pho", "total"):
                background = (exp.get("background") or {}).get(role) or {}
                for purpose in ("applied", "preview"):
                    artifact = background.get(purpose)
                    if not artifact:
                        continue
                    key = artifact.get("key", "")
                    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
                        raise ValueError("派生结果缓存键无效，不能生成可追溯归档")
                    derived_usage.append({"experimentId": exp["id"], "role": role, "purpose": purpose,
                                          "active": purpose == "applied" and background.get("mode") == "model",
                                          "key": key, "archivePath": f"derived/{key}/", "artifact": artifact})
                    if key in derived_keys:
                        continue
                    folder = store.directory / "derived" / key
                    if folder.is_symlink() or folder.resolve().parent != (store.directory / "derived").resolve():
                        raise ValueError("派生结果目录无效")
                    for name in DERIVED_FILES:
                        path = folder / name
                        if not path.is_file() or path.is_symlink():
                            raise ValueError(f"派生结果缺少文件 {key}/{name}，不能生成完整归档")
                        z.writestr(f"derived/{key}/{name}", path.read_bytes())
                    derived_keys.add(key)
                image = exp["images"][role]
                if not image:
                    continue
                z.writestr(f"annotated/{exp['id']}-{role}.png", store.annotated(exp, role))
                if image["id"] not in archived:
                    archived.add(image["id"])
                    z.writestr(f"originals/{image['id']}/{image['filename']}", store.image_path(image).read_bytes())
        z.writestr("derived/index.json", json.dumps({"kind": "derived-analysis-results", "usage": derived_usage}, ensure_ascii=False, indent=2, allow_nan=False))
        z.writestr("README.txt", SOFTWARE + " 分析归档\nresults.xlsx / results.csv：相同数据快照；原方法和背景模型互斥，不重复扣背景。\nannotated/：放大的带标记预览。\noriginals/：输入文件的完整原始副本，SHA256 位于 provenance.json 和表格中。\nderived/：派生结果，不是仪器原图；index.json 区分预览和正式应用。\n每个派生目录的 background.npy 和 corrected.npy 为处理区域内的 float64 原强度单位数组，允许负校正值；manifest.json 记录算法、参数、区域和显示映射。\nbackground.png / corrected.png 是显示用派生图，不能用作重新定量的数据来源。\nprovenance.json：选区、样本、参数、复核状态及来源。\naudit.jsonl：操作历史。\n图像信号指标不等同于绝对磷酸化比例；来源和方法限制随表格保留。\n")
    return output.getvalue(), "application/zip", "zip"
