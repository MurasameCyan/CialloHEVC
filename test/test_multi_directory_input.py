import unittest
import tempfile
import json
import os
from pathlib import Path

# 动态导入主模块
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from CialloHEVC import Config

class TestConfigMigration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

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
        cfg_file = os.path.join(self.temp_dir, 'config.json')
        with open(cfg_file, 'w', encoding='utf-8') as f:
            json.dump({'input_dir': r'D:\test', 'output_dir': r'D:\out'}, f)

        cfg = Config()
        original_method = Config._config_path
        try:
            Config._config_path = staticmethod(lambda: cfg_file)
            cfg.load()
            self.assertEqual(cfg.input_paths, [r'D:\test'])
            self.assertFalse(hasattr(cfg, 'input_dir'))
        finally:
            Config._config_path = original_method

    def test_config_save_includes_new_fields(self):
        """保存配置应包含 input_paths 和 recursive_subdirs"""
        cfg_file = os.path.join(self.temp_dir, 'config.json')
        cfg = Config()
        cfg.input_paths = [r'D:\a', r'D:\b']
        cfg.recursive_subdirs = True

        original_method = Config._config_path
        try:
            Config._config_path = staticmethod(lambda: cfg_file)
            cfg.save()
        finally:
            Config._config_path = original_method

        with open(cfg_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.assertEqual(data['input_paths'], [r'D:\a', r'D:\b'])
        self.assertTrue(data['recursive_subdirs'])

if __name__ == '__main__':
    unittest.main()
