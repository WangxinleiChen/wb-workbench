"""Numerical algorithm checks, not biological validation.

Exact integer polynomials: maximum background error < 1e-7 stored units,
net integral error < 1e-5 units*pixel. Quantized fields: mean absolute error
< 0.25 units and maximum < 0.6 units. No image appearance assertion substitutes
for these known numerical expectations.
"""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import background


class BackgroundAlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def artifact(self, array, polarity="dark", region=None, params=None):
        self.counter += 1
        source = self.root / f"source-{self.counter}.tif"
        Image.fromarray(array).save(source)
        region = region or {"x": 0, "y": 0, "w": array.shape[1], "h": array.shape[0]}
        destination = self.root / f"artifact-{self.counter}"
        metadata = background.create_artifact(source, region, polarity, params, destination)
        published = self.root / f"published-{self.counter}" / metadata["key"]
        published.parent.mkdir()
        destination.rename(published)
        return source, published, metadata

    @staticmethod
    def roi(x=12, y=29, w=9, h=4):
        # An irrelevant legacy rectangle deliberately overlaps the band: model
        # measurements must not subtract or validate it as a second background.
        band = {"x": x, "y": y, "w": w, "h": h}
        return {"band": band, "background": band.copy()}

    def test_constant_background_dark_bright_8_and_16_bit(self):
        for dtype, baseline, amplitude in ((np.uint8, 120, 35), (np.uint16, 30000, 4200)):
            for polarity in ("dark", "bright"):
                with self.subTest(dtype=dtype, polarity=polarity):
                    values = np.full((64, 96), baseline, dtype=dtype)
                    values[29:33, 12:21] = baseline + (-amplitude if polarity == "dark" else amplitude)
                    source, destination, metadata = self.artifact(values, polarity)
                    fitted = np.load(destination / "background.npy")
                    corrected = np.load(destination / "corrected.npy")
                    self.assertEqual(fitted.dtype, np.float64)
                    self.assertEqual(corrected.dtype, np.float64)
                    self.assertLess(float(np.max(np.abs(fitted - baseline))), 1e-7)
                    result = background.measure_model(source, self.roi(), destination)
                    self.assertTrue(result["valid"])
                    self.assertAlmostEqual(result["net"], amplitude * 36, delta=1e-5)
                    self.assertAlmostEqual(result["backgroundContribution"], baseline * 36, delta=1e-5)
                    self.assertEqual(result["backgroundArea"], 0)
                    self.assertFalse(metadata["legacyBackgroundRectangleUsed"])

    def test_slow_background_exact_linear8_and_quadratic16(self):
        yy, xx = np.mgrid[:64, :96]
        cases = ((80 + xx + yy, np.uint8, 12),
                 (1000 + 2 * xx + 3 * yy + xx * yy + xx * xx, np.uint16, 300))
        for expected, dtype, amplitude in cases:
            for polarity in ("dark", "bright"):
                with self.subTest(dtype=dtype, polarity=polarity):
                    values = expected.copy()
                    values[29:33, 12:21] += (-amplitude if polarity == "dark" else amplitude)
                    source, destination, _ = self.artifact(values.astype(dtype), polarity)
                    self.assertLess(float(np.max(np.abs(np.load(destination / "background.npy") - expected))), 1e-7)
                    measurement = background.measure_model(source, self.roi(), destination)
                    self.assertAlmostEqual(measurement["net"], amplitude * 36, delta=1e-5)

    def test_quantized_quadratic_has_explicit_error_bound(self):
        yy, xx = np.mgrid[:64, :96]
        true_background = 95 + 0.27 * xx + 0.19 * yy + 0.0018 * xx * xx - 0.001 * xx * yy
        values = np.rint(true_background).astype(np.uint8)
        values[29:33, 12:21] -= 20
        _, destination, _ = self.artifact(values)
        error = np.abs(np.load(destination / "background.npy") - true_background)
        self.assertLess(float(error.mean()), 0.25)
        self.assertLess(float(error.max()), 0.6)

    def test_noisy_smooth_background_has_bounded_estimation_error(self):
        yy, xx = np.mgrid[:128, :192]
        true_background = 10000 + 2 * xx + yy
        noise = np.random.default_rng(20260920).normal(0, 1.5, true_background.shape)
        values = np.rint(true_background + noise).astype(np.uint16)
        values[29:33, 12:21] -= 30
        source, directory, _ = self.artifact(values)
        fitted = np.load(directory / "background.npy")
        self.assertLess(float(np.max(np.abs(fitted - true_background))), 0.3)
        measurement = background.measure_model(source, self.roi(), directory)
        expected = float((true_background[29:33, 12:21] - values[29:33, 12:21].astype(float)).sum())
        self.assertAlmostEqual(measurement["net"], expected, delta=0.3 * 36)

    def test_pure_background_is_not_a_publishable_positive_ratio_signal(self):
        for dtype, value in ((np.uint8, 141), (np.uint16, 49157)):
            with self.subTest(dtype=dtype):
                source, destination, _ = self.artifact(np.full((64, 96), value, dtype=dtype))
                result = background.measure_model(source, self.roi(), destination)
                self.assertFalse(result["valid"])
                self.assertLessEqual(abs(result["net"]), result["numericalTolerance"])
                self.assertLess(abs(result["net"]), 1e-5)

    def test_weak_one_unit_band_and_edge_band(self):
        for polarity in ("dark", "bright"):
            for at_edge in (False, True):
                with self.subTest(polarity=polarity, edge=at_edge):
                    values = np.full((64, 96), 130, dtype=np.uint8)
                    x, y = (0, 0) if at_edge else (12, 29)
                    values[y:y + 4, x:x + 9] = 129 if polarity == "dark" else 131
                    source, destination, _ = self.artifact(values, polarity)
                    result = background.measure_model(source, self.roi(x, y), destination)
                    self.assertTrue(result["valid"])
                    self.assertAlmostEqual(result["net"], 36, delta=1e-7)

    def test_negative_values_are_preserved_and_not_used_for_ratios(self):
        values = np.full((64, 96), 100, dtype=np.uint8)
        values[29:33, 12:21] = 92
        values[40:44, 40:49] = 130
        source, destination, metadata = self.artifact(values, "bright")
        corrected = np.load(destination / "corrected.npy")
        self.assertAlmostEqual(float(corrected[30, 15]), -8, delta=1e-8)
        result = background.measure_model(source, self.roi(), destination)
        self.assertAlmostEqual(result["net"], -288, delta=1e-7)
        self.assertFalse(result["valid"])
        self.assertFalse(metadata["clippedForQuantification"])
        self.assertLess(metadata["display"]["corrected"]["signedMin"], 0)

    def test_region_coordinates_and_full_size_display(self):
        values = np.full((80, 120), 220, dtype=np.uint8)
        values[10:70, 10:110] = 130
        values[29:33, 12:21] = 110
        region = {"x": 10, "y": 10, "w": 100, "h": 60}
        source, destination, metadata = self.artifact(values, region=region)
        self.assertEqual(np.load(destination / "background.npy").shape, (60, 100))
        with Image.open(destination / "corrected.png") as image:
            self.assertEqual(image.size, (120, 80))
            self.assertEqual(image.getpixel((0, 0)), 128)
        result = background.measure_model(source, self.roi(), destination)
        self.assertAlmostEqual(result["net"], 720, delta=1e-7)
        invalid = background.measure_model(source, self.roi(0, 0), destination)
        self.assertFalse(invalid["valid"])
        self.assertIsNone(invalid["net"])
        self.assertEqual(metadata["region"], region)

    def test_new_preview_always_starts_from_original_and_is_deterministic(self):
        values = np.full((64, 96), 170, dtype=np.uint8)
        values[29:33, 12:21] = 110
        source, first, metadata = self.artifact(values)
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        second = self.root / "second"
        again = background.create_artifact(source, metadata["region"], "dark", {"clipSigma": 2.5}, second)
        self.assertEqual(metadata["key"], again["key"])
        self.assertEqual(metadata["fileHashes"], again["fileHashes"])
        self.assertEqual(source_hash, hashlib.sha256(source.read_bytes()).hexdigest())
        for name, digest in metadata["fileHashes"].items():
            self.assertEqual(digest, hashlib.sha256((first / name).read_bytes()).hexdigest())

    def test_cache_identity_tracks_every_numerical_input(self):
        sha = "a" * 64
        region = {"x": 2, "y": 3, "w": 100, "h": 40}
        key = background.cache_key(sha, region, "dark", {"clipSigma": 2.5})
        alternatives = (
            background.cache_key("b" * 64, region, "dark", {"clipSigma": 2.5}),
            background.cache_key(sha, {**region, "x": 3}, "dark", {"clipSigma": 2.5}),
            background.cache_key(sha, region, "bright", {"clipSigma": 2.5}),
            background.cache_key(sha, region, "dark", {"clipSigma": 3}),
        )
        self.assertTrue(all(other != key for other in alternatives))
        self.assertEqual(key, background.cache_key(sha, dict(reversed(list(region.items()))), "dark", {}))

    def test_illegal_parameters_regions_and_polarity_rejected(self):
        for params in ({"clipSigma": 0}, {"clipSigma": 7}, {"clipSigma": True},
                       {"clipSigma": float("nan")}, {"clipSigma": float("inf")},
                       {"clipSigma": "2.5"}, {"radius": 5}, []):
            with self.subTest(params=params), self.assertRaises(ValueError):
                background.validate_params(params)
        for region in ({"x": -1, "y": 0, "w": 32, "h": 32},
                       {"x": 0, "y": 0, "w": 7, "h": 32},
                       {"x": 0, "y": 0, "w": 2001, "h": 2000},
                       {"x": 0.5, "y": 0, "w": 32, "h": 32}):
            with self.subTest(region=region), self.assertRaises(ValueError):
                background.cache_key("a" * 64, region, "dark", {})
        with self.assertRaises(ValueError):
            background.cache_key("a" * 64, {"x": 0, "y": 0, "w": 32, "h": 32}, "up", {})
        values = np.full((16, 16), 100, dtype=np.uint8)
        with self.assertRaises(ValueError):
            self.artifact(values, region={"x": 10, "y": 10, "w": 16, "h": 16})

    def test_corrupt_array_and_changed_source_are_not_measured(self):
        values = np.full((64, 96), 130, dtype=np.uint8)
        values[29:33, 12:21] = 110
        source, destination, _ = self.artifact(values)
        with (destination / "background.npy").open("ab") as handle:
            handle.write(b"corruption")
        invalid = background.measure_model(source, self.roi(), destination)
        self.assertFalse(invalid["valid"])
        self.assertIsNone(invalid["net"])
        self.assertTrue(any("校验失败" in w for w in invalid["warnings"]))
        source, destination, _ = self.artifact(values)
        Image.fromarray(np.full(values.shape, 100, dtype=np.uint8)).save(source)
        invalid = background.measure_model(source, self.roi(), destination)
        self.assertFalse(invalid["valid"])
        self.assertTrue(any("SHA-256" in w for w in invalid["warnings"]))

    def test_complete_valid_artifact_cannot_be_relabelled_as_another_key(self):
        values = np.full((64, 96), 130, dtype=np.uint8)
        values[29:33, 12:21] = 110
        source, first, metadata = self.artifact(values)
        staging = self.root / "other-staging"
        other_metadata = background.create_artifact(
            source, metadata["region"], "dark", {"clipSigma": 3}, staging)
        second = first.parent / other_metadata["key"]
        staging.rename(second)
        self.assertTrue(background.measure_model(source, self.roi(), second)["valid"])
        # All files remain individually valid and mutually consistent, but now
        # reside under the prior parameter set's key. This must fail closed.
        shutil.copytree(second, first, dirs_exist_ok=True)
        invalid = background.measure_model(source, self.roi(), first)
        self.assertFalse(invalid["valid"])
        self.assertIsNone(invalid["net"])
        self.assertTrue(any("目录名与缓存标识不匹配" in w for w in invalid["warnings"]))

    def test_expected_key_binds_measurement_to_formally_applied_state(self):
        values = np.full((64, 96), 130, dtype=np.uint8)
        values[29:33, 12:21] = 110
        source, directory, metadata = self.artifact(values)
        valid = background.measure_model(source, self.roi(), directory, expected_key=metadata["key"])
        self.assertTrue(valid["valid"])
        invalid = background.measure_model(source, self.roi(), directory, expected_key="a" * 64)
        self.assertFalse(invalid["valid"])
        self.assertIsNone(invalid["net"])
        self.assertTrue(any("当前正式应用的缓存标识不匹配" in w for w in invalid["warnings"]))

    def test_sample_cap_and_reproducible_sparse_background(self):
        yy, xx = np.mgrid[:300, :500]
        values = (1000 + xx + 2 * yy).astype(np.uint16)
        values[100:108, 100:130] -= 70
        _, directory, metadata = self.artifact(values)
        self.assertLessEqual(metadata["fitStats"]["sampleCount"], background.MAX_FIT_SAMPLES)
        self.assertLess(float(np.max(np.abs(np.load(directory / "background.npy") - (1000 + xx + 2 * yy)))), 1e-7)

    def test_manifest_roundtrip_and_no_overwrite(self):
        values = np.full((32, 32), 100, dtype=np.uint8)
        source, destination, metadata = self.artifact(values)
        self.assertEqual(metadata, json.loads((destination / "manifest.json").read_text()))
        with self.assertRaises(ValueError):
            background.create_artifact(source, metadata["region"], "dark", {}, destination)


if __name__ == "__main__":
    unittest.main()
