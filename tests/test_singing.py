from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.sing_cover import gpu_slot, mix_cover


class SingingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lonelyfriend-singing-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tone = np.sin(np.arange(44100, dtype="float32") * 2 * np.pi * 220 / 44100)
        self.vocals = self.root / "vocals.wav"
        self.backing = self.root / "backing.wav"
        self.converted = self.root / "converted.wav"
        self.output = self.root / "cover.wav"
        sf.write(self.vocals, self.tone * 0.8, 44100, subtype="FLOAT")
        sf.write(self.backing, np.column_stack([self.tone, self.tone]) * 0.6, 44100, subtype="FLOAT")
        sf.write(self.converted, self.tone[:-441] * 0.2, 44100, subtype="FLOAT")

    def test_mix_keeps_timing_stereo_and_headroom(self):
        report = mix_cover(self.vocals, self.backing, self.converted, self.output)
        mixed, rate = sf.read(self.output, always_2d=True)
        self.assertEqual(rate, 44100)
        self.assertEqual(mixed.shape, (44100, 2))
        self.assertTrue(np.isfinite(mixed).all())
        self.assertGreater(np.std(mixed), 0.1)
        self.assertLessEqual(np.max(np.abs(mixed)), 0.981)
        self.assertEqual(report["alignment_samples"], 441)
        self.assertEqual(sf.info(self.output).subtype, "PCM_16")

    def test_invalid_voice_does_not_write_a_cover(self):
        for samples in (np.zeros(44100), self.tone[:22050]):
            with self.subTest(frames=len(samples)):
                sf.write(self.converted, samples, 44100)
                with self.assertRaises(ValueError):
                    mix_cover(self.vocals, self.backing, self.converted, self.output)
                self.assertFalse(self.output.exists())

    @unittest.skipUnless(os.name == "nt", "Siyin's shared mutex is a Windows contract")
    def test_gpu_mutex_rejects_contention_and_releases_after_failure(self):
        acquired = threading.Event()
        release = threading.Event()
        errors = []

        def owner():
            try:
                with gpu_slot():
                    acquired.set()
                    release.wait(5)
                    raise ValueError("inference failed")
            except ValueError:
                pass
            except Exception as error:
                errors.append(error)
                acquired.set()

        worker = threading.Thread(target=owner)
        worker.start()
        try:
            self.assertTrue(acquired.wait(5))
            self.assertFalse(errors)
            with self.assertRaisesRegex(RuntimeError, "祀音正在合成"):
                with gpu_slot():
                    self.fail("A second inference acquired the occupied GPU mutex")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        with gpu_slot():
            pass


if __name__ == "__main__":
    unittest.main()
