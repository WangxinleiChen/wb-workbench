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
    headers = ["记录编号", "实验编号", "实验名称", "样本", "分组", "对照", "复核状态", "P 净信号", "total 净信号", "p/total 图像信号比", "本次对照 R 均值", "相对对照图像指标", "来源与复核说明", "数值发布条件满足"]
    main = [headers]
    details = [["记录编号", "图像角色", "样本", "原文件名", "图像编号", "SHA-256", "位深", "数值极性", "选区已确认", "条带框 x,y,w,h", "背景框 x,y,w,h", "条带像素数", "条带像素和", "背景均值", "背景像素数", "净信号", "端点像素数", "状态说明", "原图在归档内的位置"]]
    methods = [["项目", "内容"], ["软件版本", "WB Workbench 1.0.0"], ["导出时间 UTC", dt.datetime.now(dt.timezone.utc).isoformat()],
               ["测量定义", analyses[0]["method"]],
               ["背景假设", "局部背景在条带区域内近似恒定。背景框应避开条带和污点；需人工复核。"],
               ["解释边界", "导出值为图像信号指标。截图/有损格式/未知处理历史及跨膜差异不能由人工确认消除。"],
               ["显示与测量", "PNG 预览仅供显示，计算读取保存的原文件像素；16-bit 单通道保留原数值。"],
               ["缺失值", "空白代表未确认、无有效对照、缺失选区或数值异常；不是零。"],
               ["端点提示", "灰度端点计数描述当前文件，不证明原始采集已饱和或处于线性范围。"],
               ["背景方法依据", "https://imagej.net/ij/docs/menus/analyze.html"],
               ["显示与测量依据", "https://imagej.net/ij/docs/guide/146-28.html"],
               ["归档说明", "完整 ZIP 同时包含原图、带框预览、分析快照与审计记录。标记图为预览，不用于重新测量。"]]
    row_offset = 2
    for exp, result in zip(experiments, analyses):
        controls = [row_offset + i for i, r in enumerate(result["rows"]) if r["control"]]
        refs = ",".join(f"J{i}" for i in controls)
        mean_formula = f'IF(COUNT({refs})={len(controls)},IF(MIN({refs})>0,AVERAGE({refs}),""),"")' if controls else None
        methods.extend([["实验", exp["id"] + " | " + exp["name"]], ["实验版本", exp["revision"]], ["实验备注", exp.get("notes", "")]])
        for row in result["rows"]:
            index = len(main) + 1
            net_links = {}
            for role in ("pho", "total"):
                measurement, image, roi = row[role], row[role + "Image"], row[role + "Roi"]
                di = len(details) + 1
                net = measurement.get("net") if measurement else None
                if measurement and net is not None and measurement.get("rawSum") is not None:
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
                                f"originals/{image['id']}/{image['filename']}" if image else None])
            main.append([row["recordId"], exp["id"], exp["name"], row["sampleName"], row["group"], row["control"], row["status"],
                         net_links["pho"], net_links["total"],
                         (f'IF(AND(N{index}=1,H{index}>0,I{index}>0),H{index}/I{index},"")', row["ratio"]),
                         (mean_formula, result["controlMean"]) if mean_formula else None,
                         (f'IF(AND(ISNUMBER(J{index}),ISNUMBER(K{index}),K{index}>0),J{index}/K{index},"")', row["relative"]),
                         "；".join(row["warnings"] + result["warnings"]), 1 if row["ratio"] is not None else 0])
        row_offset += len(result["rows"])
    sheets = [("定量结果", main, [39, 24, 27, 18, 14, 8, 25, 20, 20, 23, 23, 23, 76, 20]),
              ("测量明细", details, [39, 12, 18, 24, 24, 67, 10, 12, 15, 24, 24, 17, 20, 18, 17, 20, 17, 76, 50]),
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
    writer.writerow(header + [r + "_" + s for r in ("P", "total") for s in suffixes] + ["来源与复核说明", "计算方法"])
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
            vals += ["；".join(dict.fromkeys(row["warnings"] + result["warnings"])), result["method"]]
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
        snapshot = {"software": "WB Workbench 1.0.0", "exportedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "experiments": experiments, "analyses": analyses}
        z.writestr("provenance.json", json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False))
        ids = {e["id"] for e in experiments}
        events = [e for e in store.state.get("audit", []) if e["experimentId"] in ids]
        z.writestr("audit.jsonl", "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")
        archived = set()
        for exp in experiments:
            for role in ("pho", "total"):
                image = exp["images"][role]
                if not image:
                    continue
                z.writestr(f"annotated/{exp['id']}-{role}.png", store.annotated(exp, role))
                if image["id"] not in archived:
                    archived.add(image["id"])
                    z.writestr(f"originals/{image['id']}/{image['filename']}", store.image_path(image).read_bytes())
        z.writestr("README.txt", "WB Workbench 分析归档\nresults.xlsx / results.csv：相同数据快照。\nannotated/：放大的带标记预览。\noriginals/：输入文件的完整原始副本，SHA256 位于 provenance.json 和表格中。\nprovenance.json：选区、样本、参数、复核状态及来源。\naudit.jsonl：操作历史。\n图像信号指标不等同于绝对磷酸化比例；来源和方法限制随表格保留。\n")
    return output.getvalue(), "application/zip", "zip"
