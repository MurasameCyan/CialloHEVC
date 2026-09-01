import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "CialloHEVC.py"
SPEC = importlib.util.spec_from_file_location("ciallo_hevc", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeEntry:
    def __init__(self, border_color="default"):
        self.border_color = border_color
        self.state = "normal"
        self.xview = None

    def xview_moveto(self, fraction):
        self.xview = fraction

    def configure(self, **kwargs):
        if "border_color" in kwargs:
            self.border_color = kwargs["border_color"]
        if "state" in kwargs:
            self.state = kwargs["state"]


class FakeControl:
    def __init__(self):
        self.state = "normal"
        self.options = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)
        if "state" in kwargs:
            self.state = kwargs["state"]


class FakeConfig:
    def __init__(self, sync_dirs=False):
        self.sync_dirs = sync_dirs
        self.saved = 0

    def save(self):
        self.saved += 1


class FakeGUI:
    scroll_entry_to_end = MODULE.ConverterGUI.scroll_entry_to_end
    refresh_dir_entry = MODULE.ConverterGUI.refresh_dir_entry
    sync_output_to_input = MODULE.ConverterGUI.sync_output_to_input
    _apply_sync_dirs_state = MODULE.ConverterGUI._apply_sync_dirs_state

    def __init__(self, clipboard_value=None, clipboard_error=None):
        self.clipboard_value = clipboard_value
        self.clipboard_error = clipboard_error
        self._dir_border_color = "default"

    def clipboard_get(self):
        if self.clipboard_error:
            raise self.clipboard_error
        return self.clipboard_value


class DirectoryPasteTests(unittest.TestCase):
    def test_paste_clipboard_text_updates_target_variable(self):
        gui = FakeGUI(r"D:\Videos\Input")
        target = FakeVar("old")

        MODULE.ConverterGUI.paste_clipboard_to_var(gui, target)

        self.assertEqual(target.value, r"D:\Videos\Input")

    def test_quoted_path_from_explorer_is_unwrapped(self):
        gui = FakeGUI('  "D:\\Videos\\Input"  ')
        target = FakeVar("old")

        MODULE.ConverterGUI.paste_clipboard_to_var(gui, target)

        self.assertEqual(target.value, r"D:\Videos\Input")

    def test_empty_clipboard_does_not_replace_target(self):
        gui = FakeGUI("")
        target = FakeVar("old")

        MODULE.ConverterGUI.paste_clipboard_to_var(gui, target)

        self.assertEqual(target.value, "old")

    def test_clipboard_error_does_not_replace_target(self):
        gui = FakeGUI(clipboard_error=RuntimeError("clipboard unavailable"))
        target = FakeVar("old")

        MODULE.ConverterGUI.paste_clipboard_to_var(gui, target)

        self.assertEqual(target.value, "old")

    def test_paste_scrolls_entry_to_path_tail(self):
        gui = FakeGUI(r"D:\Videos\A\Very\Long\Input\Path")
        entry = FakeEntry()

        MODULE.ConverterGUI.paste_clipboard_to_var(gui, FakeVar("old"), entry)

        self.assertEqual(entry.xview, 1)


class DirEntryValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gui = FakeGUI()
        self.entry = FakeEntry()

    def test_existing_directory_keeps_default_border(self):
        self.gui.refresh_dir_entry(self.entry, FakeVar(self.tmp.name))

        self.assertEqual(self.entry.border_color, "default")

    def test_missing_directory_turns_border_red(self):
        missing = os.path.join(self.tmp.name, "nope")

        self.gui.refresh_dir_entry(self.entry, FakeVar(missing))

        self.assertEqual(self.entry.border_color, ("#F44336", "#EF5350"))

    def test_output_directory_may_be_missing_when_parent_exists(self):
        planned = os.path.join(self.tmp.name, "out")

        self.gui.refresh_dir_entry(self.entry, FakeVar(planned), allow_parent=True)

        self.assertEqual(self.entry.border_color, "default")

    def test_output_directory_with_missing_parent_turns_red(self):
        planned = os.path.join(self.tmp.name, "nope", "out")

        self.gui.refresh_dir_entry(self.entry, FakeVar(planned), allow_parent=True)

        self.assertEqual(self.entry.border_color, ("#F44336", "#EF5350"))


class DirectorySyncTests(unittest.TestCase):
    def setUp(self):
        self.gui = FakeGUI()
        self.gui.input_dir_var = FakeVar(r"H:\Era\3D\アパタイト")
        self.gui.output_dir_var = FakeVar(r"D:\somewhere\else")
        self.gui.sync_dirs_var = FakeVar(False)
        self.gui.output_entry = FakeEntry()
        self.gui.output_paste_btn = FakeControl()
        self.gui.output_browse_btn = FakeControl()
        self.gui.sync_btn = FakeControl()
        self.gui.config = FakeConfig()

    def call_gui_method(self, name):
        method = getattr(MODULE.ConverterGUI, name, None)
        self.assertIsNotNone(method, f"ConverterGUI.{name} is missing")
        method(self.gui)

    def test_enabled_sync_sets_output_to_input(self):
        self.gui.sync_dirs_var.set(True)

        self.call_gui_method("sync_output_to_input")

        self.assertEqual(self.gui.output_dir_var.value, r"H:\Era\3D\アパタイト")
        self.assertEqual(self.gui.output_entry.xview, 1)

    def test_enabled_sync_tracks_input_changes(self):
        self.gui.sync_dirs_var.set(True)
        self.gui.input_dir_var.set(r"E:\new\input")

        self.call_gui_method("sync_output_to_input")

        self.assertEqual(self.gui.output_dir_var.value, r"E:\new\input")

    def test_disabled_sync_keeps_output_independent(self):
        self.gui.sync_dirs_var.set(False)

        self.call_gui_method("sync_output_to_input")

        self.assertEqual(self.gui.output_dir_var.value, r"D:\somewhere\else")

    def test_empty_input_does_not_clear_output(self):
        self.gui.sync_dirs_var.set(True)
        self.gui.input_dir_var.set("   ")

        self.call_gui_method("sync_output_to_input")

        self.assertEqual(self.gui.output_dir_var.value, r"D:\somewhere\else")

    def test_toggle_updates_output_control_states(self):
        self.call_gui_method("toggle_input_output_sync")

        self.assertTrue(self.gui.sync_dirs_var.get())
        self.assertEqual(self.gui.output_entry.state, "disabled")
        self.assertEqual(self.gui.output_paste_btn.state, "disabled")
        self.assertEqual(self.gui.output_browse_btn.state, "disabled")
        self.assertEqual(self.gui.sync_btn.options["fg_color"], ("#2196F3", "#1976D2"))
        self.assertEqual(self.gui.sync_btn.options["text_color"], ("white", "white"))

        self.call_gui_method("toggle_input_output_sync")

        self.assertFalse(self.gui.sync_dirs_var.get())
        self.assertEqual(self.gui.output_entry.state, "normal")
        self.assertEqual(self.gui.output_paste_btn.state, "normal")
        self.assertEqual(self.gui.output_browse_btn.state, "normal")
        self.assertEqual(self.gui.sync_btn.options["fg_color"], "transparent")
        self.assertEqual(self.gui.sync_btn.options["text_color"], ("gray40", "gray75"))


class DirectorySyncPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.gui = FakeGUI()
        self.gui.input_dir_var = FakeVar(r"H:\Era\3D\アパタイト")
        self.gui.output_dir_var = FakeVar(r"D:\somewhere\else")
        self.gui.sync_dirs_var = FakeVar(False)
        self.gui.output_entry = FakeEntry()
        self.gui.output_paste_btn = FakeControl()
        self.gui.output_browse_btn = FakeControl()
        self.gui.sync_btn = FakeControl()
        self.gui.config = FakeConfig()

    def test_config_defaults_to_sync_disabled(self):
        self.assertFalse(MODULE.Config().sync_dirs)

    def test_toggle_writes_state_to_config_and_saves(self):
        MODULE.ConverterGUI.toggle_input_output_sync(self.gui)

        self.assertTrue(self.gui.config.sync_dirs)
        self.assertEqual(self.gui.config.saved, 1)

        MODULE.ConverterGUI.toggle_input_output_sync(self.gui)

        self.assertFalse(self.gui.config.sync_dirs)
        self.assertEqual(self.gui.config.saved, 2)

    def test_saved_state_round_trips_through_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with mock.patch.object(MODULE.Config, "_config_path", staticmethod(lambda: path)):
                saved = MODULE.Config()
                saved.sync_dirs = True
                saved.save()

                self.assertTrue(json.loads(pathlib.Path(path).read_text(encoding="utf-8"))["sync_dirs"])

                loaded = MODULE.Config()
                loaded.load()

        self.assertTrue(loaded.sync_dirs)

    def test_enabled_state_restores_locked_output_without_toggling(self):
        self.gui.sync_dirs_var.set(True)

        self.gui._apply_sync_dirs_state()

        self.assertTrue(self.gui.sync_dirs_var.get())
        self.assertEqual(self.gui.output_entry.state, "disabled")
        self.assertEqual(self.gui.sync_btn.options["fg_color"], ("#2196F3", "#1976D2"))
        self.assertEqual(self.gui.output_dir_var.value, r"H:\Era\3D\アパタイト")
        self.assertEqual(self.gui.config.saved, 0)

    def test_disabled_state_restores_editable_output(self):
        self.gui._apply_sync_dirs_state()

        self.assertEqual(self.gui.output_entry.state, "normal")
        self.assertEqual(self.gui.sync_btn.options["fg_color"], "transparent")
        self.assertEqual(self.gui.output_dir_var.value, r"D:\somewhere\else")

    def test_startup_seeds_switch_from_config_and_applies_state(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn("ctk.BooleanVar(value=bool(self.config.sync_dirs))", source)
        self.assertIn("self._apply_sync_dirs_state()", source)
        self.assertIn("self.config.sync_dirs = enabled", source)


class DirectorySyncControlTests(unittest.TestCase):
    def test_directory_sync_uses_compact_chain_button(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertIn("self.sync_btn = ctk.CTkButton(", source)
        self.assertIn('text="🔗"', source)
        self.assertNotIn('text="输入=输出"', source)
        self.assertNotIn("sync_row", source)
        self.assertNotIn("self.sync_switch", source)
        self.assertIn('self.sync_btn.place(x=25, y=40, anchor="center")', source)
        self.assertNotIn("link_btn", source)


if __name__ == "__main__":
    unittest.main()
