"""系统原生多选文件夹对话框的测试。

真正弹出模态框需要人去点，所以这里覆盖的是「弹之前」和「弹之后」两端：
COM 管线（CLSID/IID、vtable 索引、选项标志、路径提取）用真实 COM 对象验证，
Show 之后的结果处理用打桩验证。Ctrl 多选本身是 Windows 对话框自带行为，
由 FOS_ALLOWMULTISELECT 标志决定，测试只保证该标志确实被设上。
"""
import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
import CialloHEVC
from CialloHEVC import Config, ConverterGUI

from test_multi_directory_input import patch_config_path


@unittest.skipUnless(sys.platform == 'win32', '原生对话框仅 Windows')
class TestNativeDialogPlumbing(unittest.TestCase):
    """直接打真实 COM 接口，不 Show —— vtable 索引写错这里就炸"""

    def test_dialog_gets_pickfolders_and_multiselect(self):
        """建出来的对话框必须同时带上「选文件夹」和「允许多选」两个标志"""
        dialog = CialloHEVC._create_folder_multiselect_dialog()
        try:
            # GetOptions 索引 10：读回真实生效的标志位
            opts = ctypes.c_ulong()
            ctypes.WINFUNCTYPE(
                ctypes.HRESULT, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ulong))(
                    CialloHEVC._com_vtable(dialog)[10])(dialog, ctypes.byref(opts))
        finally:
            CialloHEVC._com_release(dialog)

        self.assertTrue(opts.value & CialloHEVC._FOS_PICKFOLDERS,
                        '缺 FOS_PICKFOLDERS，对话框会变成选文件而不是选文件夹')
        self.assertTrue(opts.value & CialloHEVC._FOS_ALLOWMULTISELECT,
                        '缺 FOS_ALLOWMULTISELECT，Ctrl 多选不生效——就是这次要修的 bug')
        self.assertTrue(opts.value & CialloHEVC._FOS_FORCEFILESYSTEM,
                        '缺 FOS_FORCEFILESYSTEM，可能返回没有真实路径的虚拟节点')

    def test_initial_folder_accepted(self):
        """指定初始目录不应让创建流程失败（SHCreateItemFromParsingName + SetFolder）"""
        with tempfile.TemporaryDirectory() as d:
            dialog = CialloHEVC._create_folder_multiselect_dialog(d, '标题')
            self.assertTrue(dialog)
            CialloHEVC._com_release(dialog)

    def test_shell_item_path_roundtrip(self):
        """IShellItem -> 路径 的提取必须还原出原目录（覆盖 GetDisplayName 与内存释放）"""
        with tempfile.TemporaryDirectory() as d:
            iid = CialloHEVC.TaskbarProgress._guid(CialloHEVC._IID_IShellItem)
            item = ctypes.c_void_p()
            ctypes.oledll.shell32.SHCreateItemFromParsingName(
                ctypes.c_wchar_p(os.path.abspath(d)), None,
                ctypes.byref(iid), ctypes.byref(item))
            try:
                got = CialloHEVC._shell_item_path(item)
            finally:
                CialloHEVC._com_release(item)
        self.assertEqual(os.path.normpath(got), os.path.normpath(d))

    def test_shell_item_array_paths_on_real_array(self):
        """结果解析走真实 IShellItemArray：GetResults 之后的整条链路都验到。

        Show() 之后拿到的正是这种数组，但那需要人点确定；这里用
        SHCreateShellItemArrayFromShellItem 造一个等价的数组来替代。
        """
        IID_IShellItemArray = "{B63EA76D-1F85-456F-A19C-48159EFA858B}"
        with tempfile.TemporaryDirectory() as d:
            iid_item = CialloHEVC.TaskbarProgress._guid(CialloHEVC._IID_IShellItem)
            item = ctypes.c_void_p()
            ctypes.oledll.shell32.SHCreateItemFromParsingName(
                ctypes.c_wchar_p(os.path.abspath(d)), None,
                ctypes.byref(iid_item), ctypes.byref(item))
            try:
                iid_arr = CialloHEVC.TaskbarProgress._guid(IID_IShellItemArray)
                arr = ctypes.c_void_p()
                ctypes.oledll.shell32.SHCreateShellItemArrayFromShellItem(
                    item, ctypes.byref(iid_arr), ctypes.byref(arr))
                try:
                    got = CialloHEVC._shell_item_array_paths(arr)
                finally:
                    CialloHEVC._com_release(arr)
            finally:
                CialloHEVC._com_release(item)
        self.assertEqual(got, [os.path.normpath(d)])


class TestBrowseInputDirUsesNativePicker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        patch_config_path(self, os.path.join(self.temp_dir, 'config.json'))
        # 走原生路径的用例一律先禁掉 Tk 单选框：一旦实现退化成「压根没调原生框」，
        # 这里立刻断言失败，而不是弹出真模态框把无头测试挂死。上一次回归就是这么
        # 漏过去的——挂死看起来像「跑得慢」，不像失败。故意测回退循环的用例自己
        # 再 patch 一层盖掉它即可（内层 patch 退出时会还原成这个哨兵）。
        guard = mock.patch.object(
            CialloHEVC.filedialog, 'askdirectory',
            side_effect=AssertionError('不该弹 Tk 单选框：原生多选框才是主路径'))
        guard.start()
        self.addCleanup(guard.stop)
        self.gui = ConverterGUI()
        self.gui.withdraw()

    def tearDown(self):
        self.gui.destroy()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_multiple_dirs_joined_by_semicolon(self):
        """原生框一次返回多个目录时，全部写进输入框并用分号分隔"""
        picked = [os.path.join(self.temp_dir, n) for n in ('A', 'B', 'C')]
        with mock.patch.object(CialloHEVC, 'pick_directories_native',
                               return_value=picked) as m:
            self.gui.browse_input_dir()
        m.assert_called_once()
        self.assertEqual(self.gui.input_dir_var.get(), ';'.join(picked))

    def test_cancel_keeps_existing_value(self):
        """取消（返回空列表）不得清空原有内容"""
        self.gui.input_dir_var.set(r'C:\keep')
        with mock.patch.object(CialloHEVC, 'pick_directories_native', return_value=[]):
            self.gui.browse_input_dir()
        self.assertEqual(self.gui.input_dir_var.get(), r'C:\keep')

    def test_falls_back_to_single_pick_loop_when_com_fails(self):
        """原生框不可用时回退到反复弹单选框，仍能选出多个目录"""
        seq = [os.path.join(self.temp_dir, 'X'), os.path.join(self.temp_dir, 'Y'), '']
        with mock.patch.object(CialloHEVC, 'pick_directories_native',
                               side_effect=OSError('no COM')), \
             mock.patch.object(CialloHEVC.filedialog, 'askdirectory',
                               side_effect=seq) as ask:
            self.gui.browse_input_dir()
        self.assertEqual(ask.call_count, 3, '必须一直弹到用户取消为止')
        self.assertEqual(self.gui.input_dir_var.get(), ';'.join(seq[:2]))

    def test_fallback_loop_dedups(self):
        """回退循环里重复选同一个目录只记一次"""
        d = os.path.join(self.temp_dir, 'Z')
        with mock.patch.object(CialloHEVC.filedialog, 'askdirectory',
                               side_effect=[d, d, '']):
            got = self.gui._browse_input_dirs_loop(self.temp_dir)
        self.assertEqual(got, [os.path.normpath(d)])

    def test_existing_first_path_used_as_initial_dir(self):
        """已有多目录时，初始目录取第一个，而不是整串分号路径"""
        first = os.path.join(self.temp_dir, 'first')
        os.makedirs(first, exist_ok=True)
        self.gui.input_dir_var.set(f'{first};{self.temp_dir}')
        with mock.patch.object(CialloHEVC, 'pick_directories_native',
                               return_value=[]) as m:
            self.gui.browse_input_dir()
        self.assertEqual(os.path.normpath(m.call_args[0][0]), os.path.normpath(first))


if __name__ == '__main__':
    unittest.main()
