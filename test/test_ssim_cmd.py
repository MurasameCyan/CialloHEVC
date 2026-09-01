"""SSIM 命令必须显式绑定视频流。

回归背景：源文件带封面图(attached_pic)时，`-lavfi ssim` 不标注输入，
ffmpeg 会按全局顺序取前两路视频流 —— 即源文件的正片 + 源文件的封面，
而不是 正片 + 输出文件。两者分辨率不同，ssim 滤镜直接报
"Width and height of input videos must be same." 导致 SSIM 计算失败。
"""

import importlib.util
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "CialloHEVC.py"
SPEC = importlib.util.spec_from_file_location("ciallo_hevc_ssim", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SsimCommandTests(unittest.TestCase):
    def setUp(self):
        config = MODULE.Config()
        config.ffmpeg_path = "ffmpeg.exe"
        self.converter = MODULE.Converter(config, {})
        self.converter.log_cb = lambda msg, overwrite=False: None
        self.captured = {}

        # 只拦截 Popen，拿到真实构造的命令行，不实际跑 ffmpeg
        def fake_popen(cmd, **kwargs):
            self.captured["cmd"] = cmd
            raise RuntimeError("stop before spawning ffmpeg")

        self.converter._resolve_ffmpeg = lambda: "ffmpeg.exe"
        MODULE.subprocess.Popen, self.real_popen = fake_popen, MODULE.subprocess.Popen
        self.addCleanup(lambda: setattr(MODULE.subprocess, "Popen", self.real_popen))

    def _build_cmd(self):
        with tempfile.NamedTemporaryFile(suffix="_ssim.log", delete=False) as fh:
            log_path = fh.name
        self.converter.calc_ssim("src.mp4", "out.mp4", log_path)
        return self.captured["cmd"]

    def test_both_filter_inputs_are_pinned_to_first_video_stream(self):
        cmd = self._build_cmd()
        self.assertIn("[0:v:0][1:v:0]ssim", cmd)

    def test_does_not_use_unlabeled_lavfi_ssim(self):
        cmd = self._build_cmd()
        # 裸 'ssim' 作为 -lavfi 的值就是出问题的写法
        self.assertNotIn("-lavfi", cmd)
        self.assertNotIn("ssim", cmd)

    def test_inputs_are_in_source_then_output_order(self):
        cmd = self._build_cmd()
        inputs = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "-i"]
        self.assertEqual(inputs, ["src.mp4", "out.mp4"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
