"""Meaningful numerical/geometry regressions; no biological conclusions."""

import hashlib
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import measure_roi, read_image, rects_overlap, suggest_rois, validate_rect, write_preview


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, pixels, filename="input.png"):
        path = self.folder / filename
        Image.fromarray(pixels).save(path)
        return path

    def test_dark_integral_known_pixels(self):
        pixels = np.full((10, 12), 100, dtype=np.uint8)
        pixels[3:5, 4:7] = 60
        path = self.save(pixels)
        result = measure_roi(path, {"band": {"x": 4, "y": 3, "w": 3, "h": 2},
                                    "background": {"x": 4, "y": 0, "w": 3, "h": 2}})
        self.assertTrue(result["valid"])
        self.assertEqual(result["area"], 6)
        self.assertEqual(result["rawSum"], 360)
        self.assertEqual(result["backgroundMean"], 100)
        self.assertEqual(result["backgroundArea"], 6)
        self.assertEqual(result["net"], 240)

    def test_bright_integral_with_unequal_background_area(self):
        pixels = np.full((10, 12), 10, dtype=np.uint8)
        pixels[3:5, 4:7] = np.array([[20, 30, 40], [50, 60, 70]])
        path = self.save(pixels)
        roi = {"band": {"x": 4, "y": 3, "w": 3, "h": 2},
               "background": {"x": 0, "y": 0, "w": 4, "h": 2}}
        result = measure_roi(path, roi, "bright")
        self.assertEqual(result["rawSum"], 270)
        self.assertEqual(result["backgroundMean"], 10)
        self.assertEqual(result["net"], 210)
        self.assertTrue(result["valid"])
        self.assertEqual(measure_roi(path, roi, "dark")["net"], -210)
        self.assertFalse(measure_roi(path, roi, "dark")["valid"])

    def test_16_bit_measurements_and_preview_do_not_touch_original(self):
        for extension in ("tif", "png"):
            with self.subTest(extension=extension):
                pixels = np.full((10, 12), 50_000, dtype=np.uint16)
                pixels[3:5, 4:7] = 40_000
                path = self.save(pixels, f"input.{extension}")
                original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                decoded, metadata = read_image(path)
                self.assertEqual(decoded.dtype, np.float64)
                self.assertEqual(metadata["bitDepth"], 16)
                np.testing.assert_array_equal(decoded, pixels)
                result = measure_roi(path, {"band": {"x": 4, "y": 3, "w": 3, "h": 2},
                                            "background": {"x": 4, "y": 0, "w": 3, "h": 2}})
                self.assertEqual(result["net"], 60_000)
                metadata = write_preview(path, self.folder / f"preview-{extension}.png")
                self.assertTrue(metadata["previewScaling"]["measurementUnaffected"])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original_hash)

    def test_rectangle_boundary_and_type_validation(self):
        self.assertEqual(validate_rect({"x": 0, "y": 0, "w": 10, "h": 8}, 10, 8)["w"], 10)
        invalid = [
            {"x": -1, "y": 0, "w": 3, "h": 2}, {"x": 8, "y": 0, "w": 3, "h": 2},
            {"x": 0, "y": 0, "w": 0, "h": 2}, {"x": 0.5, "y": 0, "w": 3, "h": 2},
            {"x": True, "y": 0, "w": 3, "h": 2},
        ]
        path = self.save(np.full((8, 10), 100, dtype=np.uint8))
        for rect in invalid:
            with self.subTest(rect=rect):
                with self.assertRaises(ValueError):
                    validate_rect(rect, 10, 8)
                result = measure_roi(path, {"band": rect, "background": {"x": 0, "y": 5, "w": 2, "h": 2}})
                self.assertFalse(result["valid"])
                self.assertIsNone(result["net"])

    def test_16bit_multichannel_png_rejected_before_silent_precision_loss(self):
        def chunk(kind, payload):
            checksum = zlib.crc32(kind + payload) & 0xffffffff
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

        # Write the source encoding directly: Pillow cannot create these
        # 16-bit multichannel fixtures and would lose low bytes when decoding.
        for color_type, channels in ((2, 3), (4, 2), (6, 4)):
            with self.subTest(color_type=color_type):
                samples = []
                for value in (0x1234, 0x12ff):
                    pixel = [value] * channels
                    if color_type in (4, 6):
                        pixel[-1] = 65535  # Even fully opaque alpha must not hide loss.
                    samples.extend(pixel)
                scanline = b"\0" + struct.pack(">" + "H" * len(samples), *samples)
                header = struct.pack(">IIBBBBB", 2, 1, 16, color_type, 0, 0, 0)
                payload = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                           + chunk(b"IDAT", zlib.compress(scanline)) + chunk(b"IEND", b""))
                path = self.folder / f"16bit-color-{color_type}.png"
                path.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, "16 位多通道 PNG"):
                    read_image(path)
                self.assertEqual(path.read_bytes(), payload)

    def test_overlap_invalid_but_touching_edge_allowed(self):
        a = {"x": 1, "y": 1, "w": 3, "h": 2}
        overlap = {"x": 3, "y": 1, "w": 3, "h": 2}
        touch = {"x": 4, "y": 1, "w": 3, "h": 2}
        self.assertTrue(rects_overlap(a, overlap))
        self.assertFalse(rects_overlap(a, touch))
        path = self.save(np.full((8, 10), 100, dtype=np.uint8))
        result = measure_roi(path, {"band": a, "background": overlap})
        self.assertFalse(result["valid"])
        self.assertIsNone(result["net"])

    def test_nonpositive_signal_retained_and_invalid(self):
        path = self.save(np.full((8, 10), 100, dtype=np.uint8))
        result = measure_roi(path, {"band": {"x": 1, "y": 1, "w": 3, "h": 2},
                                    "background": {"x": 1, "y": 4, "w": 3, "h": 2}})
        self.assertEqual(result["net"], 0)
        self.assertFalse(result["valid"])

    def test_fractional_rgb_cancellation_cannot_release_a_ratio(self):
        pixels = np.empty((100, 100, 3), dtype=np.uint8)
        pixels[:] = (3, 2, 3)  # Encoded luminance 2.413, not exact in float64.
        path = self.save(pixels)
        roi = {"band": {"x": 0, "y": 0, "w": 47, "h": 45},
               "background": {"x": 50, "y": 50, "w": 17, "h": 19}}
        data, _ = read_image(path)
        expected_residue = 47 * 45 * data[50:69, 50:67].mean() - data[:45, :47].sum()
        result = measure_roi(path, roi)
        self.assertEqual(result["net"], expected_residue)  # No clipping to zero.
        self.assertLessEqual(abs(result["net"]), result["numericalTolerance"])
        self.assertFalse(result["valid"])
        if result["net"] != 0:
            self.assertTrue(any("浮点" in warning for warning in result["warnings"]))
        # A single encoded-value change remains a real measurable difference;
        # the round-off check must not become an arbitrary weak-signal cutoff.
        pixels[0, 0, 0] = 2
        path = self.save(pixels)
        measurable = measure_roi(path, roi)
        self.assertAlmostEqual(measurable["net"], 0.299, places=9)
        self.assertTrue(measurable["valid"])

    def test_endpoint_pixels_are_reported_without_claiming_saturation(self):
        pixels = np.full((8, 10), 100, dtype=np.uint8)
        pixels[1:3, 1:4] = 0
        path = self.save(pixels, "endpoints.bmp")
        result = measure_roi(path, {"band": {"x": 1, "y": 1, "w": 3, "h": 2},
                                    "background": {"x": 1, "y": 4, "w": 3, "h": 2}})
        self.assertEqual(result["endpointCount"], 6)
        self.assertEqual(result["net"], 600)
        self.assertTrue(result["valid"])
        self.assertTrue(any("不证明" in value for value in result["warnings"]))

    def test_transparency_multi_frame_and_float_rejected(self):
        rgba = np.zeros((12, 12, 4), dtype=np.uint8)
        rgba[..., 3] = 255
        rgba[3, 3, 3] = 0
        path = self.save(rgba)
        with self.assertRaisesRegex(ValueError, "透明"):
            read_image(path)
        image = Image.new("L", (12, 12), 128)
        path = self.folder / "multi.tif"
        image.save(path, save_all=True, append_images=[image])
        with self.assertRaisesRegex(ValueError, "多页"):
            read_image(path)
        path = self.save(np.full((12, 12), float("nan"), dtype=np.float32), "float.tif")
        with self.assertRaises(ValueError):
            read_image(path)

    def test_8bit_rgb_jpeg_and_metadata_warnings(self):
        pixels = np.full((12, 12, 3), 100, dtype=np.uint8)
        jpeg = self.save(pixels, "lossy.jpg")
        data, metadata = read_image(jpeg)
        self.assertTrue(any("有损" in s for s in metadata["warnings"]))
        self.assertTrue(any("8 位" in s for s in metadata["warnings"]))
        np.testing.assert_array_equal(data, np.full((12, 12), 100))
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("Comment", "Screenshot")
        path = self.folder / "screenshot.png"
        Image.fromarray(pixels).save(path, pnginfo=pnginfo)
        self.assertTrue(any("Screenshot" in s for s in read_image(path)[1]["warnings"]))

    def test_six_lanes_weak_first_and_double_peaked_strong_lane(self):
        height, width = 64, 360
        xx, yy = np.meshgrid(np.arange(width), np.arange(height))
        signal = np.zeros((height, width))
        centers = [30, 90, 150, 210, 270, 330]
        for i, center in enumerate(centers):
            strength = 16 if i == 0 else 110
            # Two local x peaks still represent one lane.
            shape = np.exp(-((xx - center - 9) / 10) ** 2) + np.exp(-((xx - center + 9) / 10) ** 2)
            signal += strength * shape * np.exp(-((yy - 34) / 7) ** 2)
        path = self.save(np.clip(220 - signal, 0, 255).astype(np.uint8))
        samples = [{"id": str(i)} for i in range(6)]
        rois = suggest_rois(path, {"x": 0, "y": 0, "w": width, "h": height}, samples)
        self.assertEqual(len(rois), 6)
        for roi, expected in zip(rois, centers):
            band = roi["band"]
            self.assertLess(abs(band["x"] + band["w"] / 2 - expected), 14)
            self.assertLessEqual(band["y"], 34)
            self.assertGreater(band["y"] + band["h"], 34)
            self.assertFalse(roi["confirmed"])
            self.assertFalse(any(rects_overlap(roi["background"], other["band"]) for other in rois))

    def test_bright_suggestions_flat_fallback_and_region_clamping(self):
        pixels = np.full((40, 120), 10, dtype=np.uint8)
        pixels[15:24, 15:30] = 210
        pixels[15:24, 75:90] = 210
        path = self.save(pixels)
        samples = [{"id": "a"}, {"id": "b"}]
        rois = suggest_rois(path, {"x": -5, "y": -5, "w": 130, "h": 50}, samples, "bright")
        for roi, expected in zip(rois, [22, 82]):
            self.assertLess(abs(roi["band"]["x"] + roi["band"]["w"] / 2 - expected), 14)
            self.assertTrue(measure_roi(path, roi, "bright")["valid"])
        path = self.save(np.full((40, 120), 100, dtype=np.uint8))
        rois = suggest_rois(path, {"x": 0, "y": 0, "w": 120, "h": 40}, samples)
        self.assertTrue(all("等距" in roi["suggestionNote"] for roi in rois))
        with self.assertRaises(ValueError):
            suggest_rois(path, {"x": 0, "y": 0, "w": 8, "h": 8}, samples)

    def test_user_images_six_geometry_candidates_and_immutable_sources(self):
        example_dir = Path(__file__).resolve().parents[1] / "examples"
        if not (example_dir / "PHO.png").exists():
            self.skipTest("Optional WBTEST input not supplied")
        cases = [
            ("PHO.png", {"x": 17, "y": 16, "w": 420, "h": 40}, [50, 122, 195, 267, 337, 405]),
            ("total.png", {"x": 14, "y": 16, "w": 423, "h": 34}, [41, 111, 188, 258, 333, 409]),
        ]
        samples = [{"id": f"sample-{i}"} for i in range(6)]
        for name, region, expected_centers in cases:
            with self.subTest(image=name):
                path = example_dir / name
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                data, metadata = read_image(path)
                self.assertEqual(metadata["bitDepth"], 8)
                self.assertTrue(any("Screenshot" in s for s in metadata["warnings"]))
                rois = suggest_rois(path, region, samples)
                self.assertEqual(len(rois), 6)
                for roi, expected in zip(rois, expected_centers):
                    band = roi["band"]
                    self.assertLess(abs(band["x"] + band["w"] / 2 - expected), 12)
                    self.assertFalse(roi["confirmed"])
                    validate_rect(band, metadata["width"], metadata["height"])
                    self.assertFalse(any(rects_overlap(roi["background"], other["band"]) for other in rois))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
