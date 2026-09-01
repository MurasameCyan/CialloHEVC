import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "CialloHEVC.py"
SPEC = importlib.util.spec_from_file_location("ciallo_hevc", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeTextbox:
    def __init__(self, mapped=True):
        self.text = ""
        self.see_calls = 0
        self.mapped = mapped

    def winfo_ismapped(self):
        return self.mapped

    def insert(self, index, text):
        self.text += text

    def delete(self, start, end):
        self.text = self.text.rstrip("\n").rsplit("\n", 1)[0]

    def see(self, index):
        self.see_calls += 1


class FakeButton:
    def __init__(self):
        self.fg_color = None
        self.configure_calls = 0

    def configure(self, **kwargs):
        self.configure_calls += 1
        self.fg_color = kwargs.get("fg_color", self.fg_color)


ON_COLOR = ("#1976D2", "#1565C0")
OFF_COLOR = ("#9E9E9E", "#424242")


class FakeGUI:
    log = MODULE.ConverterGUI.log
    _set_switch_style = MODULE.ConverterGUI._set_switch_style
    _on_log_scroll = MODULE.ConverterGUI._on_log_scroll
    toggle_auto_scroll = MODULE.ConverterGUI.toggle_auto_scroll

    def __init__(self, auto_scroll=True, with_scrollbar=True, mapped=True):
        self.auto_scroll = auto_scroll
        self.auto_scroll_btn = FakeButton()
        self.log_text = FakeTextbox(mapped)
        self.scrollbar_calls = []
        self._log_scrollbar_set = self._record_scrollbar if with_scrollbar else None

    def _record_scrollbar(self, first, last):
        self.scrollbar_calls.append((first, last))

    def _ui(self, fn):
        fn()


class LogFollowTests(unittest.TestCase):
    def test_log_follows_last_line_when_enabled(self):
        gui = FakeGUI(auto_scroll=True)

        gui.log("hello")

        self.assertEqual(gui.log_text.text, "hello\n")
        self.assertEqual(gui.log_text.see_calls, 1)

    def test_log_keeps_view_when_disabled(self):
        gui = FakeGUI(auto_scroll=False)

        gui.log("hello")

        self.assertEqual(gui.log_text.text, "hello\n")
        self.assertEqual(gui.log_text.see_calls, 0)

    def test_overwrite_progress_line_respects_disabled_follow(self):
        gui = FakeGUI(auto_scroll=False)
        gui.log("50%")

        gui.log("60%", overwrite=True)

        self.assertEqual(gui.log_text.see_calls, 0)


class LogScrollSyncTests(unittest.TestCase):
    def test_scrolling_up_turns_follow_off(self):
        gui = FakeGUI(auto_scroll=True)

        gui._on_log_scroll("0.40", "0.9762")

        self.assertFalse(gui.auto_scroll)
        self.assertEqual(gui.auto_scroll_btn.fg_color, OFF_COLOR)

    def test_scrolling_back_to_last_line_turns_follow_on(self):
        gui = FakeGUI(auto_scroll=False)

        gui._on_log_scroll("0.90", "1.0")

        self.assertTrue(gui.auto_scroll)
        self.assertEqual(gui.auto_scroll_btn.fg_color, ON_COLOR)

    def test_follow_write_at_bottom_does_not_restyle_button(self):
        # 跟随写入时 Tk 回调的 last 恒为 1.0，状态未变就不该重复配置按钮
        gui = FakeGUI(auto_scroll=True)

        gui._on_log_scroll("0.10", "1.0")
        gui._on_log_scroll("0.11", "1.0")

        self.assertTrue(gui.auto_scroll)
        self.assertEqual(gui.auto_scroll_btn.configure_calls, 0)

    def test_scroll_callback_still_drives_ctk_scrollbar(self):
        gui = FakeGUI()

        gui._on_log_scroll("0.40", "0.90")

        self.assertEqual(gui.scrollbar_calls, [("0.40", "0.90")])

    def test_missing_ctk_scrollbar_does_not_break_sync(self):
        gui = FakeGUI(auto_scroll=True, with_scrollbar=False)

        gui._on_log_scroll("0.40", "0.90")

        self.assertFalse(gui.auto_scroll)

    def test_hidden_log_box_never_turns_follow_off(self):
        # 运行日志分组默认折叠，此时 Tk 回调的比例无意义，不能关掉默认开启的跟随
        gui = FakeGUI(auto_scroll=True, mapped=False)

        gui._on_log_scroll("0.0", "0.0")
        gui._on_log_scroll("0.0", "0.0667")

        self.assertTrue(gui.auto_scroll)
        self.assertEqual(gui.auto_scroll_btn.configure_calls, 0)
        self.assertEqual(gui.scrollbar_calls[-1], ("0.0", "0.0667"))


class ToggleAutoScrollTests(unittest.TestCase):
    def test_manual_disable_keeps_current_view(self):
        gui = FakeGUI(auto_scroll=True)

        gui.toggle_auto_scroll()

        self.assertFalse(gui.auto_scroll)
        self.assertEqual(gui.log_text.see_calls, 0)
        self.assertEqual(gui.auto_scroll_btn.fg_color, OFF_COLOR)

    def test_manual_enable_jumps_to_last_line(self):
        gui = FakeGUI(auto_scroll=False)

        gui.toggle_auto_scroll()

        self.assertTrue(gui.auto_scroll)
        self.assertEqual(gui.log_text.see_calls, 1)
        self.assertEqual(gui.auto_scroll_btn.fg_color, ON_COLOR)


if __name__ == "__main__":
    unittest.main()
