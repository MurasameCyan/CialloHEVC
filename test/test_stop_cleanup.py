import importlib.util
import os
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "CialloHEVC.py"
SPEC = importlib.util.spec_from_file_location("ciallo_hevc_stop", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_converter(tmpdir):
    config = MODULE.Config()
    config.ffmpeg_path = "ffmpeg"
    config.ssim_log = False
    converter = MODULE.Converter(config, {})
    converter.logs = []
    converter.log_cb = lambda msg, overwrite=False: converter.logs.append(msg)
    return converter


class CleanupPartialOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.converter = make_converter(self.tmp.name)

    def test_removes_existing_partial_file(self):
        partial = os.path.join(self.tmp.name, "clip(HEVC).mp4")
        with open(partial, "wb") as fh:
            fh.write(b"partial")

        removed = self.converter.cleanup_partial_output(partial)

        self.assertTrue(removed)
        self.assertFalse(os.path.exists(partial))

    def test_missing_file_is_not_an_error(self):
        missing = os.path.join(self.tmp.name, "nope.mp4")

        self.assertFalse(self.converter.cleanup_partial_output(missing))

    def test_locked_file_reports_warning_without_raising(self):
        locked = os.path.join(self.tmp.name, "locked(HEVC).mp4")
        with open(locked, "wb") as fh:
            fh.write(b"data")
        real_remove = os.remove
        os.remove = lambda path: (_ for _ in ()).throw(OSError("in use"))
        self.addCleanup(setattr, os, "remove", real_remove)
        try:
            result = self.converter.cleanup_partial_output(locked, retries=2)
        finally:
            os.remove = real_remove

        self.assertFalse(result)
        self.assertTrue(any("无法删除未完成文件" in line for line in self.converter.logs))


class StopDuringProcessFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.in_dir = os.path.join(self.tmp.name, "in")
        self.out_dir = os.path.join(self.tmp.name, "out")
        self.log_dir = os.path.join(self.tmp.name, "log")
        for d in (self.in_dir, self.out_dir, self.log_dir):
            os.makedirs(d)
        self.source = os.path.join(self.in_dir, "clip.mp4")
        with open(self.source, "wb") as fh:
            fh.write(b"source")

        self.converter = make_converter(self.tmp.name)
        self.converter.get_video_encoder = lambda path: "h264"
        self.converter.probe_color_metadata = lambda path: {}
        self.converter.detect_color_info = lambda path: []
        self.converter._resolve_out_ext = lambda path: ".mp4"

    def expected_output(self):
        return os.path.join(self.out_dir, "clip(HEVC).mp4")

    def test_stop_during_encode_removes_partial_output(self):
        def fake_encode(input_file, output_file, quality):
            with open(output_file, "wb") as fh:
                fh.write(b"halfway")
            self.converter.should_stop = True
            return False

        self.converter.encode = fake_encode

        result = self.converter.process_file(self.source, self.out_dir, self.log_dir)

        self.assertFalse(result)
        self.assertFalse(os.path.exists(self.expected_output()))

    def test_stop_during_ssim_removes_partial_output(self):
        def fake_encode(input_file, output_file, quality):
            with open(output_file, "wb") as fh:
                fh.write(b"encoded")
            return True

        def fake_ssim(input_file, output_file, ssim_log):
            self.converter.should_stop = True
            return False

        self.converter.encode = fake_encode
        self.converter.calc_ssim = fake_ssim

        result = self.converter.process_file(self.source, self.out_dir, self.log_dir)

        self.assertFalse(result)
        self.assertFalse(os.path.exists(self.expected_output()))

    def test_completed_file_is_kept_even_if_stop_requested_after(self):
        def fake_encode(input_file, output_file, quality):
            with open(output_file, "wb") as fh:
                fh.write(b"encoded")
            return True

        self.converter.encode = fake_encode
        self.converter.calc_ssim = lambda *a: True
        self.converter.parse_ssim = lambda log: 0.999
        self.converter.config.target_ssim = 0.95

        result = self.converter.process_file(self.source, self.out_dir, self.log_dir)
        self.converter.should_stop = True

        self.assertTrue(result)
        self.assertTrue(os.path.exists(self.expected_output()))

    def test_force_path_stop_removes_partial_output(self):
        def fake_encode(input_file, output_file, quality):
            with open(output_file, "wb") as fh:
                fh.write(b"halfway")
            self.converter.should_stop = True
            return False

        self.converter.encode = fake_encode

        result = self.converter.process_file_force(self.source, self.out_dir, self.log_dir)

        self.assertFalse(result)
        self.assertFalse(os.path.exists(self.expected_output()))


if __name__ == "__main__":
    unittest.main()
