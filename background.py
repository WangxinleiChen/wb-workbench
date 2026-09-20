"""Non-destructive robust quadratic background estimation in stored intensity units.

This is a polynomial model, not ImageJ rolling ball. Coordinates alone are
normalized; input values, fitted background, and signed residuals stay float64 in
the intensity units provided by the unchanged analysis.read_image function.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from analysis import read_image, validate_rect

ALGORITHM = "robust-quadratic-v1"
VERSION = 1
MAX_REGION_PIXELS = 4_000_000
MAX_FIT_SAMPLES = 65_536
MAX_ITERATIONS = 20
MAD_SCALE = 1.482602218505602
MODEL_WARNING = (
    "稳健二次曲面仅适用于背景平滑且非条带背景占多数的区域；宽条带、弥散信号、"
    "密集条带或背景突变可能进入拟合并被误扣。该方法不是 ImageJ rolling ball。"
)


def validate_params(params: dict | None) -> dict[str, float]:
    if params is None:
        params = {}
    if not isinstance(params, dict) or set(params) - {"clipSigma"}:
        raise ValueError("背景参数仅支持 clipSigma。")
    value = params.get("clipSigma", 2.5)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 6:
        raise ValueError("clipSigma 必须是 1–6 之间的有限数值。")
    return {"clipSigma": float(value)}


def _region(region: dict, width: int, height: int) -> dict[str, int]:
    result = validate_rect(region, width, height)
    if result["w"] < 8 or result["h"] < 8:
        raise ValueError("背景模型区域每边至少 8 像素。")
    if result["w"] * result["h"] > MAX_REGION_PIXELS:
        raise ValueError("背景模型区域最多 400 万像素；请缩小有效分析区域。")
    return result


def _polarity(value: str) -> str:
    if value not in ("dark", "bright"):
        raise ValueError("条带极性必须为 dark 或 bright。")
    return value


def cache_key(source_sha: str, region: dict, polarity: str, params: dict | None) -> str:
    if not isinstance(source_sha, str) or len(source_sha) != 64 or any(c not in "0123456789abcdef" for c in source_sha):
        raise ValueError("原图 SHA-256 无效。")
    # Validate coordinates without guessing source dimensions; source bounds are
    # checked by create_artifact and measure_model using the original image.
    if not isinstance(region, dict):
        raise ValueError("缺少背景处理区域。")
    for name in ("x", "y", "w", "h"):
        if isinstance(region.get(name), bool) or not isinstance(region.get(name), (int, np.integer)):
            raise ValueError("背景区域必须使用整数像素。")
    clean_region = _region(region, int(region["x"] + region["w"]), int(region["y"] + region["h"]))
    payload = {"sourceSha256": source_sha, "region": clean_region,
               "polarity": _polarity(polarity), "params": validate_params(params),
               "algorithm": ALGORITHM, "algorithmVersion": VERSION}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _design(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.column_stack((np.ones_like(x), x, y, x * x, x * y, y * y))


def _fit(crop: np.ndarray, clip_sigma: float) -> tuple[np.ndarray, dict]:
    height, width = crop.shape
    rows = min(height, max(8, int(math.sqrt(MAX_FIT_SAMPLES * height / width))))
    columns = min(width, max(8, MAX_FIT_SAMPLES // rows))
    rows = min(rows, MAX_FIT_SAMPLES // columns)
    yy = np.unique(np.rint(np.linspace(0, height - 1, rows)).astype(int))
    xx = np.unique(np.rint(np.linspace(0, width - 1, columns)).astype(int))
    gx, gy = np.meshgrid(2 * xx / (width - 1) - 1, 2 * yy / (height - 1) - 1)
    design = _design(gx.ravel(), gy.ravel())
    values = crop[np.ix_(yy, xx)].ravel()
    keep = np.ones(values.size, dtype=bool)
    floor = max(1.0, float(np.max(np.abs(values)))) * np.finfo(np.float64).eps * 128
    converged = False
    coefficients = None
    for iteration in range(1, MAX_ITERATIONS + 1):
        coefficients, _, rank, singular = np.linalg.lstsq(design[keep], values[keep], rcond=None)
        if rank != 6:
            raise ValueError("有效背景采样不足以拟合二次曲面，请扩大背景区域。")
        residual = values - design @ coefficients
        center = float(np.median(residual[keep]))
        sigma = float(MAD_SCALE * np.median(np.abs(residual[keep] - center)))
        candidate = np.abs(residual - center) <= max(clip_sigma * sigma, floor)
        if np.count_nonzero(candidate) < max(12, int(math.ceil(values.size * 0.1))):
            raise ValueError("残差剔除后背景样本不足，请增大 clipSigma 或重新圈定分析区域。")
        if np.array_equal(candidate, keep):
            converged = True
            break
        # Iterative sigma clipping can reconsider a previously rejected sample;
        # the final set is therefore defined by the final residual, not history.
        keep = candidate
    # The last iteration may have changed keep: coefficients must describe the
    # published final mask, even when the iteration cap was reached.
    coefficients, _, rank, singular = np.linalg.lstsq(design[keep], values[keep], rcond=None)
    if rank != 6:
        raise ValueError("背景曲面拟合退化，请调整分析区域。")
    residual = values - design @ coefficients
    center = float(np.median(residual[keep]))
    sigma = float(MAD_SCALE * np.median(np.abs(residual[keep] - center)))
    return coefficients, {
        "sampleCount": int(values.size), "retainedCount": int(np.count_nonzero(keep)),
        "retainedFraction": float(np.mean(keep)), "iterations": iteration,
        "converged": converged, "madSigma": sigma, "residualMedian": center,
        "retainedRms": float(np.sqrt(np.mean(residual[keep] ** 2))),
        "allSampleRms": float(np.sqrt(np.mean(residual ** 2))),
        "designCondition": float(singular[0] / singular[-1]),
        "sampling": "deterministic evenly spaced stored pixels, including region edges",
        "maxFitSamples": MAX_FIT_SAMPLES, "maxIterations": MAX_ITERATIONS,
        "numericalResidualFloor": floor,
    }


def _evaluate(coefficients: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    height, width = shape
    x = np.linspace(-1, 1, width)[None, :]
    result = np.empty(shape, dtype=np.float64)
    chunk = max(1, 262_144 // width)
    for start in range(0, height, chunk):
        stop = min(height, start + chunk)
        y = (2 * np.arange(start, stop) / (height - 1) - 1)[:, None]
        c = coefficients
        result[start:stop] = c[0] + c[1] * x + c[2] * y + c[3] * x * x + c[4] * x * y + c[5] * y * y
    return result


def _write_display(values: np.ndarray, destination: Path, region: dict, image_shape: tuple[int, int], polarity: str | None) -> dict:
    low, high = float(values.min()), float(values.max())
    if polarity is None:
        if high > low:
            mapped = (values - low) * (255 / (high - low))
        else:
            mapped = np.full(values.shape, 128.0)
        display = {"mapping": "linear min/max to 0..255; constant field maps to 128", "min": low, "max": high}
    else:
        extent = max(abs(low), abs(high))
        sign = -1 if polarity == "dark" else 1
        mapped = np.full(values.shape, 127.5) if extent == 0 else 127.5 + sign * values * (127.5 / extent)
        display = {"mapping": "signed residual around 127.5; dark: 127.5-127.5*S/maxAbs, bright: 127.5+127.5*S/maxAbs",
                   "maxAbs": extent, "signedMin": low, "signedMax": high}
    canvas = np.full(image_shape, 128, dtype=np.uint8)
    y, x, h, w = (region[k] for k in ("y", "x", "h", "w"))
    canvas[y:y + h, x:x + w] = np.rint(mapped).clip(0, 255).astype(np.uint8)
    Image.fromarray(canvas).save(destination, format="PNG")
    return {**display, "displayOnly": True, "measurementUnaffected": True,
            "outsideRegion": "unprocessed neutral gray (128)", "coordinates": "original stored pixel coordinates"}


def create_artifact(source_path: str | Path, region: dict, polarity: str, params: dict | None, destination_dir: str | Path) -> dict[str, Any]:
    params, polarity = validate_params(params), _polarity(polarity)
    data, original = read_image(source_path)
    region = _region(region, original["width"], original["height"])
    destination = Path(destination_dir)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("派生目录必须为空，避免覆盖既有结果或原图。")
    destination.mkdir(parents=True, exist_ok=True)
    x, y, w, h = (region[k] for k in ("x", "y", "w", "h"))
    crop = data[y:y + h, x:x + w].copy()
    del data
    coefficients, fit_stats = _fit(crop, params["clipSigma"])
    background = _evaluate(coefficients, crop.shape)
    corrected = crop - background if polarity == "bright" else background - crop
    if not np.isfinite(background).all() or not np.isfinite(corrected).all():
        raise ValueError("背景模型产生非有限数值，无法应用。")
    np.save(destination / "background.npy", background, allow_pickle=False)
    np.save(destination / "corrected.npy", corrected, allow_pickle=False)
    shape = (original["height"], original["width"])
    display = {
        "background": _write_display(background, destination / "background.png", region, shape, None),
        "corrected": _write_display(corrected, destination / "corrected.png", region, shape, polarity),
    }
    warnings = [*original["warnings"], MODEL_WARNING,
                "派生 PNG 仅用于显示；定量使用原始强度单位的浮点背景和未截断残差，不恢复截图损失、饱和或未知处理历史。"]
    if fit_stats["retainedFraction"] < 0.5:
        warnings.append("背景拟合保留的样本不足一半，请特别检查是否误扣真实信号或分析区域不合适。")
    if not fit_stats["converged"]:
        warnings.append("背景残差剔除达到 20 次上限，尚未稳定；请复核背景估计图。")
    if background.min() < original["endpointMin"] or background.max() > original["endpointMax"]:
        warnings.append("拟合背景有超出原图编码范围的值；这些数值未裁剪，请检查边界曲面是否合理。")
    metadata = {
        "key": cache_key(original["sha256"], region, polarity, params),
        "algorithm": ALGORITHM, "algorithmVersion": VERSION,
        "params": params, "region": region, "polarity": polarity,
        "sourceSha256": original["sha256"], "sourceBitDepth": original["bitDepth"],
        "sourceWidth": original["width"], "sourceHeight": original["height"],
        "intensityUnits": "unchanged stored gray intensity; float64; no intensity normalization",
        "measurementConversion": original["measurementConversion"],
        "coordinateNormalization": "u=2*(x-region.x)/(region.w-1)-1; v=2*(y-region.y)/(region.h-1)-1",
        "basis": ["1", "u", "v", "u^2", "u*v", "v^2"],
        "coefficients": coefficients.tolist(), "fitStats": fit_stats,
        "display": display, "warnings": warnings,
        "fileHashes": {name: _sha(destination / name) for name in
                  ("background.npy", "corrected.npy", "background.png", "corrected.png")},
        "arrayShape": [h, w], "arrayDtype": "float64",
        "correctedRule": "I-B" if polarity == "bright" else "B-I",
        "clippedForQuantification": False, "legacyBackgroundRectangleUsed": False,
    }
    (destination / "manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def _invalid(message: str, warnings: list[str]) -> dict:
    return {"area": 0, "rawSum": None, "backgroundMean": None, "backgroundArea": 0,
            "backgroundContribution": None, "net": None, "min": None, "max": None,
            "endpointCount": 0, "backgroundEndpointCount": 0, "numericalTolerance": None,
            "method": "model", "backgroundSource": ALGORITHM, "algorithmVersion": VERSION,
            "warnings": [*warnings, message], "valid": False}


def measure_model(source_path: str | Path, roi: dict, artifact_dir: str | Path,
                  *, expected_key: str | None = None) -> dict[str, Any]:
    """Measure a published key-named artifact, optionally bound to saved state.

    Staging directories are intentionally not measurable. The directory name
    binds a complete valid manifest/array set to the requested cache identity;
    otherwise swapping two valid artifact directories could relabel results.
    """
    data, original = read_image(source_path)
    warnings = [*original["warnings"], MODEL_WARNING]
    try:
        directory = Path(artifact_dir)
        metadata = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        warnings = list(dict.fromkeys([*warnings, *metadata.get("warnings", [])]))
        if metadata.get("algorithm") != ALGORITHM or metadata.get("algorithmVersion") != VERSION:
            raise ValueError("背景模型算法版本不匹配，需重新预览并应用。")
        if metadata.get("sourceSha256") != original["sha256"]:
            raise ValueError("背景派生结果与当前原图 SHA-256 不匹配，需重新预览并应用。")
        region = _region(metadata["region"], original["width"], original["height"])
        polarity = _polarity(metadata["polarity"])
        params = validate_params(metadata["params"])
        computed_key = cache_key(original["sha256"], region, polarity, params)
        if metadata.get("key") != computed_key:
            raise ValueError("背景模型参数或缓存标识不匹配，需重新生成。")
        if directory.name != computed_key:
            raise ValueError("背景派生目录名与缓存标识不匹配，可能混入其他参数的结果；需重新生成。")
        if expected_key is not None and expected_key != computed_key:
            raise ValueError("背景派生结果与当前正式应用的缓存标识不匹配，需重新预览并应用。")
        if not isinstance(roi, dict):
            raise ValueError("缺少条带选区。")
        band = validate_rect(roi.get("band"), original["width"], original["height"])
        if (band["x"] < region["x"] or band["y"] < region["y"] or
            band["x"] + band["w"] > region["x"] + region["w"] or
            band["y"] + band["h"] > region["y"] + region["h"]):
            raise ValueError("条带选区超出背景处理区域，不能使用区域外的显示像素定量。")
        arrays = {}
        for name in ("background.npy", "corrected.npy"):
            path = directory / name
            if _sha(path) != metadata["fileHashes"][name]:
                raise ValueError("背景派生数组校验失败，需重新生成，不能使用损坏缓存。")
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            if array.shape != (region["h"], region["w"]) or array.dtype != np.float64:
                raise ValueError("背景派生数组形状或类型无效。")
            arrays[name] = array
        x, y, w, h = (band[k] for k in ("x", "y", "w", "h"))
        pixels = data[y:y + h, x:x + w]
        local_x, local_y = x - region["x"], y - region["y"]
        bg = arrays["background.npy"][local_y:local_y + h, local_x:local_x + w]
        signal = arrays["corrected.npy"][local_y:local_y + h, local_x:local_x + w]
        if not np.isfinite(bg).all() or not np.isfinite(signal).all():
            raise ValueError("背景派生数组包含非有限数值。")
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        return _invalid(str(exc), warnings)
    raw_sum, contribution, net = float(pixels.sum()), float(bg.sum()), float(signal.sum())
    # Covers both integration roundoff and the six-term least-squares model's
    # finite arithmetic. This is numerical scale, not a biological threshold.
    depth = math.ceil(math.log2(max(1, pixels.size))) + 16
    condition = float(metadata["fitStats"].get("designCondition", 1))
    tolerance = float(np.finfo(np.float64).eps * max(depth, 128 * condition) * (abs(raw_sum) + abs(contribution)))
    endpoint_count = int(np.count_nonzero((pixels == original["endpointMin"]) | (pixels == original["endpointMax"])))
    if endpoint_count:
        warnings.append(f"条带中有 {endpoint_count} 个文件数值端点像素，请检查图像来源。")
    if net <= 0:
        warnings.append("背景校正信号非正，本版本暂不用于比值；保留原计算值，未裁零。")
    if 0 < abs(net) <= tolerance:
        warnings.append("净信号处于浮点拟合与积分的舍入误差量级，暂不用于比值；未裁零，此判断不是生物学信号阈值。")
    return {"area": int(pixels.size), "rawSum": raw_sum,
            "backgroundMean": contribution / pixels.size, "backgroundArea": 0,
            "backgroundContribution": contribution, "net": net,
            "min": float(pixels.min()), "max": float(pixels.max()),
            "endpointCount": endpoint_count, "backgroundEndpointCount": 0,
            "numericalTolerance": tolerance, "method": "model", "backgroundSource": ALGORITHM,
            "algorithmVersion": VERSION, "parameters": params, "artifactKey": metadata["key"],
            "processingRegion": region, "sourceSha256": original["sha256"],
            "warnings": warnings, "valid": net > tolerance}
