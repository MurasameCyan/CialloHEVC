import unittest
import tempfile
import json
import os
from pathlib import Path
from unittest import mock

# 动态导入主模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
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
        """递归按钮位置在输入输出行之间、右侧"""
        info = self.gui.recursive_btn.place_info()
        self.assertEqual(int(info['x']), 705)  # 输出浏览按钮右边界 700 之后，避免遮挡
        self.assertEqual(int(info['y']), 40)   # 输入10，输出55，中间40

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


if __name__ == '__main__':
    unittest.main()
