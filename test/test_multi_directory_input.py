import unittest
import tempfile
import inspect
import json
import os
from pathlib import Path
from unittest import mock

# 动态导入主模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import CialloHEVC
from CialloHEVC import Config
import customtkinter as ctk
from CialloHEVC import ConverterGUI


def patch_config_path(test_case, cfg_file):
    """把 Config._config_path 指向临时文件，测试结束自动还原。

    必须用 mock.patch.object + staticmethod：直接 Config._config_path 取出的是
    解包后的普通函数，赋值回去会变成实例方法，导致后续 self._config_path() 多传 self。
    """
    patcher = mock.patch.object(Config, "_config_path", staticmethod(lambda: cfg_file))
    patcher.start()
    test_case.addCleanup(patcher.stop)


class TestConfigMigration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cfg_file = os.path.join(self.temp_dir, 'config.json')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_new_config_has_input_paths_list(self):
        """新配置应有 input_paths: List[str] 和 recursive_subdirs: bool"""
        cfg = Config()
        self.assertIsInstance(cfg.input_paths, list)
        self.assertEqual(cfg.input_paths, [])
        self.assertIsInstance(cfg.recursive_subdirs, bool)
        self.assertEqual(cfg.recursive_subdirs, False)

    def test_old_config_migrates_input_dir_to_list(self):
        """旧配置 input_dir 应自动迁移为单元素 input_paths 列表"""
        with open(self.cfg_file, 'w', encoding='utf-8') as f:
            json.dump({'input_dir': r'D:\test', 'output_dir': r'D:\out'}, f)

        patch_config_path(self, self.cfg_file)
        cfg = Config()
        cfg.load()

        self.assertEqual(cfg.input_paths, [r'D:\test'])
        self.assertFalse(hasattr(cfg, 'input_dir'))

    def test_config_save_includes_new_fields(self):
        """保存配置应包含 input_paths 和 recursive_subdirs"""
        patch_config_path(self, self.cfg_file)
        cfg = Config()
        cfg.input_paths = [r'D:\a', r'D:\b']
        cfg.recursive_subdirs = True
        cfg.save()

        with open(self.cfg_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.assertEqual(data['input_paths'], [r'D:\a', r'D:\b'])
        self.assertTrue(data['recursive_subdirs'])


class TestRecursiveButton(unittest.TestCase):
    def setUp(self):
        # GUI 内部自建 Config，必须先改路径再构造，避免读写仓库真实 config.json
        self.temp_dir = tempfile.mkdtemp()
        patch_config_path(self, os.path.join(self.temp_dir, 'config.json'))

        self.gui = ConverterGUI()
        self.gui.withdraw()

    def tearDown(self):
        self.gui.destroy()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_recursive_button_exists(self):
        """递归按钮应存在"""
        self.assertTrue(hasattr(self.gui, 'recursive_btn'))
        self.assertIsInstance(self.gui.recursive_btn, ctk.CTkButton)

    def test_recursive_button_position(self):
        """递归按钮贴分组框右缘、落在分组框上方的标题行留白里"""
        info = self.gui.recursive_btn.place_info()
        self.assertEqual(float(info['relx']), 1.0)
        self.assertEqual(int(info['x']), CialloHEVC.RECURSIVE_BTN_RIGHT_OFFSET)
        self.assertEqual(int(info['y']), CialloHEVC.RECURSIVE_BTN_ABOVE_Y)
        self.assertEqual(info['anchor'], 'center')

    def _realize(self):
        """place/pack 的实际几何要等窗口真正布局后才可读"""
        self.gui.deiconify()
        self.gui.update()
        for _ in range(6):
            self.gui.after(50, self.gui.quit)
            self.gui.mainloop()
            self.gui.update()
        self.gui.withdraw()

    def _span(self, widget):
        origin = self.gui.recursive_btn.master.winfo_rootx()
        left = widget.winfo_rootx() - origin
        return left, left + widget.winfo_width()

    def test_recursive_button_not_clipped(self):
        """按钮实宽会被圆角撑宽，必须完整落在容器内，否则图标不可见"""
        self._realize()
        container_width = self.gui.recursive_btn.master.winfo_width()
        left, right = self._span(self.gui.recursive_btn)
        self.assertGreater(container_width, 1, "容器未完成布局，几何断言无意义")
        self.assertGreaterEqual(left, 0)
        self.assertLessEqual(right, container_width)

    def test_recursive_button_centered_above_input_browse(self):
        """按钮必须与输入行「浏览」水平居中对齐，且完全落在它上方"""
        self._realize()
        r, b = self.gui.recursive_btn, self.gui.input_browse_btn
        self.assertGreater(r.winfo_width(), 1, "按钮未完成布局，几何断言无意义")
        self.assertEqual(
            r.winfo_rootx() + r.winfo_width() // 2,
            b.winfo_rootx() + b.winfo_width() // 2,
            "递归按钮未与输入行「浏览」按钮水平居中对齐",
        )
        self.assertLessEqual(
            r.winfo_rooty() + r.winfo_height(), b.winfo_rooty(),
            "递归按钮未完全位于「浏览」按钮上方",
        )

    def test_recursive_button_does_not_overlap_rows(self):
        """按钮落在分组框上方留白里，不得压住任一输入输出控件"""
        self._realize()
        r = self.gui.recursive_btn
        r_top, r_bottom = r.winfo_rooty(), r.winfo_rooty() + r.winfo_height()
        for name in ('input_entry', 'input_browse_btn', 'output_entry',
                     'output_paste_btn', 'output_browse_btn'):
            w = getattr(self.gui, name)
            self.assertLessEqual(
                r_bottom, w.winfo_rooty(),
                f"递归按钮 {r_top}..{r_bottom} 与 {name} 垂直重叠，会劫持它的点击",
            )

    def test_recursive_button_initial_state(self):
        """递归按钮初始关闭：透明背景、灰色图标"""
        self.assertEqual(self.gui.recursive_enabled, False)
        self.assertEqual(self.gui.recursive_btn.cget('fg_color'), 'transparent')
        self.assertEqual(self.gui.recursive_btn.cget('text_color'), ("gray40", "gray75"))

    def test_toggle_recursive_changes_state(self):
        """点击递归按钮切换状态和配色"""
        self.gui.toggle_recursive()
        self.assertTrue(self.gui.recursive_enabled)
        self.assertEqual(self.gui.recursive_btn.cget('fg_color'), ("#2196F3", "#1976D2"))
        self.assertEqual(self.gui.recursive_btn.cget('text_color'), 'white')
        self.assertTrue(self.gui.config.recursive_subdirs)

        self.gui.toggle_recursive()
        self.assertFalse(self.gui.recursive_enabled)
        self.assertEqual(self.gui.recursive_btn.cget('fg_color'), 'transparent')
        self.assertEqual(self.gui.recursive_btn.cget('text_color'), ("gray40", "gray75"))
        self.assertFalse(self.gui.config.recursive_subdirs)

    def test_recursive_state_restores_from_config(self):
        """启动时从 config 恢复递归状态"""
        self.gui.config.recursive_subdirs = True
        self.gui.config.save()

        gui2 = ConverterGUI()
        gui2.withdraw()
        self.addCleanup(gui2.destroy)

        self.assertTrue(gui2.recursive_enabled)
        self.assertEqual(gui2.recursive_btn.cget('fg_color'), ("#2196F3", "#1976D2"))
        self.assertEqual(gui2.recursive_btn.cget('text_color'), 'white')


class TestMultiDirectoryBrowse(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        patch_config_path(self, os.path.join(self.temp_dir, 'config.json'))
        self.gui = ConverterGUI()
        self.gui.withdraw()
        self.dirs = [os.path.join(self.temp_dir, n) for n in ('a', 'b', 'c')]
        for d in self.dirs:
            os.makedirs(d)

    def tearDown(self):
        self.gui.destroy()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_build_input_tooltip_formats_multiline(self):
        """多路径 tooltip 每行一个，单行输入框看不全时靠它看全"""
        self.assertEqual(
            self.gui._build_input_tooltip(r'D:\a;D:\b;D:\c'),
            'D:\\a\nD:\\b\nD:\\c',
        )

    def test_build_input_tooltip_single_path(self):
        """单路径不加换行"""
        self.assertEqual(self.gui._build_input_tooltip(r'D:\single'), r'D:\single')

    def test_build_input_tooltip_ignores_blank_segments(self):
        """尾随分号和空段不产生空行"""
        self.assertEqual(self.gui._build_input_tooltip(r'D:\a; ;D:\b;'), 'D:\\a\nD:\\b')

    def test_browse_replaces_with_all_selected_dirs(self):
        """连续选择直到取消，输入框以分号拼接全部选择（替换而非累加）"""
        self.gui.input_dir_var.set(r'D:\old')
        picks = list(self.dirs) + ['']
        with mock.patch.object(CialloHEVC.filedialog, 'askdirectory',
                               side_effect=picks) as dialog:
            self.gui.browse_input_dir()
        self.assertEqual(dialog.call_count, 4)
        self.assertEqual(
            self.gui.input_dir_var.get(),
            ';'.join(os.path.normpath(d) for d in self.dirs),
        )

    def test_browse_cancelled_immediately_keeps_previous_value(self):
        """第一次就取消，不能清空已有路径"""
        self.gui.input_dir_var.set(r'D:\keep')
        with mock.patch.object(CialloHEVC.filedialog, 'askdirectory', return_value=''):
            self.gui.browse_input_dir()
        self.assertEqual(self.gui.input_dir_var.get(), r'D:\keep')

    def test_browse_starts_from_first_existing_path(self):
        """多路径时对话框从第一个路径起步，而不是拿整串当目录"""
        self.gui.input_dir_var.set(';'.join(self.dirs))
        with mock.patch.object(CialloHEVC.filedialog, 'askdirectory',
                               return_value='') as dialog:
            self.gui.browse_input_dir()
        self.assertEqual(
            dialog.call_args.kwargs['initialdir'], os.path.normpath(self.dirs[0]))

    def test_browse_deduplicates_repeated_selection(self):
        """重复选中同一目录只保留一份，否则会被转码两次"""
        with mock.patch.object(CialloHEVC.filedialog, 'askdirectory',
                               side_effect=[self.dirs[0], self.dirs[0], '']):
            self.gui.browse_input_dir()
        self.assertEqual(self.gui.input_dir_var.get(), os.path.normpath(self.dirs[0]))

    def test_multi_path_value_keeps_default_border(self):
        """多路径整串不是目录，校验必须逐段判断，否则输入框永远标红"""
        self.gui.input_dir_var.set(';'.join(self.dirs))
        self.assertEqual(
            self.gui.input_entry.cget('border_color'), self.gui._dir_border_color)

    def test_multi_path_with_one_missing_turns_border_red(self):
        """任一段不存在就标红"""
        self.gui.input_dir_var.set(self.dirs[0] + ';' + os.path.join(self.temp_dir, 'gone'))
        self.assertEqual(
            self.gui.input_entry.cget('border_color'), ("#F44336", "#EF5350"))

    def test_startup_restores_multiple_paths_from_config(self):
        """启动时把 config.input_paths 列表还原成分号串"""
        self.gui.config.input_paths = list(self.dirs)
        self.gui.config.save()

        gui2 = ConverterGUI()
        gui2.withdraw()
        self.addCleanup(gui2.destroy)
        self.assertEqual(gui2.input_dir_var.get(), ';'.join(self.dirs))

    def test_sync_uses_first_path_when_multiple_selected(self):
        """🔗 开启时输出框不能显示整串分号路径"""
        self.gui.sync_dirs_var.set(True)
        self.gui.input_dir_var.set(';'.join(self.dirs))
        self.assertEqual(self.gui.output_dir_var.get(), os.path.normpath(self.dirs[0]))


class TestStartConversionWiring(unittest.TestCase):
    """start_conversion 传给 Converter.run 的参数必须和新签名对齐。"""

    def test_run_signature_takes_paths_and_recursive(self):
        import inspect
        params = list(inspect.signature(CialloHEVC.Converter.run).parameters)
        self.assertEqual(params[:4], ['self', 'input_paths', 'output_dir', 'recursive_subdirs'])

    def test_start_conversion_passes_list_and_recursive_flag(self):
        src = inspect.getsource(CialloHEVC.ConverterGUI.start_conversion)
        self.assertIn('input_paths = self.split_dir_paths(self.input_dir_var.get())', src)
        self.assertIn('self.config.input_paths = input_paths', src)
        self.assertIn('self.converter.run(input_paths, output_dir,', src)
        self.assertIn('self.recursive_enabled', src)
        self.assertNotIn('os.path.isdir(input_dir)', src)


class TestFileCollection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.dirs = [os.path.join(self.temp_dir, n) for n in ('one', 'two', 'empty')]
        for d in self.dirs:
            os.makedirs(d)
        Path(self.dirs[0], 'a.mp4').touch()
        Path(self.dirs[0], 'b.mkv').touch()
        Path(self.dirs[0], 'notes.txt').touch()
        Path(self.dirs[1], 'c.avi').touch()
        sub = Path(self.dirs[1], 'sub')
        sub.mkdir()
        Path(sub, 'd.mp4').touch()
        Path(sub, 'deeper').mkdir()
        Path(sub, 'deeper', 'e.mkv').touch()

        cfg = Config()
        cfg.exts = ['mp4', 'mkv', 'avi']
        self.converter = CialloHEVC.Converter(cfg, {'log': lambda *a: None})

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def names(self, paths):
        return sorted(Path(p).name for p in paths)

    def test_collect_files_non_recursive_skips_subdirs(self):
        files = self.converter._collect_files(self.dirs[:2], recursive=False)
        self.assertEqual(self.names(files), ['a.mp4', 'b.mkv', 'c.avi'])

    def test_collect_files_recursive_includes_all_depths(self):
        files = self.converter._collect_files(self.dirs[:2], recursive=True)
        self.assertEqual(self.names(files), ['a.mp4', 'b.mkv', 'c.avi', 'd.mp4', 'e.mkv'])

    def test_collect_files_filters_by_configured_extensions(self):
        """非视频扩展名不能进来"""
        files = self.converter._collect_files([self.dirs[0]], recursive=True)
        self.assertNotIn('notes.txt', self.names(files))

    def test_collect_files_empty_dir_returns_empty(self):
        self.assertEqual(self.converter._collect_files([self.dirs[2]], recursive=False), [])

    def test_collect_files_skips_nonexistent_path(self):
        """路径不存在只跳过，不抛异常"""
        missing = os.path.join(self.temp_dir, 'gone')
        files = self.converter._collect_files([self.dirs[0], missing], recursive=False)
        self.assertEqual(self.names(files), ['a.mp4', 'b.mkv'])

    def test_collect_files_deduplicates_repeated_dir(self):
        files = self.converter._collect_files([self.dirs[0], self.dirs[0]], recursive=False)
        self.assertEqual(self.names(files), ['a.mp4', 'b.mkv'])

    @unittest.skipUnless(os.name == 'nt', 'junction 是 Windows 特性')
    def test_collect_files_survives_directory_junction_loop(self):
        """子目录里有指回父目录的 junction 时，rglob 会重复命中同一文件。

        去重必须按 resolve() 后的真实路径判断，否则同一个文件会被转码几十次。
        """
        import subprocess
        target = self.dirs[0]
        link = os.path.join(target, 'loop')
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', link, target],
                              capture_output=True, text=True)
        if made.returncode != 0:
            self.skipTest('无法创建 junction')
        try:
            raw = list(Path(target).rglob('*.mp4'))
            files = self.converter._collect_files([target], recursive=True)
            self.assertGreater(len(raw), 1, 'junction 未被 rglob 跟随，这个用例失去意义')
            self.assertEqual(self.names(files), ['a.mp4', 'b.mkv'])
        finally:
            subprocess.run(['cmd', '/c', 'rmdir', link], capture_output=True)

    def test_collect_files_deduplicates_nested_parent_and_child(self):
        """递归选了父目录又选了子目录，文件不能被转码两次"""
        files = self.converter._collect_files(
            [self.dirs[1], os.path.join(self.dirs[1], 'sub')], recursive=True)
        self.assertEqual(self.names(files), ['c.avi', 'd.mp4', 'e.mkv'])


if __name__ == '__main__':
    unittest.main()
