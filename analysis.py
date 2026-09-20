"""Pixel-preserving measurements and editable WB region suggestions.

Coordinates always refer to the stored image, with half-open rectangles.  The
suggestion algorithm is a convenience for review, not a biological classifier.
Only NumPy and Pillow are required; original files are never written to.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError


SUPPORTED_FORMATS = {"TIFF", "PNG", "JPEG", "BMP"}
MAX_PIXELS = 40_000_000
ENDPOINT_WARNING = "文件数值端点像素仅提示复核，不证明原始采集信号饱和或在线性范围内。"
BACKGROUND_WARNING = "使用人工可调整的局部背景矩形均值；自动建议尚未经科学方法验证，请检查背景代表性。"


def _metadata_has_screenshot(image: Image.Image) -> bool:
    # Search only metadata.  A filename or dark band is not screenshot evidence.
    for value in image.info.values():
        if isinstance(value, bytes) and b"screenshot" in value.lower():
            return True
        if isinstance(value, str) and "screenshot" in value.lower():
            return True
    try:
        exif = image.getexif()
        values = list(exif.values())
        if 34665 in exif:
            values.extend(exif.get_ifd(34665).values())
        return any("screenshot" in str(value).lower() for value in values)
    except (ValueError, TypeError, OSError):
        return False


def read_image(path: str | Path) -> tuple[np.ndarray, dict[str, Any]]:
    """Read stored pixel values into float64, without contrast/gamma correction.

    Supports 8-bit grayscale/RGB and 16-bit unsigned grayscale.  Alpha must be
    fully opaque.  Formats Pillow cannot decode without losing depth are rejected.
    """
    path = Path(path)
    try:
        with Image.open(path) as im:
            if im.format not in SUPPORTED_FORMATS:
                raise ValueError("目前支持 TIFF、PNG、JPEG 和 BMP 图像。")
            if getattr(im, "n_frames", 1) != 1:
                raise ValueError("暂不支持多页或多帧图像；请先从仪器软件导出单张分析图。")
            if im.width < 1 or im.height < 1 or im.width * im.height > MAX_PIXELS:
                raise ValueError("图像尺寸无效，或超过本版本 4000 万像素的限制。")
            fmt, mode = im.format, im.mode
            warnings = [ENDPOINT_WARNING]
            if _metadata_has_screenshot(im):
                warnings.append("元数据包含 Screenshot：该文件有截图来源标记，原始定量适用性未验证。")
            if fmt == "JPEG":
                warnings.append("JPEG 使用有损压缩，像素值可能已改变；建议另存并使用仪器原始分析图。")
            if fmt == "PNG":
                # Pillow exposes 16-bit RGB/LA/RGBA PNG as 8-bit channels. Check
                # the source IHDR before conversion can hide the precision loss.
                with path.open("rb") as source:
                    header = source.read(26)
                if len(header) == 26 and header[24] == 16 and header[25] in (2, 4, 6):
                    raise ValueError("暂不支持 16 位多通道 PNG，避免解码时丢失原始精度；请导出单通道 16 位图。")
            if fmt == "TIFF":
                bits = im.tag_v2.get(258, (8,))
                bits = (bits,) if isinstance(bits, int) else bits
                if len(bits) > 1 and any(int(bit) > 8 for bit in bits):
                    raise ValueError("暂不支持高于 8 位的多通道 TIFF，避免解码时丢失原始精度；请导出单通道 16 位图。")
                compression = im.tag_v2.get(259)
                if compression in (6, 7):
                    warnings.append("TIFF 元数据标记 JPEG 压缩，像素值可能包含有损压缩影响。")
            im.load()
            if mode in ("P", "PA"):
                im = im.convert("RGBA")
                warnings.append("调色板图像按已存颜色解码为灰度，未恢复任何原始仪器信号。")
            if "A" in im.getbands():
                alpha = np.asarray(im.getchannel("A"))
                if np.any(alpha != 255):
                    raise ValueError("图像含透明或半透明像素；请提供不含透明度的原图，以免把合成显示像素当成测量值。")
                im = im.convert("RGB" if "R" in im.getbands() else "L")
            if "transparency" in im.info:
                # L/RGB PNG may store a transparent key without an alpha channel.
                alpha = np.asarray(im.convert("RGBA").getchannel("A"))
                if np.any(alpha != 255):
                    raise ValueError("图像含透明像素；请提供不含透明度的分析原图。")
            raw = np.asarray(im)
            if raw.dtype.kind not in "uib":
                raise ValueError("暂不支持浮点或非整数图像；请使用单通道 8 位或 16 位分析图。")
            if im.mode == "1":
                raise ValueError("二值图像不适合本版本灰度信号测量，请提供 8 位或 16 位图。")
            if raw.ndim == 3 and raw.shape[2] == 3 and raw.dtype == np.uint8:
                channels = raw.astype(np.float64)
                if np.array_equal(raw[..., 0], raw[..., 1]) and np.array_equal(raw[..., 0], raw[..., 2]):
                    data = channels[..., 0].copy()
                else:
                    data = (299 * channels[..., 0] + 587 * channels[..., 1] + 114 * channels[..., 2]) / 1000
                bit_depth = 8
                conversion = "RGB encoded-value luminance: 0.299 R + 0.587 G + 0.114 B; no gamma/ICC transform"
                warnings.append("RGB 图按 0.299R + 0.587G + 0.114B 转为灰度；未作伽马或 ICC 校正，不等同于原始单通道强度。")
            elif raw.ndim == 2 and raw.dtype == np.uint8:
                data, bit_depth, conversion = raw.astype(np.float64), 8, "stored grayscale; no transform"
            elif raw.ndim == 2 and raw.dtype.kind == "u" and raw.dtype.itemsize == 2:
                data, bit_depth, conversion = raw.astype(np.float64), 16, "stored grayscale; no transform"
            elif raw.ndim == 2 and im.mode == "I" and fmt == "PNG" and raw.min() >= 0 and raw.max() <= 65535:
                # Some Pillow releases expose 16-bit PNG as mode I / int32.
                data, bit_depth, conversion = raw.astype(np.float64), 16, "stored grayscale; no transform"
            else:
                raise ValueError("该图像模式暂不支持保真定量；请导出单通道 8 位/16 位，或 8 位 RGB 图。")
            if not np.isfinite(data).all():
                raise ValueError("图像包含非有限值，无法进行定量。")
            if bit_depth == 8:
                warnings.append("8 位文件的原始采集位深、曝光和处理历史未知；本次计算仅描述文件像素。")
            if im.getexif().get(274, 1) != 1:
                warnings.append("文件含方向标记；预览和测量均按存储像素方向处理，未自动旋转。")
            metadata = {
                "width": int(data.shape[1]), "height": int(data.shape[0]),
                "format": fmt, "bitDepth": bit_depth, "mode": mode,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "warnings": warnings, "endpointMin": 0, "endpointMax": 2 ** bit_depth - 1,
                "measurementConversion": conversion, "frameCount": 1,
                "sourceMin": float(data.min()), "sourceMax": float(data.max()),
            }
            return data, metadata
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"无法读取图像：{exc}") from exc


def write_preview(path: str | Path, destination: str | Path) -> dict[str, Any]:
    """Write a display-only PNG.  Measurement always uses read_image(original)."""
    if Path(path).resolve() == Path(destination).resolve():
        raise ValueError("预览输出路径不能覆盖原始图像。")
    data, metadata = read_image(path)
    if metadata["bitDepth"] == 8:
        with Image.open(path) as im:
            preview = im.convert("RGB")
            preview.save(destination, format="PNG")
        metadata["previewScaling"] = {"mode": "source 8-bit appearance", "measurementUnaffected": True}
    else:
        low, high = float(data.min()), float(data.max())
        if high > low:
            display = np.rint((data - low) * 255 / (high - low)).clip(0, 255).astype(np.uint8)
        else:
            display = np.zeros(data.shape, dtype=np.uint8)
        Image.fromarray(display).save(destination, format="PNG")
        metadata["previewScaling"] = {
            "mode": "display-only linear min/max to 0–255", "min": low, "max": high,
            "measurementUnaffected": True,
        }
        metadata["warnings"].append("16 位图仅在预览中作线性最小/最大值拉伸；测量保留原始 16 位数值。")
    return metadata


def validate_rect(rect: dict, width: int, height: int) -> dict[str, int]:
    """Return a strict validated rectangle; never silently clip measurement ROIs."""
    if not isinstance(rect, dict):
        raise ValueError("选区必须包含整数 x、y、w、h。")
    result = {}
    for field in ("x", "y", "w", "h"):
        value = rect.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError("选区 x、y、w、h 必须为整数像素。")
        result[field] = int(value)
    x, y, w, h = (result[key] for key in ("x", "y", "w", "h"))
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        raise ValueError("选区越界或面积为零，请调整到原图范围内。")
    return result


def rects_overlap(a: dict, b: dict) -> bool:
    """Half-open rectangles may touch edges without overlapping pixels."""
    return bool(a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
                and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


def _invalid_measurement(message: str, warnings: list[str]) -> dict[str, Any]:
    return {
        "area": 0, "rawSum": None, "backgroundMean": None, "backgroundArea": 0,
        "net": None, "min": None, "max": None, "endpointCount": 0,
        "warnings": [*warnings, message], "valid": False,
    }


def measure_roi(path: str | Path, roi: dict, polarity: str = "dark") -> dict[str, Any]:
    """Compute local-mean background-corrected integrated pixel signal.

    Dark: area * backgroundMean - rawSum. Bright: the opposite. Invalid or
    overlapping rectangles are refused and nonpositive results are retained but
    flagged, so the caller cannot silently release a ratio from them.
    """
    data, metadata = read_image(path)
    warnings = [*metadata["warnings"], BACKGROUND_WARNING]
    if polarity not in ("dark", "bright"):
        return _invalid_measurement("条带极性无效，请选择 dark 或 bright。", warnings)
    try:
        if not isinstance(roi, dict):
            raise ValueError("缺少条带或背景选区。")
        band = validate_rect(roi.get("band"), metadata["width"], metadata["height"])
        background = validate_rect(roi.get("background"), metadata["width"], metadata["height"])
        if rects_overlap(band, background):
            raise ValueError("条带与背景选区重叠，请先调整后复核。")
    except ValueError as exc:
        return _invalid_measurement(str(exc), warnings)
    pixels = data[band["y"]:band["y"] + band["h"], band["x"]:band["x"] + band["w"]]
    bg = data[background["y"]:background["y"] + background["h"], background["x"]:background["x"] + background["w"]]
    raw_sum, background_mean = float(pixels.sum()), float(bg.mean())
    net = float(pixels.size * background_mean - raw_sum)
    if polarity == "bright":
        net = -net
    # Close cancellation of two integrals can leave a positive round-off residue
    # (notably for RGB luminance, whose encoded values are fractional). Bound
    # that numerical scale using float64 epsilon and the reduction depths. This
    # is not a biological detection threshold, and the original net is retained.
    reduction_depth = math.ceil(math.log2(max(1, pixels.size))) + math.ceil(math.log2(max(1, bg.size))) + 4
    numerical_tolerance = float(np.finfo(np.float64).eps * reduction_depth
                                * (abs(raw_sum) + abs(pixels.size * background_mean)))
    endpoint_count = int(np.count_nonzero((pixels == metadata["endpointMin"]) | (pixels == metadata["endpointMax"])))
    bg_endpoint_count = int(np.count_nonzero((bg == metadata["endpointMin"]) | (bg == metadata["endpointMax"])))
    if endpoint_count or bg_endpoint_count:
        warnings.append(f"条带中有 {endpoint_count} 个、背景中有 {bg_endpoint_count} 个文件数值端点像素，请检查图像来源。")
    if net <= 0:
        warnings.append("背景校正信号非正，本版本暂不用于比值；请检查极性、条带及背景选区。")
    if 0 < abs(net) <= numerical_tolerance:
        warnings.append("净信号处于浮点积分相减的舍入误差量级，暂不用于比值；保留原计算值，未裁零。此判断不是生物学信号阈值。")
    return {
        "area": int(pixels.size), "rawSum": raw_sum,
        "backgroundMean": background_mean, "backgroundArea": int(bg.size),
        "net": net, "min": float(pixels.min()), "max": float(pixels.max()),
        "numericalTolerance": numerical_tolerance,
        "endpointCount": endpoint_count, "backgroundEndpointCount": bg_endpoint_count,
        "warnings": warnings, "valid": net > numerical_tolerance,
    }


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    width = max(1, min(int(width), len(values)))
    if width % 2 == 0:
        width = max(1, width - 1)
    padding = width // 2
    return np.convolve(np.pad(values, (padding, padding), mode="edge"), np.ones(width) / width, mode="valid")


def _clamp_region(region: dict, width: int, height: int) -> dict[str, int]:
    if not isinstance(region, dict):
        raise ValueError("请先圈出包含目标条带和附近背景的区域。")
    for key in ("x", "y", "w", "h"):
        if isinstance(region.get(key), bool) or not isinstance(region.get(key), (int, np.integer)):
            raise ValueError("目标区域必须使用整数像素坐标。")
    if region["w"] <= 0 or region["h"] <= 0:
        raise ValueError("目标区域必须有正面积。")
    x0, y0 = max(0, region["x"]), max(0, region["y"])
    x1, y1 = min(width, region["x"] + region["w"]), min(height, region["y"] + region["h"])
    return validate_rect({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}, width, height)


def _lane_centers(profile: np.ndarray, count: int) -> tuple[list[float], float, bool]:
    """Fit a near-regular grid before local refinement to avoid double peaks.

    A known target count is supplied by the sample list. Log compression keeps a
    weak lane from being discarded solely because a neighbouring lane is strong.
    This is only an editable geometry suggestion.
    """
    width = len(profile)
    nominal = width / count
    smooth = _smooth(profile, max(3, round(nominal * 0.14)))
    span = float(smooth.max() - smooth.min())
    if span <= max(1e-10, float(np.max(np.abs(smooth))) * 0.025):
        return [(i + 0.5) * nominal for i in range(count)], nominal, True
    floor = float(np.percentile(smooth, 10))
    excess = np.maximum(smooth - floor, 0)
    scale = max(float(np.percentile(excess, 60)), float(excess.max()) * 0.04, 1e-12)
    scores = np.log1p(excess / scale)
    if count == 1:
        center = float(np.argmax(scores))
        return [center], nominal, False
    best_score, best = -math.inf, None
    for pitch in np.linspace(width / (count + 0.65), width / (count - 0.35), 60):
        margin = 0.23 * pitch
        end = width - 1 - margin - (count - 1) * pitch
        if end < margin:
            continue
        for offset in np.linspace(margin, end, 64):
            centers = offset + np.arange(count) * pitch
            # Score a narrow neighborhood but penalize departures from the grid.
            total = 0.0
            refined = []
            for center in centers:
                lo = max(0, int(round(center - 0.12 * pitch)))
                hi = min(width, int(round(center + 0.12 * pitch)) + 1)
                xs = np.arange(lo, hi)
                local = scores[lo:hi] - 0.3 * ((xs - center) / (0.12 * pitch)) ** 2
                winner = int(np.argmax(local))
                total += float(local[winner])
                refined.append(float(xs[winner]))
            # Very unequal margins are a weak cue that the proposed target span
            # has skipped an end lane to count a second peak in a strong lane.
            left, right = refined[0], width - 1 - refined[-1]
            total -= 0.12 * abs(left - right) / pitch
            if total > best_score:
                best_score, best = total, (refined, float(pitch))
    if best is None:
        return [(i + 0.5) * nominal for i in range(count)], nominal, True
    grid, pitch = best
    # A band can have a flat top or two dark lobes.  Finding the strongest pixel
    # is then an edge detector, not a center estimator.  Use one signal centroid
    # inside each fitted lane cell so both lobes remain one sample.
    bounds = [0, *[int((a + b) / 2) for a, b in zip(grid, grid[1:])], width]
    refined = []
    for center, left, right in zip(grid, bounds, bounds[1:]):
        local = profile[left:right]
        weights = np.maximum(local - np.percentile(local, 15), 0)
        if weights.sum() > 1e-12:
            centroid = float(np.dot(weights, np.arange(left, right)) / weights.sum())
            center = float(np.clip(centroid, center - pitch * 0.30, center + pitch * 0.30))
        refined.append(center)
    return refined, pitch, False


def suggest_rois(path: str | Path, region: dict, samples: list[dict], polarity: str = "dark") -> list[dict]:
    """Suggest one editable band/background pair per sample, left to right."""
    if polarity not in ("dark", "bright"):
        raise ValueError("请指定暗条带 dark 或亮条带 bright。")
    if not isinstance(samples, list) or not 1 <= len(samples) <= 32:
        raise ValueError("第一版支持 1–32 个样本泳道。")
    sample_ids = [sample.get("id") if isinstance(sample, dict) else None for sample in samples]
    if any(not isinstance(value, str) or not value for value in sample_ids) or len(set(sample_ids)) != len(sample_ids):
        raise ValueError("每个样本必须有唯一且非空的编号。")
    data, metadata = read_image(path)
    region = _clamp_region(region, metadata["width"], metadata["height"])
    x, y, w, h = (region[key] for key in ("x", "y", "w", "h"))
    if w < len(samples) * 8 or h < 10:
        raise ValueError("目标区域过小；每个泳道至少需 8 像素宽，区域至少需 10 像素高并包含局部背景。")
    crop = data[y:y + h, x:x + w]
    if polarity == "dark":
        base = np.percentile(crop, 85, axis=0)
        signal = np.maximum(base[np.newaxis, :] - crop, 0)
    else:
        base = np.percentile(crop, 15, axis=0)
        signal = np.maximum(crop - base[np.newaxis, :], 0)
    # Global baseline preserves full broad-band shapes in the horizontal profile;
    # per-column baselines alone can make the middle of a thick band disappear.
    global_base = np.percentile(crop, 85 if polarity == "dark" else 15)
    horizontal_signal = np.maximum(global_base - crop if polarity == "dark" else crop - global_base, 0)
    centers, pitch, fallback = _lane_centers(horizontal_signal.mean(axis=0), len(samples))
    minimum_spacing = min(np.diff(centers)) if len(centers) > 1 else pitch
    band_width = max(3, min(int(round(pitch * 0.70)), int(minimum_spacing) - 2, w))
    bg_height = max(2, min(8, int(round(h * 0.12))))
    gap = max(1, min(3, round(h * 0.045)))
    max_band_height = max(2, h - 2 * (bg_height + gap))
    proposals = []
    for sample, center in zip(samples, centers):
        bx = max(0, min(w - band_width, round(center - band_width / 2)))
        row_profile = _smooth(signal[:, bx:bx + band_width].mean(axis=1), max(1, round(h * 0.06)))
        peak = int(np.argmax(row_profile))
        low = float(np.percentile(row_profile, 20))
        amplitude = float(row_profile[peak] - low)
        if amplitude <= 1e-9:
            peak, extent_lo, extent_hi = h // 2, h // 3, 2 * h // 3
        else:
            threshold = low + amplitude * 0.18
            extent_lo = extent_hi = peak
            while extent_lo > 0 and row_profile[extent_lo - 1] > threshold:
                extent_lo -= 1
            while extent_hi + 1 < h and row_profile[extent_hi + 1] > threshold:
                extent_hi += 1
        band_height = max(2, min(max_band_height, extent_hi - extent_lo + 3))
        cy = (extent_lo + extent_hi) / 2
        by = max(0, min(h - band_height, round(cy - band_height / 2)))
        band = {"x": x + bx, "y": y + by, "w": band_width, "h": band_height}
        # Prefer the immediately adjacent upper strip. Never choose a remote
        # background just because it gives a larger corrected signal.
        background_y = by - gap - bg_height
        if background_y < 0:
            background_y = by + band_height + gap
        if background_y + bg_height > h:
            raise ValueError("目标区域缺少可用背景空间，请扩大选区并再次建议。")
        background = {"x": x + bx, "y": y + background_y, "w": band_width, "h": bg_height}
        note = "按样本数和近似等距泳道提出候选；条带范围与局部背景均须人工检查。"
        if fallback:
            note = "图像缺少明确泳道峰，已按等距生成候选；这些位置不代表已识别到条带。"
        proposals.append({"sampleId": sample["id"], "band": band, "background": background,
                          "confirmed": False, "suggestionNote": note})
    for proposal in proposals:
        validate_rect(proposal["band"], metadata["width"], metadata["height"])
        validate_rect(proposal["background"], metadata["width"], metadata["height"])
        if any(rects_overlap(proposal["background"], other["band"]) for other in proposals):
            raise ValueError("自动背景与条带冲突，请扩大区域或手动设置选区。")
    return proposals
