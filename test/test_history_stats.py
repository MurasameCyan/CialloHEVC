import importlib.util
import json
import os
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "CialloHEVC.py"
SPEC = importlib.util.spec_from_file_location("ciallo_hevc_history", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

SOURCE = MODULE_PATH.read_text(encoding="utf-8")


class HistoryStoreTests(unittest.TestCase):
    """history.json 的读写必须保住已累计的数据。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        real_app_dir = MODULE.Config._app_dir
        MODULE.Config._app_dir = staticmethod(lambda: self.tmp.name)
        self.addCleanup(setattr, MODULE.Config, "_app_dir", real_app_dir)
        self.path = os.path.join(self.tmp.name, "history.json")

    def write_raw(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read_raw(self):
        with open(self.path, "r", encoding="utf-8") as fh:
            return fh.read()

    def test_missing_file_starts_from_zero(self):
        self.assertEqual(MODULE.Converter._load_history(), (0, 0, True))

    def test_update_accumulates(self):
        self.assertEqual(MODULE.Converter._update_history(10, 4), (10, 4))
        self.assertEqual(MODULE.Converter._update_history(5, 1), (15, 5))
        self.assertEqual(MODULE.Converter._load_history(), (15, 5, True))

    def test_corrupt_file_is_never_overwritten(self):
        self.write_raw('{"total_src": 999, "total_o')  # 写入中途被截断
        self.assertIsNone(MODULE.Converter._update_history(10, 4))
        self.assertEqual(self.read_raw(), '{"total_src": 999, "total_o')

    def test_non_numeric_values_are_rejected(self):
        self.write_raw(json.dumps({"total_src": None, "total_out": 5}))
        self.assertEqual(MODULE.Converter._load_history(), (0, 0, False))
        self.assertIsNone(MODULE.Converter._update_history(10, 4))
        self.assertIn("total_src", self.read_raw())

    def test_save_leaves_no_temp_file_behind(self):
        MODULE.Converter._save_history(7, 3)
        leftovers = [n for n in os.listdir(self.tmp.name) if n != "history.json"]
        self.assertEqual(leftovers, [])
        self.assertEqual(json.loads(self.read_raw()),
                         {"total_src": 7, "total_out": 3})

    def test_tooltip_reports_a_broken_file_instead_of_zeros(self):
        self.write_raw("not json at all")
        text = MODULE.Converter._format_history_tooltip()
        self.assertNotIn("0 MB", text)
        self.assertIn("history.json", text)

    def test_tooltip_formats_totals(self):
        MODULE.Converter._save_history(3 * 1024 ** 3, 1 * 1024 ** 3)
        text = MODULE.Converter._format_history_tooltip()
        self.assertIn("3.00 GB", text)
        self.assertIn("2.00 GB", text)  # 节省量


class PerFileCommitTests(unittest.TestCase):
    """每个文件完成即落盘，任务中途退出不会丢掉整轮统计。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        real_app_dir = MODULE.Config._app_dir
        MODULE.Config._app_dir = staticmethod(lambda: self.tmp.name)
        self.addCleanup(setattr, MODULE.Config, "_app_dir", real_app_dir)

        config = MODULE.Config()
        self.converter = MODULE.Converter(config, {})
        self.logs = []
        self.converter.log_cb = lambda msg, overwrite=False: self.logs.append(msg)

    def test_finished_file_is_written_immediately(self):
        self.converter.size_map["clip.mp4"] = (100, 40)
        self.converter._commit_file_history("clip.mp4")
        self.assertEqual(MODULE.Converter._load_history(), (100, 40, True))

    def test_unknown_file_writes_nothing(self):
        self.converter._commit_file_history("missing.mp4")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "history.json")))

    def test_broken_file_is_reported_in_the_log(self):
        with open(os.path.join(self.tmp.name, "history.json"), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        self.converter.size_map["clip.mp4"] = (100, 40)
        self.converter._commit_file_history("clip.mp4")
        self.assertTrue(any("history.json" in line for line in self.logs))

    def test_history_is_committed_from_exactly_one_place(self):
        """run() 收尾处不能再累加一次，否则每个文件被统计两遍。"""
        self.assertEqual(SOURCE.count("Converter._update_history("), 1)


class ThreadCallbackTests(unittest.TestCase):
    """窗口已退出后，转换线程的界面回调不能抛异常。"""

    class FakeGUI:
        def after(self, delay, fn):
            raise RuntimeError("main thread is not in main loop")

    def test_ui_swallows_dead_window(self):
        MODULE.ConverterGUI._ui(self.FakeGUI(), lambda: None)

    def test_worker_callbacks_all_go_through_ui(self):
        """log / 进度 / 统计 回调必须走 _ui，否则关窗时会打出线程回溯。"""
        for marker in ("self._ui(_log)", "self._ui(_update)"):
            self.assertIn(marker, SOURCE)
        self.assertNotIn("self.after(0, _log)", SOURCE)
        self.assertNotIn("self.after(0, _update)", SOURCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
