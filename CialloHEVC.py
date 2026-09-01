#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
H.265 视频转码器 - CustomTkinter版本
无标题栏 + 液态玻璃效果 + 完美主题支持
"""

import os
import sys
import subprocess
import time
import re
import threading
import shutil
import tempfile
import zipfile
from pathlib import Path
import customtkinter as ctk
from tkinter import filedialog
import json
import ctypes
import html as html_lib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# 控制台编码可能是 GBK 等非 UTF-8，日志里的 ✓ 会触发
# UnicodeEncodeError 并中断转码，这里统一降级为替换字符。
for _std_stream in (sys.stdout, sys.stderr):
    try:
        _std_stream.reconfigure(errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

# ==================== 版本号 ====================
# 每次修改当前应用源码时，手动将版本号末位加一。
APP_VERSION = "1.0.69"

# ↻ 递归开关放在输入行「浏览」按钮正上方的分组标题行留白里，不占用两行内部空间，
# 因此行距和按钮间距全部保持原样。数值以「工作目录」分组框左上角为原点：
# 负 y 表示落在分组框上方的标题行内，x 相对容器右缘对齐浏览按钮水平中心。
RECURSIVE_BTN_ABOVE_Y = -19
RECURSIVE_BTN_RIGHT_OFFSET = -50

GITHUB_FFMPEG_API_URL = "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest"
GITHUB_FFMPEG_RELEASE_URL = "https://github.com/GyanD/codexffmpeg/releases/latest"
DOWNLOAD_USER_AGENT = "CialloHEVC"


def build_system_proxy_opener():
    """Build one opener from the Windows/system proxy settings.

    ``urllib.request.getproxies`` reads the platform proxy configuration on
    Windows (and environment variables on other platforms).  Returning the
    mapping as well lets the caller explain which proxy was selected without
    creating a second, potentially different request path.
    """
    proxies = urllib.request.getproxies()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return opener, proxies


def _with_proxy_prefix(url, proxy_prefix):
    if not proxy_prefix:
        return url
    return proxy_prefix.rstrip('/') + '/' + url.lstrip('/')


def _request_text(opener, url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    with opener.open(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _full_build_asset_from_html(fragment):
    for match in re.finditer(r'href=["\']([^"\']+\.zip)["\']', fragment, re.IGNORECASE):
        candidate = html_lib.unescape(match.group(1))
        if urllib.parse.urlsplit(candidate).path.lower().endswith("full_build.zip"):
            return candidate
    return None


def _release_from_page(opener, page_url, proxy_prefix=""):
    """Resolve the latest full-build asset without using the GitHub API."""
    page = _request_text(opener, _with_proxy_prefix(page_url, proxy_prefix))

    tag_name = ""
    for tag_match in re.finditer(r"/releases/tag/([^\"?#<]+)", page, re.IGNORECASE):
        candidate = urllib.parse.unquote(html_lib.unescape(tag_match.group(1)))
        if candidate != "*name":
            tag_name = candidate
            break

    expanded_match = re.search(
        r'(?:src|href)=["\']([^"\']*/releases/expanded_assets/[^"\']+)["\']',
        page,
        re.IGNORECASE,
    )
    if expanded_match:
        expanded_url = urllib.parse.urljoin(page_url, html_lib.unescape(expanded_match.group(1)))
        if not tag_name:
            expanded_tag = re.search(r"/releases/expanded_assets/([^/?#\"'<]+)", expanded_url)
            if expanded_tag:
                tag_name = urllib.parse.unquote(expanded_tag.group(1))
    elif tag_name:
        expanded_url = urllib.parse.urljoin(
            page_url,
            f"../expanded_assets/{urllib.parse.quote(tag_name, safe='')}"
        )
    else:
        raise RuntimeError("无法从 GitHub 发布页面识别版本")

    fragment = _request_text(opener, _with_proxy_prefix(expanded_url, proxy_prefix))
    relative_asset_url = _full_build_asset_from_html(fragment)
    if not relative_asset_url:
        raise RuntimeError("未找到 full_build.zip 资源")

    return {
        "tag_name": tag_name,
        "download_url": urllib.parse.urljoin(expanded_url, relative_asset_url),
        "source": "release_page",
    }


def resolve_ffmpeg_release(opener, use_proxy=False, proxy_prefix=""):
    """Resolve the latest FFmpeg full-build URL, with API rate-limit fallback."""
    prefix = proxy_prefix if use_proxy else ""
    api_url = _with_proxy_prefix(GITHUB_FFMPEG_API_URL, prefix)
    request = urllib.request.Request(api_url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    try:
        with opener.open(request, timeout=30) as response:
            release_info = json.loads(response.read().decode("utf-8"))
        for asset in release_info.get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith("full_build.zip"):
                return {
                    "tag_name": release_info.get("tag_name", ""),
                    "download_url": asset.get("browser_download_url"),
                    "source": "api",
                }
        # A successful but incomplete API response can still be resolved from
        # the release page (some proxy caches omit the assets array).
        return _release_from_page(opener, GITHUB_FFMPEG_RELEASE_URL, prefix)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(4096).decode("utf-8", "replace")
        except Exception:
            pass
        rate_limited = exc.code == 403 and (
            "rate limit" in body.lower()
            or exc.headers.get("X-RateLimit-Remaining") == "0"
        )
        if not rate_limited:
            raise
        return _release_from_page(opener, GITHUB_FFMPEG_RELEASE_URL, prefix)


def download_with_opener(opener, url, target_path, progress_callback=None):
    """Stream a download through the supplied opener and report byte progress."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": DOWNLOAD_USER_AGENT,
            "Accept": "application/octet-stream",
        },
    )
    with opener.open(request, timeout=60) as response:
        try:
            total_size = int(response.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            total_size = 0
        downloaded = 0
        with open(target_path, "wb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                downloaded += len(block)
                if progress_callback:
                    progress_callback(downloaded, total_size)


def install_ffmpeg_zip(zip_path, core_dir):
    """先解压到同级临时目录，确认含 ffmpeg.exe 后才替换旧的 Core。

    这样下载或解压中途失败时，已经装好的 FFmpeg 不会被删掉。
    返回替换后的 ffmpeg.exe 路径；压缩包里没有 ffmpeg.exe 时返回 None 并保留旧 Core。
    """
    staging_dir = core_dir + '.new'
    shutil.rmtree(staging_dir, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(staging_dir)

        staged_exe = None
        for root, _dirs, files in os.walk(staging_dir):
            if 'ffmpeg.exe' in files:
                staged_exe = os.path.join(root, 'ffmpeg.exe')
                break
        if not staged_exe:
            return None

        if os.path.exists(core_dir):
            shutil.rmtree(core_dir)
        os.replace(staging_dir, core_dir)
        return os.path.join(core_dir, os.path.relpath(staged_exe, staging_dir))
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

# 设置唯一 App ID
myappid = 'yuzu.ciallo.gui.hevcconver'
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)


# ==================== 任务栏进度（Windows ITaskbarList3） ====================
class TaskbarProgress:
    """在 Windows 任务栏程序图标上叠加显示总进度。

    利用系统原生的 ITaskbarList3 COM 接口实现：
    - 背景层为程序图标本身，进度层为图标底部的绿色填充，二者天然区分、互不冲突；
    - 进度由系统平滑渲染，支持 0%~100% 连续更新；
    - 通过 set_progress(value 0.0~1.0) 实时绑定外部传入的总进度。
    """

    # 进度状态标志（TBPFLAG）
    TBPF_NOPROGRESS = 0
    TBPF_INDETERMINATE = 0x1
    TBPF_NORMAL = 0x2
    TBPF_ERROR = 0x4
    TBPF_PAUSED = 0x8

    def __init__(self):
        self._taskbar = None
        self._available = False
        if sys.platform != 'win32':
            return
        try:
            self._init_com()
            self._available = True
        except Exception:
            self._taskbar = None
            self._available = False

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    @classmethod
    def _guid(cls, s):
        g = cls._GUID()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
        return g

    def _init_com(self):
        ctypes.oledll.ole32.CoInitialize(None)
        clsid_taskbar = self._guid("{56FDF344-FD6D-11D0-958A-006097C9A090}")
        iid_taskbar3 = self._guid("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
        CLSCTX_INPROC_SERVER = 1
        ptr = ctypes.c_void_p()
        ctypes.oledll.ole32.CoCreateInstance(
            ctypes.byref(clsid_taskbar), None, CLSCTX_INPROC_SERVER,
            ctypes.byref(iid_taskbar3), ctypes.byref(ptr))
        self._taskbar = ptr
        # 取得对象 vtable 并解析所需方法
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0]
        funcs = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))
        # HrInit 位于 vtable 索引 3
        HrInit = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(funcs[3])
        HrInit(ptr)
        # SetProgressValue 索引 9：(this, hwnd, completed, total)
        self._set_value = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_ulonglong, ctypes.c_ulonglong)(funcs[9])
        # SetProgressState 索引 10：(this, hwnd, flag)
        self._set_state = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int)(funcs[10])
        self._release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(funcs[2])

    def set_state(self, hwnd, flag):
        if not self._available or not hwnd:
            return
        try:
            self._set_state(self._taskbar, ctypes.c_void_p(hwnd), flag)
        except Exception:
            pass

    def set_progress(self, hwnd, value):
        """绑定外部总进度，value 取值 0.0~1.0，自动更新动画状态。"""
        if not self._available or not hwnd:
            return
        try:
            v = max(0.0, min(1.0, float(value)))
            completed = int(v * 1000)
            self._set_state(self._taskbar, ctypes.c_void_p(hwnd), self.TBPF_NORMAL)
            self._set_value(self._taskbar, ctypes.c_void_p(hwnd), completed, 1000)
        except Exception:
            pass

    def clear(self, hwnd):
        """清除进度动画（恢复为纯图标）。"""
        self.set_state(hwnd, self.TBPF_NOPROGRESS)


# 设置customtkinter外观
ctk.set_appearance_mode("dark")  # 默认暗色主题
ctk.set_default_color_theme("blue")


# ==================== 编码器预设 ====================
# 参考 md/gpu.md 中记录的 GPU 参数配置标准（SSIM ≥ 0.95）
ENCODERS = {
    'CPU': {
        'codec': 'libx265',          # 软件编码
        'quality_label': 'CRF',      # 质量参数名称
        'quality': 18,               # 默认质量值
        'min_quality': 5,            # 自动循环最小质量值
        'quality_step': 2,           # 自动循环步长
        'presets': ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast',
                    'medium', 'slow', 'slower', 'veryslow'],
        'preset': 'slow',            # 默认预设
        'hwaccel': False,
    },
    'GPU/N': {
        'codec': 'hevc_nvenc',       # NVIDIA NVENC
        'quality_label': 'CQ',
        'quality': 22,
        'min_quality': 5,
        'quality_step': 2,
        'presets': ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7'],
        'preset': 'p4',
        'hwaccel': True,
    },
    'GPU/A': {
        'codec': 'hevc_amf',         # AMD AMF
        'quality_label': 'QP',
        'quality': 24,
        'min_quality': 5,
        'quality_step': 2,
        'presets': ['speed', 'balanced', 'quality'],
        'preset': 'quality',
        'hwaccel': True,
    },
    'GPU/I': {
        'codec': 'hevc_qsv',         # Intel QSV
        'quality_label': '质量值',
        'quality': 22,
        'min_quality': 5,
        'quality_step': 2,
        'presets': ['veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'],
        'preset': 'medium',
        'hwaccel': True,
    },
}


# ==================== 配置类 ====================
class Config:
    @staticmethod
    def _app_dir():
        # PyInstaller 打包后：exe 所在目录；否则：脚本所在目录
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def _config_path():
        return os.path.join(Config._app_dir(), 'config.json')

    @staticmethod
    def resolve_ffmpeg(ffmpeg_path=''):
        """静态方法：解析实际可用的 ffmpeg 路径（不依赖实例）。"""
        fp = (ffmpeg_path or '').strip()
        exe = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'
        if fp:
            if os.path.isabs(fp) and os.path.exists(fp):
                return fp
            if not os.path.isabs(fp):
                candidate = os.path.join(Config._app_dir(), fp)
                if os.path.exists(candidate):
                    return candidate
            suffix = '.exe' if sys.platform == 'win32' and not fp.lower().endswith('.exe') else ''
            return shutil.which(fp) or shutil.which(fp + suffix) or None
        return shutil.which(exe) or shutil.which('ffmpeg') or (
            os.path.join(Config._app_dir(), exe) if os.path.exists(os.path.join(Config._app_dir(), exe)) else None
        )

    def __init__(self):
        self.exts = ['mp4', 'mkv', 'avi', 'mov', 'ts', 'm2ts', 'wmv', 'flv']
        self.suffix = '_hevc'
        self.out_ext = '.mp4'
        self.crf = 18
        self.preset = 'slow'
        self.target_ssim = 0.95
        self.min_crf = 10
        self.crf_step = 2
        self.encoder = 'CPU'  # 编码器类型：CPU / GPU/N / GPU/A / GPU/I
        self.ffmpeg_path = 'ffmpeg.exe'
        self.theme = 'dark'  # 默认暗色主题
        self.input_paths = []  # 输入路径列表（目录）
        self.output_dir = ''  # 输出目录
        self.recursive_subdirs = False  # 是否递归扫描子目录
        self.proxy_url = 'https://gh-proxy.com'  # 反代地址
        self.gen_ssim_log = True   # 是否生成 SSIM 日志文件（运行目录/log）
        self.gen_summary_log = True  # 是否生成转换汇总日志文件（输出目录）
        self.out_format = 'Auto'  # 输出封装格式：Auto / mp4 / mkv
        self.skip_encode = '每次询问'  # 跳过编码策略：全部跳过 / 每次询问 / 全部编码
        self.sync_dirs = False  # 🔗 开关：输出目录是否始终跟随输入目录

    def load(self):
        cfg = self._config_path()
        if os.path.exists(cfg):
            try:
                with open(cfg, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 迁移旧 input_dir 为单元素列表
                    if 'input_dir' in data and data['input_dir']:
                        data['input_paths'] = [data['input_dir']]
                        del data['input_dir']
                    for k, v in data.items():
                        if hasattr(self, k):
                            setattr(self, k, v)
            except: pass

    def save(self):
        try:
            cfg = self._config_path()
            with open(cfg, 'w', encoding='utf-8') as f:
                json.dump({k: v for k, v in self.__dict__.items()}, f, indent=2, ensure_ascii=False)
        except: pass


# ==================== 转码引擎 ====================
class Converter:
    def __init__(self, config, callbacks):
        self.config = config
        self.log_cb = callbacks.get('log')
        self.progress_cb = callbacks.get('progress')
        self.file_progress_cb = callbacks.get('file_progress')
        self.current_file_progress_cb = callbacks.get('current_file_progress')
        self.stats_cb = callbacks.get('stats')  # 新增：实时统计回调
        self.ssim_status_cb = callbacks.get('ssim_status')  # SSIM计算状态回调
        self.is_running = False
        self.should_stop = False
        self.ssim_results = []
        self.color_changes = {}  # 文件名 -> 实际应用的色彩参数描述（仅用于任务明细展示）
        self.size_map = {}      # 文件名 -> (源文件字节数, 输出文件字节数)
        self.failed_files = []   # 失败文件列表：[(文件名, 视频编码)]
        self.skipped_files = []  # 跳过文件列表：[(文件名, 视频编码)]
        self.file_codecs = {}    # 文件名 -> 视频编码（大写）
        self.last_run_stats = None  # 最近一次任务统计 (output_dir, total, completed, failed, skipped)
        self.total_duration = 0  # 当前文件总时长（秒）
        self.current_time = 0  # 当前处理时间（秒）
        self.start_time = 0  # 总开始时间
        self.file_start_time = 0  # 当前文件开始时间
        self.current_bitrate = 0  # 当前码率（kbits/s）
        self.current_speed = 0  # 当前速度倍率
        self.current_process = None  # 当前运行的ffmpeg进程
        self.ssim_process = None     # 当前运行的SSIM计算ffmpeg进程
        self.color_args = []  # 当前文件的色彩输出参数
        self.current_file_index = 0  # 当前处理的文件索引（从0开始）
        self.total_files = 0  # 总文件数
        self.current_file_progress = 0  # 当前文件的编码进度(0~1)
    
    def log(self, msg, overwrite=False):
        if self.log_cb: self.log_cb(msg, overwrite)
        print(msg)
    
    def _resolve_ffmpeg(self):
        """解析实际可用的 ffmpeg 路径。委托给 Config.resolve_ffmpeg 统一实现。"""
        return Config.resolve_ffmpeg(self.config.ffmpeg_path)

    def check_ffmpeg(self):
        try:
            fp = self._resolve_ffmpeg()
            if not fp:
                return False
            r = subprocess.run([fp, '-version'],
                             capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            return r.returncode == 0
        except: return False
    
    def get_video_duration(self, input_file):
        """获取视频总时长（秒）"""
        try:
            cmd = [self.config.ffmpeg_path, '-i', input_file]
            result = subprocess.run(cmd, capture_output=True, 
                                  encoding='utf-8', errors='ignore',
                                  creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            output = result.stderr if result.stderr else result.stdout
            
            # 查找 Duration 信息
            # 格式: Duration: 00:02:33.04, start: 0.000000, bitrate: 5000 kb/s
            for line in output.split('\n'):
                if 'Duration:' in line:
                    match = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.\d+)', line)
                    if match:
                        hours = int(match.group(1))
                        minutes = int(match.group(2))
                        seconds = float(match.group(3))
                        total_seconds = hours * 3600 + minutes * 60 + seconds
                        return total_seconds
            return 0
        except:
            return 0
    
    def get_video_encoder(self, input_file):
        """获取视频编码格式"""
        try:
            fp = self._resolve_ffmpeg()
            if not fp: return None
            cmd = [fp, '-i', input_file]
            result = subprocess.run(cmd, capture_output=True, 
                                  encoding='utf-8', errors='ignore',
                                  creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            # ffmpeg 的信息在 stderr 中
            output = result.stderr if result.stderr else result.stdout
            
            # 查找视频流编码信息
            # 格式: Stream #0:0: Video: hevc (Main 10) ...
            for line in output.split('\n'):
                if 'Video:' in line and 'Stream' in line:
                    # 提取编码格式
                    if 'hevc' in line.lower() or 'h265' in line.lower():
                        return 'hevc'
                    elif 'h264' in line.lower() or 'avc' in line.lower():
                        return 'h264'
                    else:
                        # 尝试提取编码名称
                        match = re.search(r'Video:\s*(\w+)', line)
                        if match:
                            return match.group(1).lower()
            return None
        except:
            return None

    def get_audio_codec(self, input_file):
        """获取首个音频流的编码名称（小写），无音频或失败时返回 None。"""
        try:
            cmd = [self.get_ffprobe_path(), '-v', 'quiet', '-select_streams', 'a:0',
                   '-show_entries', 'stream=codec_name',
                   '-of', 'default=noprint_wrappers=1:nokey=1', input_file]
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8',
                                    errors='ignore',
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            name = (result.stdout or '').strip().split('\n')[0].strip()
            return name.lower() if name else None
        except:
            return None
    
    def get_ffprobe_path(self):
        """根据 ffmpeg 路径推导 ffprobe 路径"""
        fp = (self.config.ffmpeg_path or '').strip()
        exe = 'ffprobe.exe' if sys.platform == 'win32' else 'ffprobe'
        if not fp:
            # 优先查找与 ffmpeg 同目录的 ffprobe，否则 PATH，再否则 app_dir
            ffmpeg_fp = self._resolve_ffmpeg()
            if ffmpeg_fp:
                cand = os.path.join(os.path.dirname(ffmpeg_fp), exe)
                if os.path.exists(cand):
                    return cand
            return shutil.which(exe) or 'ffprobe'
        if fp.lower() in ('ffmpeg', 'ffmpeg.exe'):
            return shutil.which(exe) or 'ffprobe'
        d = os.path.dirname(os.path.abspath(fp)) if not os.path.isdir(fp) else fp
        cand = os.path.join(d, exe)
        if os.path.exists(cand):
            return cand
        return shutil.which(exe) or 'ffprobe'

    def probe_color_metadata(self, input_file):
        """使用 ffprobe 采集源视频色彩元数据，返回字典。"""
        info = {'color_range': None, 'color_space': None,
                'color_primaries': None, 'color_transfer': None}
        try:
            cmd = [self.get_ffprobe_path(), '-v', 'quiet', '-select_streams', 'v',
                   '-show_entries',
                   'stream=color_range,color_space,color_primaries,color_transfer',
                   '-of', 'default=noprint_wrappers=1', input_file]
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8',
                                    errors='ignore',
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            for line in (result.stdout or '').split('\n'):
                line = line.strip()
                if '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip()
                if k in info:
                    info[k] = None if v in ('', 'unknown', 'N/A') else v
        except:
            pass
        return info

    def detect_full_range(self, input_file):
        """通过 signalstats 检测亮度分布，判断是否为 Full Range。"""
        try:
            # movie 滤镜在 Windows 下需转义路径中的 \ 与 :
            esc = input_file.replace('\\', '/').replace(':', '\\:')
            vf = f"movie={esc},signalstats,metadata=mode=print:file=-"
            cmd = [self.config.ffmpeg_path, '-hide_banner', '-f', 'lavfi',
                   '-i', vf, '-frames:v', '100', '-f', 'null', '-']
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8',
                                    errors='ignore',
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            text = (result.stdout or '') + (result.stderr or '')
            ymins, ymaxs = [], []
            for m in re.finditer(r'signalstats\.YMIN=(\d+)', text):
                ymins.append(int(m.group(1)))
            for m in re.finditer(r'signalstats\.YMAX=(\d+)', text):
                ymaxs.append(int(m.group(1)))
            if ymins and ymaxs:
                # 最小值接近0、最大值接近255 判定为 Full Range
                return min(ymins) <= 10 and max(ymaxs) >= 245
        except:
            pass
        return False

    def detect_color_info(self, input_file):
        """检测源视频色彩参数并返回转码输出所需的 ffmpeg 色彩参数列表。
        规则：
          - HDR(bt2020 等)：保留源色彩参数，不套用 SDR 标准；
          - 参数缺失/unknown：采用通用 SDR(bt709) 推荐标准，并用 signalstats 校验 Full Range；
          - 其余：沿用源视频已声明的有效参数。
        """
        meta = self.probe_color_metadata(input_file)
        cs = (meta.get('color_space') or '').lower()
        cp = (meta.get('color_primaries') or '').lower()
        ct = (meta.get('color_transfer') or '').lower()

        self.log(f"色彩元数据: range={meta['color_range']}, space={meta['color_space']}, "
                 f"primaries={meta['color_primaries']}, transfer={meta['color_transfer']}")

        # HDR 判定：bt2020 色彩空间 / primaries，或 PQ/HLG 传递函数
        hdr_markers = ('bt2020', 'smpte2084', 'arib-std-b67')
        is_hdr = any(x in cs for x in hdr_markers) or any(x in cp for x in hdr_markers) \
            or any(x in ct for x in hdr_markers)
        if is_hdr:
            self.log("[色彩] 检测到 HDR 视频，保留源 bt2020 色彩参数")
            args = []
            if meta['color_range']:
                args += ['-color_range', meta['color_range']]
            if meta['color_space']:
                args += ['-colorspace', meta['color_space']]
            if meta['color_primaries']:
                args += ['-color_primaries', meta['color_primaries']]
            if meta['color_transfer']:
                args += ['-color_trc', meta['color_transfer']]
            return args

        missing = [k for k, v in meta.items() if not v]
        if missing:
            # 缺失参数：采用通用 SDR(bt709) 推荐标准
            is_full = self.detect_full_range(input_file)
            color_range = 'pc' if is_full else 'tv'
            self.log(f"[色彩] 缺失参数({', '.join(missing)})，套用 SDR/bt709 推荐标准"
                     f"，范围判定为 {'Full(pc)' if is_full else 'Limited(tv)'}")
            return ['-color_range', color_range,
                    '-colorspace', 'bt709',
                    '-color_primaries', 'bt709',
                    '-color_trc', 'bt709']

        # 参数完整且非 HDR：沿用源视频已声明参数
        self.log("[色彩] 源参数完整，沿用源视频色彩声明")
        return ['-color_range', meta['color_range'],
                '-colorspace', meta['color_space'],
                '-color_primaries', meta['color_primaries'],
                '-color_trc', meta['color_transfer']]
    
    def parse_ssim(self, log_file):
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                m = re.search(r'All:\s*(\d+\.\d+)', f.read())
                return float(m.group(1)) if m else None
        except: return None

    def _make_ssim_log_path(self, log_dir, name, timestamp):
        """返回 SSIM 日志文件路径。
        开关关闭时写入系统临时目录（用完即删），保证 log 目录不生成文件。
        返回 (path, is_temp)。
        """
        if self.config.gen_ssim_log:
            return os.path.join(log_dir, f"{timestamp}_{name}_ssim.log"), False
        fd, tmp = tempfile.mkstemp(suffix='_ssim.log')
        os.close(fd)
        return tmp, True

    @staticmethod
    def _format_size_pair(src_bytes, out_bytes):
        """把字节数格式化为可读字符串。>=1GB 用 GB，否则 MB。
        输出形如: ( 1.23 GB > 456.78 MB )
        """
        def fmt(n):
            if n is None or n <= 0:
                return '0 MB'
            gb = n / (1024 ** 3)
            if gb >= 1:
                return f"{gb:.2f} GB"
            mb = n / (1024 ** 2)
            return f"{mb:.2f} MB"
        return f"( {fmt(src_bytes)} > {fmt(out_bytes)} )"

    @staticmethod
    def _format_size_tb(n):
        """格式化字节数，超过1TB时只显示 'x TB'。"""
        if n is None or n <= 0:
            return '0 MB'
        tb = n / (1024 ** 4)
        if tb >= 1:
            return f"{tb:.2f} TB"
        gb = n / (1024 ** 3)
        if gb >= 1:
            return f"{gb:.2f} GB"
        mb = n / (1024 ** 2)
        return f"{mb:.2f} MB"

    @staticmethod
    def _load_history():
        """加载历史处理文件大小记录，返回 (total_src, total_out, ok)。
        ok=False 表示文件存在但读不出来（损坏或字段类型异常），
        调用方必须保留原文件，不能用 0 覆盖已累计的数据。"""
        history_path = os.path.join(Config._app_dir(), 'history.json')
        if not os.path.exists(history_path):
            return 0, 0, True
        try:
            with open(history_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            src = data.get('total_src', 0)
            out = data.get('total_out', 0)
        except:
            return 0, 0, False
        if not isinstance(src, (int, float)) or not isinstance(out, (int, float)):
            return 0, 0, False
        return src, out, True

    @staticmethod
    def _save_history(total_src, total_out):
        """保存历史处理文件大小记录。
        先写临时文件再 os.replace，避免写入过程中进程退出把记录截断。"""
        history_path = os.path.join(Config._app_dir(), 'history.json')
        tmp_path = history_path + '.tmp'
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump({'total_src': total_src, 'total_out': total_out}, f, indent=2)
            os.replace(tmp_path, history_path)
        except:
            try: os.remove(tmp_path)
            except: pass

    @staticmethod
    def _update_history(add_src, add_out):
        """累加历史处理文件大小并保存。返回新的 (total_src, total_out)。
        历史文件损坏时返回 None 并原样保留文件，避免累计值被清零。"""
        src, out, ok = Converter._load_history()
        if not ok:
            return None
        src += add_src
        out += add_out
        Converter._save_history(src, out)
        return src, out

    def _commit_file_history(self, fname):
        """单个文件完成后立即把它的大小累加进历史记录。
        逐个落盘而不是整轮结束再写，任务中途关窗/崩溃也不会丢掉已完成文件的统计。"""
        src_bytes, out_bytes = self.size_map.get(fname, (0, 0))
        if not (src_bytes or out_bytes):
            return
        if Converter._update_history(src_bytes, out_bytes) is None:
            self.log("[警告] history.json 无法解析，本次统计未累加（请检查或删除该文件）")

    @staticmethod
    def _format_history_tooltip():
        """读取历史记录，格式化为版本号 tooltip 文本。"""
        src, out, ok = Converter._load_history()
        if not ok:
            return "历史统计读取失败\nhistory.json 已损坏，请检查或删除该文件"
        saved = src - out if src > out else 0
        return (
            f"总处理前: {Converter._format_size_tb(src)}\n"
            f"总处理后: {Converter._format_size_tb(out)}\n"
            f"总节省量: {Converter._format_size_tb(saved)}"
        )

    @staticmethod
    def _format_color_diff(meta, args):
        """对比源色彩元数据与实际应用的 ffmpeg 输出参数，返回 '参数(源>新)' 形式描述。
        仅展示发生变动的参数。"""
        if not args:
            return ''
        src = {
            'range': (meta.get('color_range') or '').lower() or None,
            'space': (meta.get('color_space') or '').lower() or None,
            'primaries': (meta.get('color_primaries') or '').lower() or None,
            'trc': (meta.get('color_transfer') or '').lower() or None,
        }
        keys = {'-color_range': 'range', '-colorspace': 'space',
                '-color_primaries': 'primaries', '-color_trc': 'trc'}
        new = {}
        for i in range(0, len(args) - 1, 2):
            k = keys.get(args[i])
            if k:
                new[k] = args[i + 1]
        parts = []
        for k, nv in new.items():
            sv = src.get(k)
            # 只展示变动：源缺失(套用标准) 或 源与新不同
            if sv is None or sv != nv:
                parts.append(f"{k} ( {sv if sv else 'None'} > {nv} )")
        return ', '.join(parts)

    def _resolve_out_ext(self, input_file):
        """根据输出格式设置解析输出文件扩展名。
        - mp4 / mkv：固定使用该格式
        - Auto：源为 mp4/mkv 则沿用，其余一律使用 mp4
        返回带点的扩展名，如 '.mp4'。
        """
        fmt = getattr(self.config, 'out_format', 'Auto')
        if fmt == 'mp4':
            return '.mp4'
        if fmt == 'mkv':
            return '.mkv'
        # Auto：检测源封装格式
        src_ext = Path(input_file).suffix.lower()
        if src_ext == '.mkv':
            return '.mkv'
        return '.mp4'

    def build_encode_cmd(self, input_file, output_file, quality):
        """根据编码器类型构建 ffmpeg 编码命令。quality 为当前迭代的质量值。"""
        enc = ENCODERS.get(self.config.encoder, ENCODERS['CPU'])
        codec = enc['codec']
        preset = self.config.preset
        is_mkv = os.path.splitext(output_file)[1].lower() == '.mkv'

        cmd = [self.config.ffmpeg_path, '-y']
        if enc.get('hwaccel'):
            cmd += ['-hwaccel', 'auto']
        cmd += ['-i', input_file]
        # mkv：映射全部流（视频/音频/字幕/附件等）
        if is_mkv:
            cmd += ['-map', '0']
        cmd += ['-c:v', codec]

        if self.config.encoder == 'CPU':
            cmd += ['-crf', str(quality), '-preset', preset]
        elif self.config.encoder == 'GPU/N':
            cmd += ['-preset', preset, '-cq', str(quality)]
        elif self.config.encoder == 'GPU/A':
            cmd += ['-quality', preset, '-qp_i', str(quality), '-qp_p', str(quality)]
        elif self.config.encoder == 'GPU/I':
            cmd += ['-preset', preset, '-global_quality', str(quality)]
        else:
            cmd += ['-crf', str(quality), '-preset', preset]

        if is_mkv:
            # mkv：保留音频/字幕/附件流并映射章节
            cmd += ['-tag:v', 'hvc1', '-c:a', 'copy', '-c:s', 'copy', '-c:t', 'copy', '-map_chapters', '0']
        else:
            # mp4：视频参数保持不变；音频默认 copy，
            # 但 mp4 容器无法封装 wmav2/wmapro 等编码，需转 aac（保留源码率）后再封装
            audio_codec = self.get_audio_codec(input_file)
            mp4_incompatible_audio = {'wmav1', 'wmav2', 'wmapro', 'wmalossless', 'vorbis', 'pcm_s16le'}
            if audio_codec in mp4_incompatible_audio:
                cmd += ['-tag:v', 'hvc1', '-c:a', 'aac', '-b:a', '320k', '-movflags', '+faststart']
            else:
                cmd += ['-tag:v', 'hvc1', '-c:a', 'copy', '-movflags', '+faststart']
        # 注入色彩输出参数（来自源视频检测 / SDR 推荐标准）
        if self.color_args:
            cmd += list(self.color_args)
        cmd += [output_file]
        return cmd

    def encode(self, input_file, output_file, crf):
        cmd = self.build_encode_cmd(input_file, output_file, crf)
        try:
            # 获取视频总时长
            self.total_duration = self.get_video_duration(input_file)
            self.current_time = 0
            self.file_start_time = time.time()
            
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               encoding='utf-8', errors='ignore',
                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            
            # 保存进程引用，用于强制终止
            self.current_process = p
            
            for line in p.stdout:
                if self.should_stop: 
                    p.terminate()
                    try:
                        p.wait(timeout=3)  # 等待最多3秒
                    except subprocess.TimeoutExpired:
                        p.kill()  # 强制杀死进程
                        p.wait()
                    self.current_process = None
                    return False
                line = line.strip()
                
                # 提取当前处理时间并更新进度
                if line.startswith('frame='):
                    try:
                        # 提取各项信息
                        # frame= 471 fps= 29 q=24.0 size= 9216KiB time=00:00:20.73 bitrate=3752.4kbits/s speed=1.18x
                        
                        # 提取time字段 (格式: 00:00:20.73)
                        if 'time=' in line:
                            time_str = line.split('time=')[1].split()[0]
                            # 解析时间格式 HH:MM:SS.ms
                            time_match = re.match(r'(\d+):(\d+):(\d+\.\d+)', time_str)
                            if time_match:
                                hours = int(time_match.group(1))
                                minutes = int(time_match.group(2))
                                seconds = float(time_match.group(3))
                                self.current_time = hours * 3600 + minutes * 60 + seconds
                        
                        # 提取bitrate (kbits/s)
                        if 'bitrate=' in line:
                            bitrate_part = line.split('bitrate=')[1].split()[0]
                            # 移除单位，只保留数字
                            bitrate_value = float(bitrate_part.replace('kbits/s', '').replace('Mbits/s', ''))
                            # 如果是Mbits/s，转换为kbits/s
                            if 'Mbits/s' in bitrate_part:
                                self.current_bitrate = bitrate_value * 1024
                            else:
                                self.current_bitrate = bitrate_value
                        
                        # 提取speed倍率
                        if 'speed=' in line:
                            speed_str = line.split('speed=')[1].split()[0].replace('x', '')
                            self.current_speed = float(speed_str)
                        
                        # 实时更新统计信息（码率、速度、总用时）
                        if self.stats_cb:
                            elapsed_total = time.time() - self.start_time
                            self.stats_cb(self.current_bitrate, self.current_speed, elapsed_total)
                        
                        # 基于时长计算进度和预计剩余时间
                        if self.total_duration > 0 and self.current_file_progress_cb:
                            progress = self.current_time / self.total_duration
                            if progress > 1.0:
                                progress = 1.0
                            
                            # 计算预计剩余时间
                            elapsed = time.time() - self.file_start_time
                            if progress > 0.01:  # 至少1%进度再计算
                                estimated_total = elapsed / progress
                                remaining = estimated_total - elapsed
                                self.current_file_progress_cb(progress, remaining)
                            else:
                                self.current_file_progress_cb(progress, 0)
                    except:
                        pass
                
                # frame开头的行使用覆盖模式，保持单行输出
                overwrite = line.startswith('frame=')
                self.log(line, overwrite=overwrite)
            
            p.wait()
            self.current_process = None
            # 编码成功时强制进度归满，避免末帧时间略小于总时长导致停在99%
            if p.returncode == 0 and self.current_file_progress_cb:
                self.current_file_progress_cb(1.0, 0)
            return p.returncode == 0
        except Exception as e:
            self.log(f"编码错误: {e}")
            if hasattr(self, 'current_process') and self.current_process:
                try:
                    self.current_process.kill()
                    self.current_process.wait()
                except:
                    pass
                self.current_process = None
            return False
    
    def calc_ssim(self, input_file, output_file, ssim_log):
        fp = self._resolve_ffmpeg()
        if not fp: return False
        # 显式绑定两个输入的首个视频流：源文件带封面图(attached_pic)时，
        # 未标注的 -lavfi ssim 会把封面当成第二路输入，导致
        # "Width and height of input videos must be same." 而计算失败
        cmd = [fp, '-i', input_file, '-i', output_file,
               '-filter_complex', '[0:v:0][1:v:0]ssim', '-an', '-f', 'null', '-']
        proc = None
        log_f = None
        try:
            if self.ssim_status_cb: self.ssim_status_cb(True)  # 开始SSIM计算
            # ffmpeg 把 ssim 统计写到 stderr；把 stderr 写入 ssim_log 供 parse_ssim 解析
            log_f = open(ssim_log, 'w', encoding='utf-8', errors='ignore')
            self.ssim_process = proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=log_f,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )
            # 轮询直到结束，或被请求停止
            while True:
                if self.should_stop:
                    try: proc.terminate()
                    except: pass
                    try: proc.wait(timeout=2)
                    except:
                        try: proc.kill()
                        except: pass
                    if self.ssim_status_cb: self.ssim_status_cb(False)
                    self.ssim_process = None
                    return False
                ret = proc.poll()
                if ret is not None:
                    break
                # SSIM 计算期间每秒推送一次总用时，保持 UI 计时跳动
                if self.stats_cb:
                    self.stats_cb(0, 0, time.time() - self.start_time)
                time.sleep(1)
            # 确保日志内容落盘
            try: log_f.flush()
            except: pass
            if self.ssim_status_cb: self.ssim_status_cb(False)  # 结束SSIM计算
            self.ssim_process = None
            return proc.returncode == 0
        except:
            if proc is not None:
                try: proc.terminate()
                except: pass
            if self.ssim_status_cb: self.ssim_status_cb(False)  # 异常时也停止动画
            self.ssim_process = None
            return False
        finally:
            if log_f is not None:
                try: log_f.close()
                except: pass
    
    def cleanup_partial_output(self, output_file, retries=5):
        """删除因停止任务而残留的未完成输出文件。

        ffmpeg 进程刚退出时 Windows 可能仍短暂持有文件句柄，故重试若干次。
        """
        if not output_file or not os.path.exists(output_file):
            return False
        for attempt in range(retries):
            try:
                os.remove(output_file)
                self.log(f"[清理] 已删除未完成文件: {os.path.basename(output_file)}")
                return True
            except OSError:
                if attempt < retries - 1:
                    time.sleep(0.2)
        self.log(f"[警告] 无法删除未完成文件: {output_file}")
        return False

    def _determine_output_dir(self, input_file, unified_output_dir):
        """决定单个文件的输出目录。

        🔗 开启时输出到源文件所在目录：多目录转码时全挤进一个输出目录会混淆来源。
        """
        if self.config.sync_dirs:
            return str(Path(input_file).parent)
        return unified_output_dir

    def process_file(self, input_file, output_dir, log_dir):
        if self.should_stop: return False
        
        name = Path(input_file).stem
        filename = Path(input_file).name
        
        # 检查文件名是否已包含(HEVC)标记
        if '(HEVC)' in name:
            self.log(f"[跳过] {input_file}")
            codec = self.get_video_encoder(input_file)
            if codec:
                self.file_codecs[filename] = codec.upper()
            return 'skip'
        
        # 检测视频编码
        self.log(f"\n处理: {input_file}")
        self.log("检测视频编码...")
        codec = self.get_video_encoder(input_file)
        
        if codec:
            self.log(f"视频编码: {codec.upper()}")
            self.file_codecs[filename] = codec.upper()
            if codec == 'hevc':
                self.log("[提示] 该视频已是HEVC编码")
                # 返回特殊状态，让调用者决定是否跳过
                return 'hevc'
        else:
            self.log("[警告] 无法检测视频编码，继续处理")
        
        # 输出文件保存到output_dir，格式：文件名(HEVC).后缀（按输出格式设置解析）
        out_ext = self._resolve_out_ext(input_file)
        output_dir = self._determine_output_dir(input_file, output_dir)
        os.makedirs(output_dir, exist_ok=True)
        output = os.path.join(output_dir, f"{name}(HEVC){out_ext}")
        if os.path.exists(output):
            self.log(f"[跳过] {output} 已存在")
            return 'skip'
        
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        ssim_log, _ssim_is_temp = self._make_ssim_log_path(log_dir, name, timestamp)

        output_committed = False
        try:
            # 更新当前文件名显示
            if self.file_progress_cb: 
                self.file_progress_cb(filename)
            
            # 转码前检测源视频色彩参数，避免色差问题
            self.log("检测色彩参数...")
            _src_meta = self.probe_color_metadata(input_file)
            self.color_args = self.detect_color_info(input_file)

            # 保险：开始转码前再次同步当前编码器的 min_crf / crf_step
            enc = ENCODERS.get(self.config.encoder, ENCODERS['CPU'])
            self.config.min_crf = enc['min_quality']
            self.config.crf_step = enc['quality_step']

            crf = self.config.crf
            final_ssim = None
            
            while crf >= self.config.min_crf:
                if self.should_stop: return False

                quality_label = ENCODERS.get(self.config.encoder, ENCODERS['CPU']).get('quality_label', 'CRF')
                self.log(f"[{quality_label}={crf}] 转码中...")
                if not self.encode(input_file, output, crf):
                    self.log("[错误] 转码失败")
                    return False

                self.log(f"[{quality_label}={crf}] 计算 SSIM ...")
                if not self.calc_ssim(input_file, output, ssim_log):
                    self.log("[错误] SSIM 计算失败")
                    return False

                final_ssim = self.parse_ssim(ssim_log)
                if not final_ssim:
                    self.log("[错误] 无法解析 SSIM")
                    return False

                self.log(f"      SSIM = {final_ssim:.6f}")

                if final_ssim >= self.config.target_ssim:
                    self.log("✓ SSIM 达标")
                    break

                self.log(f"SSIM < {self.config.target_ssim}，降低{quality_label}重试...")
                crf -= self.config.crf_step
                if crf < self.config.min_crf:
                    self.log(f"已达最小{quality_label}，保留当前输出")
                    break
            
            if final_ssim:
                # 记录源/输出文件大小（仅在文件存在时记录）
                try:
                    src_size = os.path.getsize(input_file) if os.path.exists(input_file) else 0
                except: src_size = 0
                try:
                    out_size = os.path.getsize(output) if os.path.exists(output) else 0
                except: out_size = 0
                self.size_map[Path(input_file).name] = (src_size, out_size)
                self.ssim_results.append((Path(input_file).name, final_ssim))
                self.color_changes[Path(input_file).name] = self._format_color_diff(_src_meta, self.color_args)
            output_committed = True
            return True  # 成功
        finally:
            # 中途停止：删除本次未完成的输出文件，避免残留半成品并阻塞下次转码
            if self.should_stop and not output_committed:
                self.cleanup_partial_output(output)
            # SSIM 日志开关关闭时，删除临时日志文件，保证 log 目录不生成文件
            if _ssim_is_temp:
                try: os.remove(ssim_log)
                except: pass
    
    def _collect_files(self, input_paths, recursive):
        """从多个输入目录收集视频文件，按 config.exts 过滤并去重。

        去重用 resolve() 后的绝对路径：父目录和子目录同时被选中时，
        递归扫描会把同一个文件找到两次。
        """
        files = []
        seen = set()
        for path_str in input_paths:
            path = Path(path_str)
            if not path.is_dir():
                continue
            for ext in self.config.exts:
                found = path.rglob(f"*.{ext}") if recursive else path.glob(f"*.{ext}")
                for f in found:
                    key = f.resolve()
                    if key not in seen:
                        seen.add(key)
                        files.append(f)
        return files

    def run(self, input_paths, output_dir, recursive_subdirs=False, skip_hevc_callback=None):
        self.is_running = True
        self.should_stop = False
        self.ssim_results = []
        self.color_changes = {}
        self.size_map = {}
        self.failed_files = []   # 失败文件列表：[(文件名, 视频编码)]
        self.skipped_files = []  # 跳过文件列表：[(文件名, 视频编码)]
        self.file_codecs = {}    # 文件名 -> 视频编码（大写）
        self.start_time = time.time()  # 记录总开始时间
        
        # 日志目录保存到运行路径\log
        script_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(script_dir, 'log')
        os.makedirs(log_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        
        if not self.check_ffmpeg():
            self.log("[错误] ffmpeg不可用")
            self.is_running = False
            return
        
        # 从输入目录列表查找文件，递归开关决定扫描深度
        files = self._collect_files(input_paths, recursive_subdirs)

        total = len(files)
        if total == 0:
            self.log("未找到视频文件")
            self.is_running = False
            return
        
        self.log(f"找到 {total} 个文件\n")
        
        # 设置总文件数，用于平滑进度计算
        self.total_files = total
        
        # 初始化进度显示
        if self.progress_cb:
            self.progress_cb(0, total, 0, 0, 0, 0, 0)
        
        completed = 0
        failed = 0
        skipped = 0
        for i, f in enumerate(files, 1):
            if self.should_stop:
                self.log("\n用户取消")
                break
            
            self.current_file_index = i - 1  # 当前文件索引（从0开始）
            self.current_file_progress = 0  # 重置当前文件进度
            self.log(f"\n[{i}/{total}]")
            fname = f.name
            result = self.process_file(str(f), output_dir, log_dir)
            
            if result == 'hevc':
                # 检测到HEVC编码，询问用户
                if skip_hevc_callback:
                    user_choice = skip_hevc_callback(str(f))
                    if user_choice == 'skip':
                        self.log("[跳过] 用户选择跳过HEVC视频")
                        skipped += 1
                        self.skipped_files.append((fname, self.file_codecs.get(fname, '')))
                    elif user_choice == 'skip_all':
                        self.log("[跳过] 用户选择跳过所有HEVC视频")
                        skipped += 1
                        self.skipped_files.append((fname, self.file_codecs.get(fname, '')))
                    else:
                        # 继续处理
                        result = self.process_file_force(str(f), output_dir, log_dir)
                        if result:
                            completed += 1
                            self._commit_file_history(fname)
                        else:
                            failed += 1
                            self.failed_files.append((fname, self.file_codecs.get(fname, '')))
                else:
                    # 没有回调，默认跳过
                    self.log("[跳过] 检测到HEVC编码")
                    skipped += 1
                    self.skipped_files.append((fname, self.file_codecs.get(fname, '')))
            elif result == 'skip':
                skipped += 1
                self.skipped_files.append((fname, self.file_codecs.get(fname, '')))
            elif result == True:
                completed += 1
                self._commit_file_history(fname)
            else:
                failed += 1
                self.failed_files.append((fname, self.file_codecs.get(fname, '')))
            
            # 更新进度（传递成功、失败和跳过计数、总用时、码率和速度）
            if self.progress_cb:
                elapsed = time.time() - self.start_time
                self.progress_cb(completed, total, failed, elapsed, self.current_bitrate, self.current_speed, skipped)
        
        self.log("\n" + "="*60)
        self.log("Ciallo～(∠・ω< )⌒★\n")
        if self.ssim_results:
            enc = ENCODERS.get(self.config.encoder, ENCODERS['CPU'])
            ql = enc.get('quality_label', 'CRF')
            self.log(f"任务明细:  ( 质量预设: {self.config.preset}  |  SSIM: {self.config.target_ssim}  |  {ql}: {self.config.crf}  )")
            for filename, ssim in self.ssim_results:
                src_bytes, out_bytes = self.size_map.get(filename, (0, 0))
                cc = self.color_changes.get(filename, '')
                line = f"{filename}  |  SSIM: {ssim:.6f}  |  文件大小: {self._format_size_pair(src_bytes, out_bytes)}"
                if cc:
                    line += f"  |  色彩参数: {cc}"
                self.log(line)
        if self.failed_files:
            self.log("\n失败文件:")
            for fname, codec in self.failed_files:
                self.log(f"{fname}  |  视频编码: {codec}" if codec else f"{fname}")
        if self.skipped_files:
            self.log("\n跳过文件:")
            for fname, codec in self.skipped_files:
                self.log(f"{fname}  |  视频编码: {codec}" if codec else f"{fname}")
        if self.ssim_results:
            avg = sum(s for _, s in self.ssim_results) / len(self.ssim_results)
            total_src = sum(s for s, _ in self.size_map.values()) if self.size_map else 0
            total_out = sum(o for _, o in self.size_map.values()) if self.size_map else 0
            self.log(f"\n平均 SSIM: {avg:.6f}  |  总成功文件: {completed}  |  总文件大小: {self._format_size_pair(total_src, total_out)}")
        self.log("="*60)

        # 保存本次任务统计，供自动关机等收尾流程强制生成汇总日志使用
        self.last_run_stats = (output_dir, total, completed, failed, skipped)

        # 将完整转换完成内容输出到输出目录（受汇总日志开关控制）
        if self.config.gen_summary_log:
            self._save_summary_to_output(output_dir, total, completed, failed, skipped)
        
        self.is_running = False
    
    def _save_summary_to_output(self, output_dir, total, completed, failed, skipped):
        """将转换完成汇总日志写入输出目录，保留原日志排版"""
        try:
            elapsed = time.time() - self.start_time
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            
            lines = []
            lines.append("=" * 60)
            lines.append("Ciallo～(∠・ω< )⌒★\n")
            lines.append(f"总计: {total}  成功: {completed}  失败: {failed}  跳过: {skipped}")
            lines.append(f"总用时: {hours:02d}:{minutes:02d}:{seconds:02d}")
            if self.ssim_results:
                enc = ENCODERS.get(self.config.encoder, ENCODERS['CPU'])
                ql = enc.get('quality_label', 'CRF')
                lines.append(f"\n任务明细:  ( 质量预设: {self.config.preset}  |  SSIM: {self.config.target_ssim}  |  {ql}: {self.config.crf}  )")
                for filename, ssim in self.ssim_results:
                    src_bytes, out_bytes = self.size_map.get(filename, (0, 0))
                    cc = self.color_changes.get(filename, '')
                    line = f"{filename}  |  SSIM: {ssim:.6f}  |  文件大小: {self._format_size_pair(src_bytes, out_bytes)}"
                    if cc:
                        line += f"  |  色彩参数: {cc}"
                    lines.append(line)
            if self.failed_files:
                lines.append("\n失败文件:")
                for fname, codec in self.failed_files:
                    lines.append(f"{fname}  |  视频编码: {codec}" if codec else f"{fname}")
            if self.skipped_files:
                lines.append("\n跳过文件:")
                for fname, codec in self.skipped_files:
                    lines.append(f"{fname}  |  视频编码: {codec}" if codec else f"{fname}")
            if self.ssim_results:
                avg = sum(s for _, s in self.ssim_results) / len(self.ssim_results)
                total_src = sum(s for s, _ in self.size_map.values()) if self.size_map else 0
                total_out = sum(o for _, o in self.size_map.values()) if self.size_map else 0
                lines.append(f"\n平均 SSIM: {avg:.6f}  |  总成功文件: {completed}  |  总文件大小: {self._format_size_pair(total_src, total_out)}")
            lines.append("=" * 60)
            
            timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            summary_file = os.path.join(output_dir, f"{timestamp}_转换汇总.log")
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            self.log(f"\n汇总日志已保存: {summary_file}")
        except Exception as e:
            self.log(f"[警告] 保存汇总日志失败: {e}")
    
    def process_file_force(self, input_file, output_dir, log_dir):
        """强制处理文件（不检查编码）"""
        if self.should_stop: return False
        
        name = Path(input_file).stem
        filename = Path(input_file).name
        
        # 输出文件保存到output_dir，格式：文件名(HEVC).后缀（按输出格式设置解析）
        out_ext = self._resolve_out_ext(input_file)
        output_dir = self._determine_output_dir(input_file, output_dir)
        os.makedirs(output_dir, exist_ok=True)
        output = os.path.join(output_dir, f"{name}(HEVC){out_ext}")

        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        ssim_log, _ssim_is_temp = self._make_ssim_log_path(log_dir, name, timestamp)

        output_committed = False
        try:
            # 更新当前文件名显示
            if self.file_progress_cb: 
                self.file_progress_cb(filename)
            
            # 转码前检测源视频色彩参数，避免色差问题
            self.log("检测色彩参数...")
            _src_meta = self.probe_color_metadata(input_file)
            self.color_args = self.detect_color_info(input_file)

            # 保险：开始转码前再次同步当前编码器的 min_crf / crf_step
            enc = ENCODERS.get(self.config.encoder, ENCODERS['CPU'])
            self.config.min_crf = enc['min_quality']
            self.config.crf_step = enc['quality_step']

            crf = self.config.crf
            final_ssim = None
            
            while crf >= self.config.min_crf:
                if self.should_stop: return False

                quality_label = ENCODERS.get(self.config.encoder, ENCODERS['CPU']).get('quality_label', 'CRF')
                self.log(f"[{quality_label}={crf}] 转码中...")
                if not self.encode(input_file, output, crf):
                    self.log("[错误] 转码失败")
                    return False

                self.log(f"[{quality_label}={crf}] 计算SSIM...")
                if not self.calc_ssim(input_file, output, ssim_log):
                    self.log("[错误] SSIM计算失败")
                    return False

                final_ssim = self.parse_ssim(ssim_log)
                if not final_ssim:
                    self.log("[错误] 无法解析SSIM")
                    return False

                self.log(f"      SSIM = {final_ssim:.6f}")

                if final_ssim >= self.config.target_ssim:
                    self.log("✓ SSIM达标")
                    break

                self.log(f"SSIM < {self.config.target_ssim}，降低{quality_label}重试...")
                crf -= self.config.crf_step
                if crf < self.config.min_crf:
                    self.log(f"已达最小{quality_label}，保留当前输出")
                    break
            
            if final_ssim:
                try:
                    src_size = os.path.getsize(input_file) if os.path.exists(input_file) else 0
                except: src_size = 0
                try:
                    out_size = os.path.getsize(output) if os.path.exists(output) else 0
                except: out_size = 0
                self.size_map[Path(input_file).name] = (src_size, out_size)
                self.ssim_results.append((Path(input_file).name, final_ssim))
                self.color_changes[Path(input_file).name] = self._format_color_diff(_src_meta, self.color_args)
            output_committed = True
            return True
        finally:
            # 中途停止：删除本次未完成的输出文件，避免残留半成品并阻塞下次转码
            if self.should_stop and not output_committed:
                self.cleanup_partial_output(output)
            # SSIM 日志开关关闭时，删除临时日志文件，保证 log 目录不生成文件
            if _ssim_is_temp:
                try: os.remove(ssim_log)
                except: pass


# ==================== CustomTkinter GUI ====================
class ConverterGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("Ciallo～(∠・ω< )⌒★")
        self.geometry("700x580")  # 初始尺寸（高度会自动调整）
        self.minsize(750, 400)
        
        # 设置窗口图标（支持开发环境和PyInstaller打包后的环境）
        try:
            # 获取图标文件路径（兼容PyInstaller打包）
            if getattr(sys, 'frozen', False):
                # PyInstaller打包后，使用_MEIPASS临时目录
                icon_path = os.path.join(sys._MEIPASS, 'icon.ico')
            else:
                # 开发环境，使用当前目录
                icon_path = 'icon.ico'
            
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except:
            pass
        
        # 移除标题栏
        self.overrideredirect(True)
        
        self.config = Config()
        self.config.load()
        self.converter = None
        self.worker = None

        # 任务栏进度（Windows 原生图标进度动画）
        self.taskbar = TaskbarProgress()
        self._taskbar_hwnd = None
        
        # 拖动变量
        self._drag_data = {"x": 0, "y": 0}
        
        # 设置主题
        ctk.set_appearance_mode(self.config.theme)
        
        # 应用液态玻璃效果
        self.after(100, self.apply_glass_effect)
        
        self.build_ui()
        
        # 构建完成后自适应高度
        self.after(100, self.auto_resize_window)
        
        # 居中显示
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()  # 使用自适应后的高度
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2 - 50  # 稍微向上偏移
        self.geometry(f"{w}x{h}+{x}+{y}")
    
    def show_dialog(self, title, message, dialog_type="info", buttons=None):
        """
        显示自定义对话框
        dialog_type: "info", "error", "warning", "yesno"
        buttons: 自定义按钮列表 [(text, value, color), ...]
        返回: 按钮的value值，取消返回None
        """
        dialog = ctk.CTkToplevel(self)
        dialog.title("")  # 不显示标题文字
        dialog.geometry("400x220")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        
        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (400 // 2)
        y = (dialog.winfo_screenheight() // 2) - (220 // 2)
        dialog.geometry(f"+{x}+{y}")
        
        result_value = {"value": None}
        
        # 图标映射
        icons = {
            "info": "ℹ️",
            "error": "❌",
            "warning": "⚠️",
            "yesno": "❓"
        }
        
        # 图标和标题区域
        header_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(20, 10))
        
        ctk.CTkLabel(header_frame, text=icons.get(dialog_type, "ℹ️"), 
                    font=("Segoe UI", 36)).pack(side="left", padx=(0, 15))
        
        title_label = ctk.CTkLabel(header_frame, text=title, 
                                  font=("Segoe UI", 15, "bold"),
                                  anchor="w")
        title_label.pack(side="left", fill="x", expand=True)
        
        # 消息内容
        msg_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        msg_frame.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        
        ctk.CTkLabel(msg_frame, text=message, 
                    font=("Segoe UI", 12),
                    wraplength=350,
                    anchor="w",
                    justify="left").pack(fill="both", expand=True)
        
        # 按钮区域
        button_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        button_frame.pack(fill="x", padx=20, pady=(0, 20))
        
        def make_callback(value):
            def callback():
                result_value["value"] = value
                dialog.destroy()
            return callback
        
        # 默认按钮配置
        if buttons is None:
            if dialog_type == "yesno":
                buttons = [
                    ("是", True, ("#4CAF50", "#388E3C")),
                    ("否", False, ("#9E9E9E", "#616161"))
                ]
            else:
                buttons = [("确定", True, ("#2196F3", "#1976D2"))]
        
        # 创建按钮
        for text, value, color in buttons:
            btn = ctk.CTkButton(button_frame, text=text, 
                               command=make_callback(value),
                               width=100, height=35,
                               fg_color=color,
                               font=("Segoe UI", 12, "bold"))
            btn.pack(side="left", expand=True, padx=5)
        
        # ESC键关闭
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        
        # 等待对话框关闭
        dialog.wait_window()
        
        return result_value["value"]
    
    def apply_glass_effect(self):
        """应用Windows 11液态玻璃效果和圆角窗口"""
        if sys.platform == 'win32':
            try:
                self.update()
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                
                # 强制在任务栏显示图标
                # 设置窗口样式，确保显示在任务栏
                GWL_EXSTYLE = -20
                WS_EX_APPWINDOW = 0x00040000  # 强制显示在任务栏
                WS_EX_LAYERED = 0x00080000
                LWA_ALPHA = 0x00000002
                
                ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_LAYERED | WS_EX_APPWINDOW)
                ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 250, LWA_ALPHA)

                # 添加 WS_MINIMIZEBOX 样式，使点击任务栏图标可最小化/还原
                # overrideredirect 会移除该样式，需手动补回
                GWL_STYLE = -16
                WS_MINIMIZEBOX = 0x00020000
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_MINIMIZEBOX)
                
                # 强制刷新窗口，让任务栏图标立即显示
                # 先隐藏再显示窗口，触发任务栏更新
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                
                # 应用圆角窗口 (Windows 11)
                try:
                    DWMWA_WINDOW_CORNER_PREFERENCE = 33
                    DWMWCP_ROUND = 2  # 圆角
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd,
                        DWMWA_WINDOW_CORNER_PREFERENCE,
                        ctypes.byref(ctypes.c_int(DWMWCP_ROUND)),
                        ctypes.sizeof(ctypes.c_int)
                    )
                except:
                    pass
                
                # 应用Mica/Acrylic效果 (Windows 11)
                try:
                    DWMWA_SYSTEMBACKDROP_TYPE = 38
                    DWMSBT_TRANSIENTWINDOW = 3  # Acrylic
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd,
                        DWMWA_SYSTEMBACKDROP_TYPE,
                        ctypes.byref(ctypes.c_int(DWMSBT_TRANSIENTWINDOW)),
                        ctypes.sizeof(ctypes.c_int)
                    )
                except:
                    pass
            except:
                pass
    
    def _get_taskbar_hwnd(self):
        """获取用于任务栏进度的顶层窗口句柄（带缓存）。"""
        if self._taskbar_hwnd:
            return self._taskbar_hwnd
        if sys.platform != 'win32':
            return None
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            self._taskbar_hwnd = hwnd
            return hwnd
        except Exception:
            return None

    def set_taskbar_progress(self, value):
        """绑定总进度到任务栏图标，value 取值 0.0~1.0。"""
        self.taskbar.set_progress(self._get_taskbar_hwnd(), value)

    def clear_taskbar_progress(self):
        """清除任务栏图标进度动画。"""
        self.taskbar.clear(self._get_taskbar_hwnd())

    def set_taskbar_error(self):
        """以错误状态（红色）显示任务栏进度。"""
        self.taskbar.set_state(self._get_taskbar_hwnd(), TaskbarProgress.TBPF_ERROR)

    def build_ui(self):
        # 自定义标题栏（用于拖动 + 右上角控制按钮）
        titlebar = ctk.CTkFrame(self, height=40, corner_radius=0)
        titlebar.pack(fill="x", side="top")
        titlebar.pack_propagate(False)
        
        # 绑定拖动事件
        titlebar.bind('<Button-1>', self.start_drag)
        titlebar.bind('<B1-Motion>', self.drag_window)
        
        # 左侧标题
        title_label = ctk.CTkLabel(titlebar, text="  Ciallo～(∠・ω< )⌒★", 
                                   font=("Segoe UI", 13, "bold"))
        title_label.pack(side="left", padx=15)
        title_label.bind('<Button-1>', self.start_drag)
        title_label.bind('<B1-Motion>', self.drag_window)
        
        # 右侧控制按钮容器
        control_frame = ctk.CTkFrame(titlebar, fg_color="transparent")
        control_frame.pack(side="right", padx=5)

        # 关闭按钮 - 黑色字体（亮色主题），白色字体（暗色主题）
        close_btn = ctk.CTkButton(control_frame, text="✕", width=40, height=30,
                                  command=self.close_window, corner_radius=0,
                                  fg_color="transparent", hover_color="#E81123",
                                  text_color=("black", "white"),
                                  font=("Segoe UI", 14))
        close_btn.pack(side="right")
        self.close_btn = close_btn

        # 最小化按钮 - 黑色字体（亮色主题），白色字体（暗色主题）
        min_btn = ctk.CTkButton(control_frame, text="─", width=40, height=30,
                               command=self.minimize_window, corner_radius=0,
                               fg_color="transparent", hover_color=("gray70", "gray30"),
                               text_color=("black", "white"),
                               font=("Segoe UI", 14))
        min_btn.pack(side="right")
        self.min_btn = min_btn

        # 主题切换按钮 - 衣服图标，蓝色（亮色主题），黄色（暗色主题）
        theme_icon = "👔"  # 统一使用衣服图标
        self.theme_btn = ctk.CTkButton(control_frame, text=theme_icon, width=40, height=30,
                                       command=self.toggle_theme, corner_radius=0,
                                       fg_color="transparent", hover_color=("gray70", "gray30"),
                                       text_color=("#1E90FF", "#1E90FF"),  # (亮色模式, 暗色模式)
                                       font=("Segoe UI", 16, "bold"))  # 加粗字体，确保颜色显示
        self.theme_btn.pack(side="right")

        # 版本号标识（绿色文本，主题切换按钮左侧）
        self.version_label = ctk.CTkLabel(control_frame, text=f"{APP_VERSION}",
                                          font=("Segoe UI", 11, "bold"),
                                          text_color=("#4CAF50", "#66BB6A"))
        self.version_label.pack(side="right", padx=(15, 5))
        # 传入 callable，每次悬停时动态获取最新历史统计数据
        self.create_tooltip(self.version_label, Converter._format_history_tooltip)
        
        # 主容器 - 使用普通Frame替代ScrollableFrame移除滚动条
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=(10, 10))
        
        # 0. 功能按钮组（无标题）
        self.build_control_section(main_frame)
        
        # 1. 工作目录
        self.create_section(main_frame, "📁 工作目录", self.build_dir_section)
        
        # 2. 详细设定（整合核心设置、质量参数和视频格式，可折叠，默认折叠）
        self.create_collapsible_section(main_frame, "🔧 详细设定", self.build_detailed_settings, default_collapsed=True)
        
        # 3. 任务进度
        self.create_section(main_frame, "📊 任务进度", self.build_progress_section)
        
        # 4. 日志（可折叠） , default_collapsed=True
        self.create_collapsible_section(main_frame, "📋 运行日志", self.build_log_section, default_collapsed=True)
    
    def create_section(self, parent, title, builder):
        label = ctk.CTkLabel(parent, text=title, font=("Segoe UI", 15, "bold"))
        label.pack(anchor="w", pady=(10, 5), padx=0)
        
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", pady=(0, 5))
        
        builder(frame)
    
    def create_collapsible_section(self, parent, title, builder, default_collapsed=False):
        """创建可折叠分组"""
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="x", pady=(0, 5))
        
        # 标题行（可点击）
        title_frame = ctk.CTkFrame(container, fg_color="transparent")
        title_frame.pack(fill="x", pady=(10, 5), padx=0)
        
        # 折叠状态
        is_collapsed = {"value": default_collapsed}
        
        # 标题和箭头
        title_label = ctk.CTkLabel(title_frame, text=title, font=("Segoe UI", 15, "bold"), cursor="hand2")
        title_label.pack(side="left")
        
        arrow_label = ctk.CTkLabel(title_frame, text="▶" if default_collapsed else "▼", 
                                   font=("Segoe UI", 12, "bold"), width=20, cursor="hand2")
        arrow_label.pack(side="left", padx=(5, 0))
        
        # 内容框架
        content_frame = ctk.CTkFrame(container)
        if not default_collapsed:
            content_frame.pack(fill="x")
        
        builder(content_frame)
        
        def toggle_collapse(event=None):
            is_collapsed["value"] = not is_collapsed["value"]
            if is_collapsed["value"]:
                # 折叠
                arrow_label.configure(text="▶")
                content_frame.pack_forget()
            else:
                # 展开
                arrow_label.configure(text="▼")
                content_frame.pack(fill="x")
            
            # 自动调整窗口大小
            self.after(10, self.auto_resize_window)
        
        # 绑定点击事件
        title_label.bind("<Button-1>", toggle_collapse)
        arrow_label.bind("<Button-1>", toggle_collapse)
    
    def auto_resize_window(self):
        """自动调整窗口高度以适应内容"""
        self.update_idletasks()
        
        # 保持宽度不变
        current_width = self.winfo_width()
        
        # 计算需要的高度
        # 基础高度：标题栏 + 顶部内边距
        total_height = 40 + -10  # 标题栏40px + 顶部内边距10px
        
        # 遍历主容器中的所有可见组件，累加它们的实际高度
        for child in self.winfo_children():
            if child.winfo_viewable():
                try:
                    # 获取组件的实际需求高度
                    child.update_idletasks()
                    total_height += child.winfo_reqheight()
                except:
                    pass
        
        # 添加底部内边距（与main_frame的pady保持一致）
        total_height += 0
        
        # 限制在合理范围内
        min_height = 400
        max_height = int(self.winfo_screenheight() * 0.9)  # 最大为屏幕高度的90%
        new_height = max(min_height, min(total_height, max_height))
        
        # 只在高度变化超过5px时才调整
        current_height = self.winfo_height()
        if abs(new_height - current_height) > 5:
            self.geometry(f"{current_width}x{new_height}")
    
    def build_control_section(self, parent):
        # 功能按钮（横向排列）
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=10)
        
        self.start_btn = ctk.CTkButton(btn_row, text="▶  开始转换", command=self.start_conversion,
                                       font=("Segoe UI", 17, "bold"), height=40, width=120,
                                       fg_color=("#4CAF50", "#388E3C"),
                                       hover_color=("#45A049", "#2E7D32"),
                                       corner_radius=8)
        self.start_btn.pack(side="left", padx=(0, 10))
        
        self.stop_btn = ctk.CTkButton(btn_row, text="■", command=self.stop_conversion,
                                      font=("Segoe UI", 17, "bold"), height=40, width=40, state="disabled",
                                      fg_color=("#F44336", "#E53935"),
                                      hover_color=("#E53935", "#C62828"),
                                      corner_radius=8)
        self.stop_btn.pack(side="left", padx=(0, 10))
        
        ctk.CTkButton(btn_row, text="💾", command=self.save_config,
                     width=40, height=40, font=("Segoe UI", 17, "bold"),
                     fg_color=("#2196F3", "#1976D2"),
                     hover_color=("#1976D2", "#1565C0"),
                     corner_radius=8).pack(side="left", padx=(0, 10))
    
    def build_dir_section(self, parent):
        # 从配置加载或使用当前目录作为默认值
        default_input = ';'.join(self.config.input_paths) if self.config.input_paths else os.getcwd()
        default_output = self.config.output_dir if self.config.output_dir else os.getcwd()
        
        self.input_dir_var = ctk.StringVar(value=default_input)
        self.output_dir_var = ctk.StringVar(value=default_output)
        self.sync_dirs_var = ctk.BooleanVar(value=bool(self.config.sync_dirs))
        
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="x", padx=0, pady=10)
        
        # 第一行：输入目录
        input_row = ctk.CTkFrame(container, fg_color="transparent")
        input_row.pack(fill="x", pady=(0, 10), padx=0)
        
        ctk.CTkLabel(input_row, text="输入:", font=("Segoe UI", 12), width=50).pack(side="left", padx=(0, 5))
        
        input_field = ctk.CTkFrame(input_row, fg_color="transparent")
        input_field.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.input_entry = ctk.CTkEntry(input_field, textvariable=self.input_dir_var, height=35)
        self.input_entry.pack(side="left", fill="x", expand=True)
        # 传 callable：单行装不下的多目录列表，每次悬停按当前值展开成多行
        self.create_tooltip(
            self.input_entry, lambda: self._build_input_tooltip(self.input_dir_var.get()))

        input_paste_btn = ctk.CTkButton(
            input_field, text="📋",
            command=lambda: self.paste_clipboard_to_var(self.input_dir_var, self.input_entry),
            width=35, height=35,
            fg_color=("#2196F3", "#1976D2"),
            hover_color=("#1976D2", "#1565C0"),
            corner_radius=8,
            font=("Segoe UI", 15, "bold"))
        input_paste_btn.pack(side="right", padx=(5, 0))
        self.create_tooltip(input_paste_btn, "从剪贴板粘贴输入目录")
        
        self.input_browse_btn = ctk.CTkButton(
            input_row, text="浏览", command=self.browse_input_dir, width=80, height=35,
            fg_color=("#9C27B0", "#7B1FA2"),
            hover_color=("#7B1FA2", "#6A1B9A"),
            corner_radius=8)
        self.input_browse_btn.pack(side="left", padx=(0, 10))

        # 第二行：输出目录
        output_row = ctk.CTkFrame(container, fg_color="transparent")
        output_row.pack(fill="x", padx=0)
        
        ctk.CTkLabel(output_row, text="输出:", font=("Segoe UI", 12), width=50).pack(side="left", padx=(0, 5))
        
        output_field = ctk.CTkFrame(output_row, fg_color="transparent")
        output_field.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.output_entry = ctk.CTkEntry(output_field, textvariable=self.output_dir_var, height=35)
        self.output_entry.pack(side="left", fill="x", expand=True)

        self.output_paste_btn = ctk.CTkButton(
            output_field, text="📋",
            command=lambda: self.paste_clipboard_to_var(self.output_dir_var, self.output_entry),
            width=35, height=35,
            fg_color=("#2196F3", "#1976D2"),
            hover_color=("#1976D2", "#1565C0"),
            corner_radius=8,
            font=("Segoe UI", 15, "bold"))
        self.output_paste_btn.pack(side="right", padx=(5, 0))
        self.create_tooltip(self.output_paste_btn, "从剪贴板粘贴输出目录")
        
        self.output_browse_btn = ctk.CTkButton(
            output_row, text="浏览", command=self.browse_output_dir, width=80, height=35,
            fg_color=("#9C27B0", "#7B1FA2"),
            hover_color=("#7B1FA2", "#6A1B9A"),
            corner_radius=8)
        self.output_browse_btn.pack(side="left", padx=(0, 10))

        self.sync_btn = ctk.CTkButton(
            container, text="🔗",
            command=self.toggle_input_output_sync,
            width=38, height=22,
            fg_color="transparent",
            hover_color=("gray78", "gray28"),
            text_color=("gray40", "gray75"),
            corner_radius=11,
            border_spacing=0,
            font=("Segoe UI Emoji", 10))
        self.sync_btn.place(x=25, y=40, anchor="center")
        self.create_tooltip(self.sync_btn, "开启后输出目录始终等于输入目录")

        # ↻ 递归子目录开关
        self.recursive_enabled = self.config.recursive_subdirs
        self.recursive_btn = ctk.CTkButton(
            parent.master, text="↻",
            command=self.toggle_recursive,
            width=38, height=22,
            fg_color="transparent",
            hover_color=("gray78", "gray28"),
            text_color=("gray40", "gray75"),
            corner_radius=11,
            border_spacing=0,
            font=("Segoe UI", 14))
        self.recursive_btn.place(
            in_=parent, relx=1.0, rely=0.0,
            x=RECURSIVE_BTN_RIGHT_OFFSET,
            y=RECURSIVE_BTN_ABOVE_Y,
            anchor="center")
        self.create_tooltip(self.recursive_btn, "递归扫描子目录")
        self._apply_recursive_state()

        # 路径即时校验：输入目录必须已存在；输出目录允许不存在（开始转码时会自动创建）
        self._dir_border_color = self.input_entry.cget("border_color")
        self.input_dir_var.trace_add("write", lambda *_: self.refresh_dir_entry(self.input_entry, self.input_dir_var))
        self.input_dir_var.trace_add("write", self.sync_output_to_input)
        self.output_dir_var.trace_add(
            "write", lambda *_: self.refresh_dir_entry(self.output_entry, self.output_dir_var, allow_parent=True))
        self.refresh_dir_entry(self.input_entry, self.input_dir_var)
        self.refresh_dir_entry(self.output_entry, self.output_dir_var, allow_parent=True)
        # 恢复上次的 🔗 状态：按钮配色、输出控件锁定、输出目录跟随
        self._apply_sync_dirs_state()
    
    def build_core_settings(self, parent):
        """构建核心设置区域"""
        # 第一行：FFmpeg版本
        row1 = ctk.CTkFrame(parent, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(0, 10))
        
        ctk.CTkLabel(row1, text="FFmpeg 版本:", font=("Segoe UI", 12), width=100, anchor="w").pack(side="left", padx=(0, 10))

        # 版本显示区域
        self.ffmpeg_version_label = ctk.CTkLabel(row1, text="检测中...", font=("Segoe UI", 12),
                                                 text_color=("#666666", "#AAAAAA"), anchor="w")
        self.ffmpeg_version_label.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        # 反代下载按钮（右侧）
        self.proxy_download_btn = ctk.CTkButton(row1, text="反代下载", 
                                                command=lambda: self.download_ffmpeg(use_proxy=True),
                                                width=80, height=35,
                                                fg_color=("#03A9F4", "#0288D1"),
                                                hover_color=("#29B6F6", "#039BE5"),
                                                corner_radius=8)
        self.proxy_download_btn.pack(side="right")
        
        # 直连下载按钮（右侧，反代按钮左边）
        self.download_ffmpeg_btn = ctk.CTkButton(row1, text="直连下载", command=self.download_ffmpeg,
                                                 width=80, height=35,
                                                 fg_color=("#FF9800", "#F57C00"),
                                                 hover_color=("#FB8C00", "#E64A19"),
                                                 corner_radius=8)
        self.download_ffmpeg_btn.pack(side="right", padx=(0, 10))
        
        # 第二行：自定义路径
        row2 = ctk.CTkFrame(parent, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=(0, 10))
        
        ctk.CTkLabel(row2, text="自定义路径:", font=("Segoe UI", 12), width=100, anchor="w").pack(side="left", padx=(0, 10))
        
        # 路径输入框
        self.ffmpeg_path_var = ctk.StringVar(value=self.config.ffmpeg_path)
        ffmpeg_entry = ctk.CTkEntry(row2, textvariable=self.ffmpeg_path_var, height=35, 
                                   placeholder_text="留空则检测系统PATH")
        ffmpeg_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        # 浏览按钮
        ctk.CTkButton(row2, text="浏览", command=self.browse_ffmpeg, width=80, height=35,
                     fg_color=("#9C27B0", "#7B1FA2"),
                     hover_color=("#7B1FA2", "#6A1B9A"),
                     corner_radius=8).pack(side="left")
        
        # 第三行：自定义反代
        row3 = ctk.CTkFrame(parent, fg_color="transparent")
        row3.pack(fill="x", padx=10)
        
        ctk.CTkLabel(row3, text="自定义反代:", font=("Segoe UI", 12), width=100, anchor="w").pack(side="left", padx=(0, 10))
        
        # 反代输入框（与路径输入框保持一致的样式）
        self.proxy_url_var = ctk.StringVar(value=getattr(self.config, 'proxy_url', 'https://gh-proxy.com'))
        proxy_entry = ctk.CTkEntry(row3, textvariable=self.proxy_url_var, height=35,
                                  placeholder_text="https://gh-proxy.com")
        proxy_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        # 添加一个透明占位符，保持与上一行对齐（80px宽度，与浏览按钮一致）
        placeholder = ctk.CTkLabel(row3, text="", width=80)
        placeholder.pack(side="left")
        
        # 检测FFmpeg版本
        self.after(100, self.detect_ffmpeg_version)
    
    def build_params_section(self, parent):
        """构建质量参数区域（单行布局）"""
        # 编码器选择按钮行
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(0, 5))

        ctk.CTkLabel(btn_row, text="编码器:", font=("Segoe UI", 12)).pack(side="left", padx=(0, 10))

        self.encoder_btns = {}
        for key in ('CPU', 'GPU/N', 'GPU/A', 'GPU/I'):
            b = ctk.CTkButton(btn_row, text=key, width=70, height=32,
                              corner_radius=8,
                              font=("Segoe UI", 12, "bold"),
                              command=lambda k=key: self.select_encoder(k))
            b.pack(side="left", padx=(0, 8))
            self.encoder_btns[key] = b

        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=10)
        
        # Preset组（最左）
        preset_frame = ctk.CTkFrame(inner, fg_color="transparent")
        preset_frame.pack(side="left", padx=(0, 40))
        
        ctk.CTkLabel(preset_frame, text="质量预设:", font=("Segoe UI", 12)).pack(side="left", padx=(0, 5))
        self.preset_var = ctk.StringVar(value=self.config.preset)
        presets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow']
        self.preset_menu = ctk.CTkOptionMenu(preset_frame, variable=self.preset_var, values=presets, width=120, height=35)
        self.preset_menu.pack(side="left", padx=(0, 5))
        
        preset_help = ctk.CTkLabel(preset_frame, text="❓", text_color=("#2196F3", "#64B5F6"),
                                  font=("Segoe UI", 12), cursor="hand2")
        preset_help.pack(side="left")
        self.create_tooltip(preset_help,
                           "编码预设 (Encoding Preset)\n"
                           "控制编码速度和压缩效率的平衡\n"
                           "• CPU：ultrafast ~ veryslow\n"
                           "• GPU/N(NVENC)：p1 ~ p7（p4 平衡）\n"
                           "• GPU/A(AMF)：speed/balanced/quality\n"
                           "• GPU/I(QSV)：veryfast ~ veryslow\n"
                           "• 切换编码器会自动更新可选项")
        
        # SSIM组（中间）
        ssim_frame = ctk.CTkFrame(inner, fg_color="transparent")
        ssim_frame.pack(side="left", padx=(0, 40))
        
        ctk.CTkLabel(ssim_frame, text="SSIM:", font=("Segoe UI", 12)).pack(side="left", padx=(0, 5))
        self.ssim_var = ctk.StringVar(value=str(self.config.target_ssim))
        ctk.CTkEntry(ssim_frame, textvariable=self.ssim_var, width=50, height=35).pack(side="left", padx=(0, 5))
        
        ssim_help = ctk.CTkLabel(ssim_frame, text="❓", text_color=("#2196F3", "#64B5F6"),
                                font=("Segoe UI", 12), cursor="hand2")
        ssim_help.pack(side="left")
        self.create_tooltip(ssim_help,
                           "SSIM (Structural Similarity Index)\n"
                           "结构相似性指数，衡量视频质量\n"
                           "• 值越接近1.0，质量越好\n"
                           "• 0.95以上：高质量，接近原片\n"
                           "• 0.90-0.95：良好质量\n"
                           "• 0.85-0.90：可接受质量\n"
                           "• 推荐值：0.95（高质量保留）")
        
        # CRF/质量组（最右）
        crf_frame = ctk.CTkFrame(inner, fg_color="transparent")
        crf_frame.pack(side="left", padx=(0, 0))
        
        self.crf_label = ctk.CTkLabel(crf_frame, text="CRF:", font=("Segoe UI", 12))
        self.crf_label.pack(side="left", padx=(0, 5))
        self.crf_var = ctk.StringVar(value=str(self.config.crf))
        ctk.CTkEntry(crf_frame, textvariable=self.crf_var, width=50, height=35).pack(side="left", padx=(0, 5))
        
        crf_help = ctk.CTkLabel(crf_frame, text="❓", text_color=("#2196F3", "#64B5F6"),
                               font=("Segoe UI", 12), cursor="hand2")
        crf_help.pack(side="left")
        self.create_tooltip(crf_help, 
                           "质量参数（CRF / CQ / QP / 质量值）\n"
                           "控制视频质量，数值越小质量越高、文件越大\n"
                           "• CPU(libx265) CRF：推荐 18-28\n"
                           "• GPU/N(NVENC) CQ：推荐 20-24\n"
                           "• GPU/A(AMF) QP：推荐 22-26\n"
                           "• GPU/I(QSV) 质量值：推荐 20-24\n"
                           "• 切换编码器会自动载入推荐默认值")

        # 输出格式 + 跳过编码（位于质量预设行下方，并排一行）
        opt_row = ctk.CTkFrame(parent, fg_color="transparent")
        opt_row.pack(fill="x", padx=10, pady=(0, 10))

        # 输出格式组（最左）
        out_format_frame = ctk.CTkFrame(opt_row, fg_color="transparent")
        out_format_frame.pack(side="left", padx=(0, 40))
        ctk.CTkLabel(out_format_frame, text="输出格式:", font=("Segoe UI", 12)).pack(side="left", padx=(0, 5))
        self.out_format_var = ctk.StringVar(value=self.config.out_format)
        self.out_format_menu = ctk.CTkOptionMenu(out_format_frame, variable=self.out_format_var,
                                                 values=["Auto", "mp4", "mkv"], width=100, height=35,
                                                 dynamic_resizing=False)
        self.out_format_menu.pack(side="left", padx=(0, 5))
        out_format_help = ctk.CTkLabel(out_format_frame, text="❓", text_color=("#2196F3", "#64B5F6"),
                                       font=("Segoe UI", 12), cursor="hand2")
        out_format_help.pack(side="left")
        self.create_tooltip(out_format_help,
                           "选择转码后视频的封装容器格式\n"
                           "• Auto：自动模式,优先源封装格式\n"
                           "• mp4：兼容性最佳\n"
                           "• mkv：支持多音轨/字幕/章节等特性")

        # 跳过编码组（中间）
        skip_encode_frame = ctk.CTkFrame(opt_row, fg_color="transparent")
        skip_encode_frame.pack(side="left", padx=(0, 40))
        ctk.CTkLabel(skip_encode_frame, text="跳过编码:", font=("Segoe UI", 12)).pack(side="left", padx=(0, 5))
        self.skip_encode_var = ctk.StringVar(value=self.config.skip_encode)
        self.skip_encode_menu = ctk.CTkOptionMenu(skip_encode_frame, variable=self.skip_encode_var,
                                                  values=["全部跳过", "每次询问", "全部编码"], width=100, height=35,
                                                  dynamic_resizing=False)
        self.skip_encode_menu.pack(side="left", padx=(0, 5))
        skip_encode_help = ctk.CTkLabel(skip_encode_frame, text="❓", text_color=("#2196F3", "#64B5F6"),
                                        font=("Segoe UI", 12), cursor="hand2")
        skip_encode_help.pack(side="left")
        self.create_tooltip(skip_encode_help, "跳过 HEVC 编码的文件")

        # 根据已保存的编码器初始化按钮高亮与预设选项（不覆盖已保存的参数值）
        self.select_encoder(self.config.encoder, load_defaults=False)

    def select_encoder(self, key, load_defaults=True):
        """切换编码器：高亮按钮、更新质量标签与预设选项，并按需载入推荐参数。"""
        if key not in ENCODERS:
            key = 'CPU'
        enc = ENCODERS[key]
        self.config.encoder = key

        # 按钮高亮：选中为蓝色，其余为灰色
        for k, btn in self.encoder_btns.items():
            if k == key:
                btn.configure(fg_color=("#1976D2", "#1565C0"),
                              hover_color=("#1565C0", "#0D47A1"))
            else:
                btn.configure(fg_color=("#9E9E9E", "#424242"),
                              hover_color=("#757575", "#303030"))

        # 更新质量参数标签
        self.crf_label.configure(text=f"{enc['quality_label']}:")

        # 更新预设下拉选项
        self.preset_menu.configure(values=enc['presets'])

        if load_defaults:
            # 点击按钮时载入该编码器的推荐默认值
            self.crf_var.set(str(enc['quality']))
            self.preset_var.set(enc['preset'])
            self.config.min_crf = enc['min_quality']
            self.config.crf_step = enc['quality_step']
        else:
            # 初始化：保证当前预设值在可选项内
            if self.preset_var.get() not in enc['presets']:
                self.preset_var.set(enc['preset'])
    
    def create_tooltip(self, widget, text_or_func):
        """创建工具提示。
        - text_or_func: 字符串或无参 callable（每次显示时动态获取最新文本）
        - 延迟 300ms 显示，Leave / 点击 / 按键立即销毁
        """
        if not hasattr(self, '_tooltip_state'):
            self._tooltip_state = {'tip': None, 'owner': None, 'show_id': None}
            self._bind_tooltip_global()

        get_text = text_or_func if callable(text_or_func) else lambda: text_or_func

        # 清理旧绑定
        for seq, attr in (("<Enter>", "_tip_enter"), ("<Leave>", "_tip_leave"),
                          ("<Button-1>", "_tip_click"), ("<Destroy>", "_tip_destroy")):
            old = getattr(widget, attr, None)
            if old:
                try: widget.unbind(seq, old)
                except: pass

        def _hide():
            st = self._tooltip_state
            if st['show_id']:
                try: widget.after_cancel(st['show_id'])
                except: pass
                st['show_id'] = None
            if st['tip'] is not None:
                try: st['tip'].destroy()
                except: pass
                st['tip'] = None
                st['owner'] = None

        def _show():
            st = self._tooltip_state
            st['show_id'] = None
            # 再次确认鼠标仍在 widget 上
            try:
                x, y = widget.winfo_pointerxy()
                if not (widget.winfo_rootx() <= x <= widget.winfo_rootx() + widget.winfo_width()
                        and widget.winfo_rooty() <= y <= widget.winfo_rooty() + widget.winfo_height()):
                    return
            except:
                return

            _hide()  # 销毁可能存在的旧 tooltip
            text = get_text()
            tw = ctk.CTkToplevel(self)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            # 让 tooltip 不抢焦点、不拦截鼠标事件
            try:
                tw.wm_attributes("-disabled", True)
            except:
                pass
            label = ctk.CTkLabel(tw, text=text,
                                justify="left",
                                fg_color=("#F0F0F0", "#2B2B2B"),
                                corner_radius=8,
                                padx=12, pady=8,
                                font=("Segoe UI", 12))
            label.pack()

            # 定位：widget 下方，屏幕边缘修正
            tw.update_idletasks()
            try:
                rx = widget.winfo_rootx()
                ry = widget.winfo_rooty() + widget.winfo_height() + 4
                sw, sh = tw.winfo_width(), tw.winfo_height()
                scr_w, scr_h = tw.winfo_screenwidth(), tw.winfo_screenheight()
                if rx + sw > scr_w:
                    rx = max(0, scr_w - sw - 8)
                if ry + sh > scr_h:
                    ry = max(0, widget.winfo_rooty() - sh - 4)
            except:
                pass
            tw.wm_geometry(f"+{int(rx)}+{int(ry)}")

            st['tip'] = tw
            st['owner'] = widget

        def on_enter(event):
            st = self._tooltip_state
            if st['show_id']:
                try: widget.after_cancel(st['show_id'])
                except: pass
            st['show_id'] = widget.after(300, _show)

        def on_leave(event):
            _hide()

        def on_click(event):
            _hide()

        def on_destroy(event=None):
            _hide()

        widget._tip_enter = widget.bind("<Enter>", on_enter)
        widget._tip_leave = widget.bind("<Leave>", on_leave)
        widget._tip_click = widget.bind("<Button-1>", on_click)
        widget._tip_destroy = widget.bind("<Destroy>", on_destroy, add="+")

    def _on_tip_destroyed(self, owner_widget):
        """tooltip 窗口被销毁时同步清状态"""
        st = getattr(self, '_tooltip_state', None)
        if not st:
            return
        if st['owner'] is owner_widget:
            if st['show_id']:
                try: owner_widget.after_cancel(st['show_id'])
                except: pass
            st['show_id'] = None
            st['tip'] = None
            st['owner'] = None

    def _bind_tooltip_global(self):
        """全局点击/滚轮/按键立即销毁 tooltip"""
        self.bind_all("<Button-1>", lambda e: self._hide_any_tooltip(), add="+")
        self.bind_all("<Button-2>", lambda e: self._hide_any_tooltip(), add="+")
        self.bind_all("<Button-3>", lambda e: self._hide_any_tooltip(), add="+")
        self.bind_all("<MouseWheel>", lambda e: self._hide_any_tooltip(), add="+")
        self.bind_all("<Key>", lambda e: self._hide_any_tooltip(), add="+")
        self.bind("<Destroy>", lambda e: self._hide_any_tooltip(), add="+")

    def _hide_any_tooltip(self):
        st = getattr(self, '_tooltip_state', None)
        if not st:
            return
        if st['show_id'] and st['owner'] is not None:
            try: st['owner'].after_cancel(st['show_id'])
            except: pass
        if st['tip'] is not None:
            try: st['tip'].destroy()
            except: pass
        st['tip'] = None
        st['owner'] = None
        st['show_id'] = None
    
    def build_detailed_settings(self, parent):
        """整合核心设置、质量参数和视频格式的详细设定"""
        # 创建三个独立的容器
        
        # 核心设置容器
        core_container = ctk.CTkFrame(parent, fg_color="transparent")
        core_container.pack(fill="x", padx=0, pady=(0, 15))
        
        # 添加小标题（含提示图标）
        title_row = ctk.CTkFrame(core_container, fg_color="transparent")
        title_row.pack(anchor="w", padx=10, pady=(5, 5), fill="x")

        ctk.CTkLabel(title_row, text="核心设置", font=("Segoe UI", 13, "bold"),
                    anchor="w").pack(side="left")

        version_help = ctk.CTkLabel(title_row, text="❓", text_color=("#2196F3", "#64B5F6"),
                                    font=("Segoe UI", 12), cursor="hand2")
        version_help.pack(side="left", padx=(6, 0))
        self.create_tooltip(version_help,
                            "若发现更新版本后转换文件大小异常，请尝试更换版本\n"
                            "\n推荐版本:\n"
                            "9.0.1 full_build GYanD(www.gyan.dev)\n"
                            "8.1.2 full_build GYanD(www.gyan.dev)")
        
        # 调用核心设置构建方法
        self.build_core_settings(core_container)
        
        # 质量参数容器
        params_container = ctk.CTkFrame(parent, fg_color="transparent")
        params_container.pack(fill="x", padx=0, pady=(0, 15))
        
        # 添加小标题（含提示图标）
        title_row = ctk.CTkFrame(params_container, fg_color="transparent")
        title_row.pack(anchor="w", padx=10, pady=(5, 5), fill="x")

        ctk.CTkLabel(title_row, text="质量参数", font=("Segoe UI", 13, "bold"),
                    anchor="w").pack(side="left")

        params_help = ctk.CTkLabel(title_row, text="❓", text_color=("#2196F3", "#64B5F6"),
                                   font=("Segoe UI", 12), cursor="hand2")
        params_help.pack(side="left", padx=(6, 0))
        self.create_tooltip(params_help,
                            "请优先选择使用GPU编码\n"
                            "N = NVIDIA (NVENC)\n"
                            "A = AMD (AMF)\n"
                            "I  = Intel (QSV)\n"
                            "\n若处理大文件建议降低SSIM值至0.93")
        
        # 调用原来的质量参数构建方法
        self.build_params_section(params_container)
        
        # 视频格式容器
        format_container = ctk.CTkFrame(parent, fg_color="transparent")
        format_container.pack(fill="x", padx=0, pady=(0, 0))
        
        # 添加小标题（含提示图标）
        title_row = ctk.CTkFrame(format_container, fg_color="transparent")
        title_row.pack(anchor="w", padx=10, pady=(5, 5), fill="x")

        ctk.CTkLabel(title_row, text="输入格式", font=("Segoe UI", 13, "bold"),
                    anchor="w").pack(side="left")

        format_help = ctk.CTkLabel(title_row, text="❓", text_color=("#2196F3", "#64B5F6"),
                                   font=("Segoe UI", 12), cursor="hand2")
        format_help.pack(side="left", padx=(6, 0))
        self.create_tooltip(format_help, "未在列表内的格式请自行添加")
        
        # 调用原来的视频格式构建方法
        self.build_format_section(format_container)
    
    def build_format_section(self, parent):
        self.tags_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.tags_frame.pack(fill="x", padx=10, pady=(10, 5))
        
        input_frame = ctk.CTkFrame(parent, fg_color="transparent")
        input_frame.pack(fill="x", padx=10, pady=(0, 10))
        
        self.format_entry = ctk.CTkEntry(input_frame, placeholder_text="输入格式如: mp4", height=35)
        self.format_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.format_entry.bind('<Return>', lambda e: self.add_format())
        
        # 添加按钮 - 视频主题青色
        ctk.CTkButton(input_frame, text="添加", command=self.add_format, width=80, height=35,
                     fg_color=("#03A9F4", "#0288D1"),  # 青色(亮色/暗色)
                     hover_color=("#29B6F6", "#039BE5"),
                     corner_radius=8).pack(side="left")
        
        self.update_format_tags()
        # 容器完成布局后再重排一次，确保按实际宽度正确换行
        self.after(100, self.update_format_tags)
    
    def build_progress_section(self, parent):
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="x", padx=0, pady=10)
        
        # 第一部分：全部文件
        # 标签行
        label_row1 = ctk.CTkFrame(container, fg_color="transparent")
        label_row1.pack(fill="x", pady=(0, 5), padx=10)
        
        ctk.CTkLabel(label_row1, text="全部文件", font=("Segoe UI", 12), width=80, anchor="w").pack(side="left", padx=(0, 10))
        
        self.total_label = ctk.CTkLabel(label_row1, text="0/0", font=("Segoe UI", 12))
        self.total_label.pack(side="left", padx=(0, 10))
        
        # 成功计数（绿色）
        self.success_label = ctk.CTkLabel(label_row1, text="成功:0", font=("Segoe UI", 12), 
                                         text_color=("#4CAF50", "#66BB6A"))
        self.success_label.pack(side="left", padx=(0, 10))
        
        # 失败计数（红色）
        self.failed_label = ctk.CTkLabel(label_row1, text="失败:0", font=("Segoe UI", 12), 
                                        text_color=("#F44336", "#EF5350"))
        self.failed_label.pack(side="left", padx=(0, 10))
        
        # 跳过计数（灰色）
        self.skipped_label = ctk.CTkLabel(label_row1, text="跳过:0", font=("Segoe UI", 12), 
                                         text_color=("#9E9E9E", "#757575"))
        self.skipped_label.pack(side="left", padx=(0, 10))
        
        # 速度（蓝色）
        self.speed_label = ctk.CTkLabel(label_row1, text="速度:0MB/s", font=("Segoe UI", 12),
                                       text_color=("#2196F3", "#64B5F6"))
        self.speed_label.pack(side="left", padx=(0, 10))
        
        # 倍率（黄色）
        self.rate_label = ctk.CTkLabel(label_row1, text="倍率:0x", font=("Segoe UI", 12),
                                      text_color=("#FFC107", "#FFD54F"))
        self.rate_label.pack(side="left", padx=(0, 10))
        
        # SSIM计算状态（橙色，动态省略号）
        self.ssim_status_var = ctk.StringVar(value="")
        self.ssim_status_label = ctk.CTkLabel(label_row1, textvariable=self.ssim_status_var,
                                               font=("Segoe UI", 12),
                                               text_color=("#FF9800", "#FFA726"))
        self.ssim_status_label.pack(side="left")

        # 总文件大小变化（青色，完成后显示）
        self.total_size_var = ctk.StringVar(value="")
        self.total_size_label = ctk.CTkLabel(label_row1, textvariable=self.total_size_var,
                                              font=("Segoe UI", 12),
                                              text_color=("#00BCD4", "#4DD0E1"))
        self.total_size_label.pack(side="left", padx=(0, 10))

        # 总用时（右侧）
        self.total_time_label = ctk.CTkLabel(label_row1, text="总共用时: 00:00:00", font=("Segoe UI", 12))
        self.total_time_label.pack(side="right")
        
        # 进度条行
        progress_row1 = ctk.CTkFrame(container, fg_color="transparent")
        progress_row1.pack(fill="x", pady=(0, 15), padx=10)
        
        self.progress_bar = ctk.CTkProgressBar(progress_row1, height=20,
                                                     progress_color=("#3B82F6", "#3B82F6"))
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.progress_bar.set(0)
        
        self.progress_pct = ctk.CTkLabel(progress_row1, text="0%", font=("Segoe UI", 12), width=50)
        self.progress_pct.pack(side="left")
        
        # 第二部分：当前文件
        # 标签行
        label_row2 = ctk.CTkFrame(container, fg_color="transparent")
        label_row2.pack(fill="x", pady=(0, 5), padx=10)
        
        ctk.CTkLabel(label_row2, text="当前文件", font=("Segoe UI", 12), width=80, anchor="w").pack(side="left", padx=(0, 10))
        
        # 预计用时（右侧，先布局确保不被文件名遮挡）
        self.estimated_time_label = ctk.CTkLabel(label_row2, text="预计用时: 00:00:00", font=("Segoe UI", 12))
        self.estimated_time_label.pack(side="right")
        
        # 当前文件名（固定显示宽度，超长动态滚动）
        self._marquee_text = "等待开始..."
        self._marquee_pos = 0
        self._marquee_job = None
        self.marquee_len = 42  # 固定显示字符数
        self.current_file_label = ctk.CTkLabel(label_row2, text="等待开始...",
                                               font=("Consolas", 12), anchor="w",
                                               width=300)
        self.current_file_label.pack(side="left", padx=(0, 10))
        
        # 进度条行
        progress_row2 = ctk.CTkFrame(container, fg_color="transparent")
        progress_row2.pack(fill="x", padx=10)
        
        self.current_progress_bar = ctk.CTkProgressBar(progress_row2, height=20,
                                                              progress_color=("#3B82F6", "#3B82F6"))
        self.current_progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.current_progress_bar.set(0)
        
        self.current_progress_pct = ctk.CTkLabel(progress_row2, text="0%", font=("Segoe UI", 12), width=50)
        self.current_progress_pct.pack(side="left")
    
    def build_log_section(self, parent):
        """构建日志区域"""
        # 顶部按钮行：清空日志 + 两个日志输出开关
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(10, 0))

        # 清空日志按钮（尺寸同编码器按钮，颜色保持橙色不变）
        ctk.CTkButton(btn_row, text="🗑  清空",
                      command=lambda: self.log_text.delete("0.0", "end"),
                      width=70, height=32, corner_radius=8,
                      font=("Segoe UI", 12, "bold"),
                      fg_color=("#FF9800", "#F57C00"),
                      hover_color=("#F57C00", "#E65100")).pack(side="left", padx=(0, 8))

        # SSIM 日志输出开关（外观同编码器按钮）
        self.ssim_log_btn = ctk.CTkButton(btn_row, text="SSIM日志",
                                          width=70, height=32, corner_radius=8,
                                          font=("Segoe UI", 12, "bold"),
                                          command=self.toggle_ssim_log)
        self.ssim_log_btn.pack(side="left", padx=(0, 8))

        # 汇总日志输出开关（外观同编码器按钮）
        self.summary_log_btn = ctk.CTkButton(btn_row, text="汇总日志",
                                             width=70, height=32, corner_radius=8,
                                             font=("Segoe UI", 12, "bold"),
                                             command=self.toggle_summary_log)
        self.summary_log_btn.pack(side="left", padx=(0, 8))

        # 帮助图标：说明 3 个按钮的作用
        log_help = ctk.CTkLabel(btn_row, text="❓", text_color=("#2196F3", "#64B5F6"),
                                font=("Segoe UI", 12), cursor="hand2")
        log_help.pack(side="left")
        self.create_tooltip(log_help,
                           "日志按钮说明\n"
                           "• 清空日志：清除下方日志显示框中的所有内容\n"
                           "• SSIM日志：开关是否在运行目录/log 生成每个文件的 SSIM 计算日志\n"
                           "• 汇总日志：开关是否在输出目录生成转换汇总日志\n"
                           "• 自动滚动：日志跟随最新一行；向上翻看时自动关闭，拉回最后一行自动开启\n"
                           "（蓝色为启用、灰色为关闭；前两个开关的状态自动保存）")

        # 自动关机按钮（靠右，默认不启用）
        self.auto_shutdown = False
        self.auto_shutdown_btn = ctk.CTkButton(btn_row, text="自动关机",
                                               width=70, height=32, corner_radius=8,
                                               font=("Segoe UI", 12, "bold"),
                                               command=self.toggle_auto_shutdown)
        self.auto_shutdown_btn.pack(side="right")
        self._update_auto_shutdown_style()

        # 自动滚动按钮（自动关机左侧，默认开启）
        self.auto_scroll = True
        self.auto_scroll_btn = ctk.CTkButton(btn_row, text="自动滚动",
                                             width=70, height=32, corner_radius=8,
                                             font=("Segoe UI", 12, "bold"),
                                             command=self.toggle_auto_scroll)
        self.auto_scroll_btn.pack(side="right", padx=(0, 8))
        self._set_switch_style(self.auto_scroll_btn, self.auto_scroll)

        # 初始化开关按钮高亮
        self._update_log_switch_style()

        self.log_text = ctk.CTkTextbox(parent, height=310, font=("Consolas", 13))
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(10, 10))

        # 接管纵向滚动回调，用户翻看历史/拉回底部时同步“自动滚动”开关。
        # ponytail: CTkTextbox 没有公开访问内部滚动条的接口，只能取私有属性；
        # 万一将来 CustomTkinter 改名，这里退化为开关仍可用、滚动条不再联动，不会启动崩溃。
        scrollbar = getattr(self.log_text, "_y_scrollbar", None)
        self._log_scrollbar_set = None if scrollbar is None else scrollbar.set
        self.log_text.configure(yscrollcommand=self._on_log_scroll)

    def _set_switch_style(self, btn, on):
        """开关类按钮的统一配色：蓝色为启用、灰色为关闭（同编码器按钮配色）。"""
        btn.configure(fg_color=("#1976D2", "#1565C0") if on else ("#9E9E9E", "#424242"),
                      hover_color=("#1565C0", "#0D47A1") if on else ("#757575", "#303030"))

    def _on_log_scroll(self, first, last):
        """日志框纵向视图变化回调：先驱动 CTk 自带滚动条，再同步自动滚动开关。

        停在最后一行→开启跟随，向上翻看→关闭跟随。Tk 只在滚动比例真的变化时回调，
        且回调发生在重绘阶段（insert/see 都已执行完），所以跟随写入时 last 恒为 1.0，
        不会把自己滚动的结果误判成用户翻页。"""
        if self._log_scrollbar_set is not None:
            self._log_scrollbar_set(first, last)
        # 运行日志分组默认折叠，未显示时 Tk 会回调 (0.0, 0.0) 这类无效比例，
        # 不能当成用户翻页，否则默认开启的跟随会在启动时就被自己关掉。
        if not self.log_text.winfo_ismapped():
            return
        at_bottom = float(last) >= 0.999
        if at_bottom != self.auto_scroll:
            self.auto_scroll = at_bottom
            self._set_switch_style(self.auto_scroll_btn, at_bottom)

    def toggle_auto_scroll(self):
        """手动切换日志自动滚动；重新开启时立刻回到最后一行。"""
        self.auto_scroll = not self.auto_scroll
        if self.auto_scroll:
            self.log_text.see("end")
        self._set_switch_style(self.auto_scroll_btn, self.auto_scroll)

    def _update_log_switch_style(self):
        """根据 config 中的开关状态更新两个日志开关按钮高亮（同编码器按钮配色）。"""
        self._set_switch_style(self.ssim_log_btn, self.config.gen_ssim_log)
        self._set_switch_style(self.summary_log_btn, self.config.gen_summary_log)

    def toggle_ssim_log(self):
        """切换 SSIM 日志输出开关并保存到 config。"""
        self.config.gen_ssim_log = not self.config.gen_ssim_log
        self._update_log_switch_style()
        self.config.save()

    def toggle_summary_log(self):
        """切换汇总日志输出开关并保存到 config。"""
        self.config.gen_summary_log = not self.config.gen_summary_log
        self._update_log_switch_style()
        self.config.save()

    def _update_auto_shutdown_style(self):
        """根据当前状态更新自动关机按钮高亮（同编码器按钮配色）。"""
        self._set_switch_style(self.auto_shutdown_btn, self.auto_shutdown)

    def toggle_auto_shutdown(self):
        """切换自动关机：启用前先弹窗询问确认。"""
        if not self.auto_shutdown:
            if self.show_dialog("启用自动关机？",
                                "任务队列全部完成后，将强制生成汇总日志，"
                                "随后弹窗倒数 1 分钟自动关机。",
                                dialog_type="yesno"):
                self.auto_shutdown = True
        else:
            self.auto_shutdown = False
        self._update_auto_shutdown_style()

    def _do_auto_shutdown(self):
        """任务完成后的自动关机收尾：强制生成汇总日志 → 弹窗内倒数 1 分钟关机。"""
        # 强制生成汇总日志（无视汇总日志开关）
        try:
            if self.converter and getattr(self.converter, 'last_run_stats', None):
                self.converter._save_summary_to_output(*self.converter.last_run_stats)
        except Exception as e:
            self.log(f"[警告] 自动关机前生成汇总日志失败: {e}")

        # 自定义弹窗：在窗口内倒计时，结束才执行关机（不弹系统通知窗口）
        dialog = ctk.CTkToplevel(self)
        dialog.title("")
        dialog.geometry("400x220")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (400 // 2)
        y = (dialog.winfo_screenheight() // 2) - (220 // 2)
        dialog.geometry(f"+{x}+{y}")

        state = {"remaining": 60, "cancelled": False, "after_id": None}

        header_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(20, 10))
        ctk.CTkLabel(header_frame, text="⚠️", font=("Segoe UI", 36)).pack(side="left", padx=(0, 15))
        ctk.CTkLabel(header_frame, text="即将自动关机",
                     font=("Segoe UI", 15, "bold"), anchor="w").pack(side="left", fill="x", expand=True)

        msg_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        msg_frame.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        msg_var = ctk.StringVar(value=f"汇总日志已生成，系统将在 {state['remaining']} 秒后关机。\n\n点击“取消关机”可中止。")
        ctk.CTkLabel(msg_frame, textvariable=msg_var, font=("Segoe UI", 12),
                     wraplength=350, anchor="w", justify="left").pack(fill="both", expand=True)

        button_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        button_frame.pack(fill="x", padx=20, pady=(0, 20))

        def do_cancel():
            if state["cancelled"]:
                return
            state["cancelled"] = True
            if state["after_id"]:
                try: dialog.after_cancel(state["after_id"])
                except: pass
            self.log("[提示] 已取消自动关机")
            dialog.destroy()

        def tick():
            if state["cancelled"]:
                return
            state["remaining"] -= 1
            if state["remaining"] <= 0:
                dialog.destroy()
                try:
                    subprocess.run(['shutdown', '/s', '/t', '0'],
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                except Exception as e:
                    self.log(f"[警告] 自动关机命令执行失败: {e}")
                return
            msg_var.set(f"汇总日志已生成，系统将在 {state['remaining']} 秒后关机。\n点击“取消关机”可中止。")
            state["after_id"] = dialog.after(1000, tick)

        ctk.CTkButton(button_frame, text="取消关机", command=do_cancel,
                      width=100, height=35, fg_color=("#F44336", "#D32F2F"),
                      font=("Segoe UI", 12, "bold")).pack(side="left", expand=True, padx=5)

        dialog.bind("<Escape>", lambda e: do_cancel())
        dialog.protocol("WM_DELETE_WINDOW", do_cancel)
        state["after_id"] = dialog.after(1000, tick)
    
    def start_drag(self, event):
        """开始拖动窗口：记录鼠标相对窗口左上角的偏移量"""
        self._drag_data["x"] = event.x_root - self.winfo_x()
        self._drag_data["y"] = event.y_root - self.winfo_y()

    def drag_window(self, event):
        """拖动窗口：使用屏幕绝对坐标，避免相对坐标反馈抖动产生残影"""
        x = event.x_root - self._drag_data["x"]
        y = event.y_root - self._drag_data["y"]
        self.geometry(f"+{x}+{y}")
    
    def close_window(self):
        """关闭窗口"""
        if self.converter and self.converter.is_running:
            if self.show_dialog("警告: 任务运行中，确定退出？", "", dialog_type="yesno"):
                # 设置停止标志
                self.converter.should_stop = True
                
                # 强制终止当前运行的ffmpeg进程
                if self.converter.current_process:
                    try:
                        self.converter.current_process.terminate()
                        # 等待进程结束，最多2秒
                        try:
                            self.converter.current_process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            # 如果进程没有在2秒内结束，强制杀死
                            self.converter.current_process.kill()
                            self.converter.current_process.wait()
                    except:
                        pass
                
                # 等待一小段时间让转换线程清理
                self.after(100, self.quit)
        else:
            self.quit()
    
    def minimize_window(self):
        """最小化窗口"""
        if sys.platform == 'win32':
            try:
                # 在Windows上使用Win32 API最小化窗口
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE = 6
            except:
                # 备用方案：使用withdraw和deiconify
                self.withdraw()
                self.after(100, self.deiconify)
        else:
            # 其他平台使用标准方法
            self.iconify()
    
    def toggle_theme(self):
        current = ctk.get_appearance_mode()
        new_mode = "dark" if current == "Light" else "light"
        ctk.set_appearance_mode(new_mode)
        self.config.theme = new_mode
        
        # 图标保持不变，只有颜色会根据主题自动变化
        # 不需要更新图标
    
    def start_ssim_animation(self):
        """启动SSIM计算状态动画（循环省略号）"""
        self._ssim_anim_dots = 0
        self._ssim_anim_running = True
        self._ssim_anim_step()
    
    def stop_ssim_animation(self):
        """停止SSIM计算状态动画"""
        self._ssim_anim_running = False
        self.ssim_status_var.set("")
        if hasattr(self, '_ssim_anim_job') and self._ssim_anim_job is not None:
            try:
                self.after_cancel(self._ssim_anim_job)
            except:
                pass
            self._ssim_anim_job = None
    
    def _ssim_anim_step(self):
        """SSIM动画单步：更新省略号数量"""
        if not self._ssim_anim_running:
            return
        self._ssim_anim_dots = (self._ssim_anim_dots % 3) + 1
        self.ssim_status_var.set("计算 SSIM " + "." * self._ssim_anim_dots)
        self._ssim_anim_job = self.after(500, self._ssim_anim_step)
    
    def _on_ssim_status(self, computing):
        """SSIM计算状态回调，控制动画启停"""
        if computing:
            self._ui(self.start_ssim_animation)
        else:
            self._ui(self.stop_ssim_animation)

    def paste_clipboard_to_var(self, target_var, entry=None):
        """将剪贴板文本写入目标输入变量，剪贴板不可用时保持原值。"""
        try:
            value = self.clipboard_get()
        except Exception:
            return
        # 资源管理器的"复制为路径"给出的是带双引号的路径，原样写入会导致目录校验失败
        value = value.strip().strip('"').strip()
        if value:
            target_var.set(os.path.normpath(value))
            self.scroll_entry_to_end(entry)

    def scroll_entry_to_end(self, entry):
        """长路径在输入框里只看得到开头，写入后把视图移到末尾。"""
        if entry is not None:
            entry.xview_moveto(1)

    @staticmethod
    def split_dir_paths(paths_string):
        """把分号分隔的多目录串拆成列表，顺序保留、去重、丢掉空段和包裹引号。"""
        seen = []
        for part in (paths_string or '').split(';'):
            part = part.strip().strip('"').strip()
            if part and part not in seen:
                seen.append(part)
        return seen

    def _build_input_tooltip(self, paths_string):
        """输入框只有单行，多路径靠 tooltip 每行一个看全。"""
        return '\n'.join(self.split_dir_paths(paths_string))

    def refresh_dir_entry(self, entry, target_var, allow_parent=False):
        """目录不可用时把输入框边框标红。

        输入框可以是分号分隔的多目录，逐段校验，任一段不可用就标红。
        allow_parent=True 用于输出目录：它可以还不存在，只要父目录在，
        开始转码时会自动创建。
        """
        paths = self.split_dir_paths(target_var.get())
        usable = bool(paths) and all(
            os.path.isdir(path)
            or (allow_parent and os.path.isdir(os.path.dirname(path.rstrip('\\/')) or path))
            for path in paths
        )
        entry.configure(border_color=self._dir_border_color if usable else ("#F44336", "#EF5350"))

    def sync_output_to_input(self, *_):
        """同步开启时，让输出目录持续跟随输入目录。"""
        if not self.sync_dirs_var.get():
            return
        # 多目录时输出框只显示第一个：整串分号路径不是合法目录
        paths = self.split_dir_paths(self.input_dir_var.get())
        if paths:
            self.output_dir_var.set(os.path.normpath(paths[0]))
            self.scroll_entry_to_end(self.output_entry)

    def _apply_sync_dirs_state(self):
        """按当前同步状态刷新按钮配色、输出控件可用性，并做一次同步。

        启动恢复和点击切换共用；本身不写 config，避免启动时产生多余写盘。
        """
        enabled = bool(self.sync_dirs_var.get())
        if enabled:
            self.sync_btn.configure(
                fg_color=("#2196F3", "#1976D2"),
                hover_color=("#1976D2", "#1565C0"),
                text_color=("white", "white"))
        else:
            self.sync_btn.configure(
                fg_color="transparent",
                hover_color=("gray78", "gray28"),
                text_color=("gray40", "gray75"))
        state = "disabled" if enabled else "normal"
        for control in (self.output_entry, self.output_paste_btn, self.output_browse_btn):
            control.configure(state=state)
        self.sync_output_to_input()

    def toggle_input_output_sync(self):
        """切换目录同步，并把状态记忆到 config.json。"""
        enabled = not self.sync_dirs_var.get()
        self.sync_dirs_var.set(enabled)
        self._apply_sync_dirs_state()
        self.config.sync_dirs = enabled
        self.config.save()

    def toggle_recursive(self):
        """切换递归扫描状态"""
        self.recursive_enabled = not self.recursive_enabled
        self.config.recursive_subdirs = self.recursive_enabled
        self.config.save()
        self._apply_recursive_state()

    def _apply_recursive_state(self):
        """应用递归开关状态（颜色+tooltip）"""
        if self.recursive_enabled:
            self.recursive_btn.configure(fg_color=("#2196F3", "#1976D2"), text_color="white")
        else:
            self.recursive_btn.configure(fg_color="transparent", text_color=("gray40", "gray75"))

    def browse_input_dir(self):
        """可连续选择多个输入目录，取消结束；选到的目录整体替换原有内容。"""
        existing = self.split_dir_paths(self.input_dir_var.get())
        initial = os.path.normpath(existing[0]) if existing else os.getcwd()

        picked = []
        while True:
            d = filedialog.askdirectory(
                initialdir=initial, title="选择输入目录（可继续选择，取消结束）")
            if not d:
                break
            d = os.path.normpath(d)
            if d not in picked:
                picked.append(d)
            initial = d

        if picked:
            self.input_dir_var.set(';'.join(picked))
            self.scroll_entry_to_end(self.input_entry)

    def browse_output_dir(self):
        d = filedialog.askdirectory(initialdir=self.output_dir_var.get(), title="选择输出目录")
        if d:
            self.output_dir_var.set(os.path.normpath(d))
            self.scroll_entry_to_end(self.output_entry)
    
    def add_format(self):
        fmt = self.format_entry.get().strip().lower()
        if fmt and fmt not in self.config.exts:
            self.config.exts.append(fmt)
            self.format_entry.delete(0, "end")
            self.update_format_tags()
    
    def remove_format(self, fmt):
        if fmt in self.config.exts:
            self.config.exts.remove(fmt)
            self.update_format_tags()
    
    def update_format_tags(self):
        """更新格式标签显示，支持自动换行"""
        for widget in self.tags_frame.winfo_children():
            widget.destroy()

        # 动态获取容器实际可用宽度作为换行依据（未渲染时回退到窗口宽度）
        self.tags_frame.update_idletasks()
        max_row_width = self.tags_frame.winfo_width()
        if max_row_width <= 1:
            max_row_width = self.winfo_width() - 40
        if max_row_width <= 1:
            max_row_width = 700

        # 创建一个行容器来实现换行
        current_row = None
        current_row_width = 0

        for fmt in self.config.exts:
            # 标签宽度自适应文字：文字区(约9px/字) + 左内边距 + X按钮区
            tag_width = len(fmt) * 9 + 40
            # 外间距（左右各4）计入占用宽度
            occupied = tag_width + 8

            # 如果当前行为空或超出宽度，创建新行
            if current_row is None or current_row_width + occupied > max_row_width:
                current_row = ctk.CTkFrame(self.tags_frame, fg_color="transparent")
                current_row.pack(fill="x", pady=0)
                current_row_width = 0

            # 标签容器 - 样式同编码器 CPU 按钮（蓝色、圆角8），宽度自动贴合内容、高度26
            tag_frame = ctk.CTkFrame(current_row,
                                    corner_radius=8,
                                    fg_color=("#03A9F4", "#0288D1"),  # 蓝色，同编码器选中按钮
                                    height=18)
            tag_frame.pack(side="left", padx=1, pady=0)

            # 格式文字 - 字体12号 bold，白色
            ctk.CTkLabel(tag_frame, text=fmt,
                        text_color="white",
                        font=("Segoe UI", 12, "bold")).pack(side="left", padx=(10, 2), pady=0)

            # 红色 X - 紧贴文字右侧，点击移除
            ctk.CTkButton(tag_frame, text="✕", width=18, height=18,
                         fg_color="transparent",
                         hover_color=("#1565C0", "#0D47A1"),
                         text_color="#FF5252", font=("Segoe UI", 12, "bold"),
                         corner_radius=6,
                         command=lambda f=fmt: self.remove_format(f)).pack(side="left", padx=(0, 3), pady=0)

            current_row_width += occupied
    
    def save_config(self):
        try:
            self.config.crf = int(self.crf_var.get() or 18)
            self.config.target_ssim = float(self.ssim_var.get() or 0.95)
            self.config.preset = self.preset_var.get()
            self.config.input_paths = self.split_dir_paths(self.input_dir_var.get())
            self.config.output_dir = self.output_dir_var.get()
            self.config.ffmpeg_path = self.ffmpeg_path_var.get()
            self.config.proxy_url = self.proxy_url_var.get()
            self.config.out_format = self.out_format_var.get()
            self.config.skip_encode = self.skip_encode_var.get()

            # 若自定义路径为空，尝试从系统 PATH 解析后回填
            if not self.config.ffmpeg_path.strip():
                resolved = Config.resolve_ffmpeg()
                if resolved:
                    self.config.ffmpeg_path = resolved
                    self.ffmpeg_path_var.set(resolved)

            self.config.save()

            # 自动异步检测一次版本
            threading.Thread(target=self.detect_ffmpeg_version, daemon=True).start()

            self.show_dialog("成功: 配置已保存", "", dialog_type="info")
        except Exception as e:
            self.show_dialog("错误", f"保存失败: {e}", dialog_type="error")
    
    def detect_ffmpeg_version(self):
        """检测FFmpeg版本"""
        def check_version():
            try:
                # 临时把路径同步到 config 后走 Config.resolve_ffmpeg 解析
                self.config.ffmpeg_path = self.ffmpeg_path_var.get().strip()
                ffmpeg_path = Config.resolve_ffmpeg(self.config.ffmpeg_path)
                if not ffmpeg_path:
                    self._ui(lambda: self.ffmpeg_version_label.configure(
                        text="未检测到", text_color=("#F44336", "#EF5350")))
                    self._ui(lambda: self.show_dialog(
                        "错误",
                        "未找到 ffmpeg.exe\n请确认已安装并加入 PATH，或在配置中填写完整路径",
                        dialog_type="warning"))
                    return
                result = subprocess.run([ffmpeg_path, '-version'],
                                      capture_output=True, text=True,
                                      creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0,
                                      timeout=5)
                if result.returncode == 0:
                    # 解析版本号（stdout 或 stderr 都可能，第一行通常含 version）
                    output = (result.stdout or '') + (result.stderr or '')
                    for line in output.split('\n'):
                        if 'version' in line.lower():
                            try:
                                version = line.split('version', 1)[1].strip().split()[0]
                            except IndexError:
                                continue
                            # 如果是从系统PATH检测到的，显示提示
                            if not self.ffmpeg_path_var.get().strip():
                                self._ui(lambda v=version: self.ffmpeg_version_label.configure(
                                    text=f"v{v} (系统PATH)",
                                    text_color=("#4CAF50", "#66BB6A")))
                            else:
                                self._ui(lambda v=version: self.ffmpeg_version_label.configure(
                                    text=f"v{v}",
                                    text_color=("#4CAF50", "#66BB6A")))
                            return
                self._ui(lambda: self.ffmpeg_version_label.configure(
                    text="未检测到", text_color=("#F44336", "#EF5350")))
            except:
                self._ui(lambda: self.ffmpeg_version_label.configure(
                    text="未检测到", text_color=("#F44336", "#EF5350")))

        threading.Thread(target=check_version, daemon=True).start()
    
    def browse_ffmpeg(self):
        """浏览选择FFmpeg可执行文件"""
        file_path = filedialog.askopenfilename(
            title="选择 FFmpeg 可执行文件",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")]
        )
        if file_path:
            self.ffmpeg_path_var.set(file_path)
            self.detect_ffmpeg_version()
    
    def download_ffmpeg(self, use_proxy=False):
        """下载FFmpeg"""
        # 确定使用哪个按钮显示进度
        if use_proxy:
            active_btn = self.proxy_download_btn
            inactive_btn = self.download_ffmpeg_btn
            btn_original_text = "反代下载"
        else:
            active_btn = self.download_ffmpeg_btn
            inactive_btn = self.proxy_download_btn
            btn_original_text = "直连下载"
        
        # 禁用两个按钮，活动按钮显示进度
        active_btn.configure(state="disabled", text="准备下载...")
        inactive_btn.configure(state="disabled")
        
        def download_task():
            zip_path = None
            try:
                # 所有请求（API、发布页和 ZIP）共用同一个系统代理 opener。
                opener, system_proxies = build_system_proxy_opener()
                proxy_for_log = system_proxies.get('https') or system_proxies.get('http')
                if proxy_for_log:
                    parsed_proxy = urllib.parse.urlsplit(proxy_for_log)
                    proxy_host = parsed_proxy.hostname or parsed_proxy.netloc
                    proxy_port = f":{parsed_proxy.port}" if parsed_proxy.port else ""
                    self.log(f"使用系统代理: {proxy_host}{proxy_port}")
                else:
                    self.log("未检测到系统代理，使用直接网络连接")

                # 反代前缀只用于“反代下载”；直连下载保持原始 GitHub URL，
                # 但仍通过上面构造的 Windows 系统代理 opener。
                proxy_prefix = ''
                if use_proxy:
                    proxy_url = self.proxy_url_var.get().strip()
                    if not proxy_url:
                        proxy_url = 'https://gh-proxy.com'
                    # 确保反代URL末尾有/
                    if not proxy_url.endswith('/'):
                        proxy_url += '/'
                    proxy_prefix = proxy_url
                    self.log(f"使用反代: {proxy_url}")
                
                # 通过 GitHub API 获取最新版本；API 匿名限流时回退到发布页资产列表。
                self.log("正在获取最新 FFmpeg 版本信息...")
                release_info = resolve_ffmpeg_release(
                    opener,
                    use_proxy=use_proxy,
                    proxy_prefix=proxy_prefix,
                )
                if release_info.get('source') == 'release_page':
                    self.log("GitHub API 限流，已改用发布页获取下载地址")
                download_url = release_info.get('download_url')
                if not download_url:
                    raise RuntimeError("未找到 full_build.zip 资源")

                self.log(f"最新版本: {release_info.get('tag_name', '')}")
                url = _with_proxy_prefix(download_url, proxy_prefix if use_proxy else '')
                
                # zip 先下到应用目录的临时文件，安装成功后才替换旧 Core
                script_dir = Config._app_dir()
                core_dir = os.path.join(script_dir, 'Core')
                zip_fd, zip_path = tempfile.mkstemp(prefix='ffmpeg-', suffix='.zip', dir=script_dir)
                os.close(zip_fd)
                
                # 下载文件并显示进度
                self.log("正在下载 FFmpeg...")
                
                def download_progress(downloaded, total_size):
                    if total_size > 0:
                        percent = min(100, (downloaded / total_size) * 100)
                        downloaded_mb = downloaded / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        
                        # 更新活动按钮文本显示进度
                        self._ui(lambda p=percent: active_btn.configure(
                            text=f"{int(p)}%"
                        ))
                        
                        # 更新日志显示进度
                        self.log(f"下载进度: {percent:.1f}% ({downloaded_mb:.1f}MB / {total_mb:.1f}MB)", overwrite=True)

                download_with_opener(opener, url, zip_path, download_progress)
                
                # 解压并替换 Core：只有解压出 ffmpeg.exe 后才会删掉旧版本
                self.log("正在解压 FFmpeg...")
                self._ui(lambda: active_btn.configure(text="解压中..."))
                ffmpeg_exe = install_ffmpeg_zip(zip_path, core_dir)
                
                if ffmpeg_exe:
                    # 更新路径
                    self.ffmpeg_path_var.set(ffmpeg_exe)
                    self.config.ffmpeg_path = ffmpeg_exe
                    self.config.save()
                    
                    self.log("FFmpeg 下载并安装成功！")
                    self._ui(self.detect_ffmpeg_version)
                    self._ui(lambda: self.show_dialog("成功: FFmpeg 已成功下载并配置", "", dialog_type="info"))
                else:
                    self.log("[错误] 解压后未找到 ffmpeg.exe")
                    self._ui(lambda: self.show_dialog("错误: 解压后未找到 ffmpeg.exe", "", dialog_type="error"))
                
            except Exception as e:
                err_msg = str(e)
                self.log(f"[错误] 下载失败: {err_msg}")
                self._ui(lambda: self.show_dialog("错误", f"下载失败: {err_msg}", dialog_type="error"))
            finally:
                # 临时 zip 无论成功失败都清理
                if zip_path and os.path.exists(zip_path):
                    try:
                        os.remove(zip_path)
                    except OSError:
                        pass
                # 恢复两个按钮
                self._ui(lambda: active_btn.configure(state="normal", text=btn_original_text))
                self._ui(lambda: inactive_btn.configure(state="normal"))
        
        threading.Thread(target=download_task, daemon=True).start()
    
    def _ui(self, fn):
        """把工作线程的界面更新调度到主线程。
        窗口已退出时 Tk 会抛 RuntimeError/TclError，这里静默丢弃：
        界面已经不存在了，再刷新没有意义，也不该把线程回溯打到控制台。"""
        try:
            self.after(0, fn)
        except Exception:
            pass

    def log(self, msg, overwrite=False):
        """
        输出日志
        overwrite: 如果为True，删除最后一行并替换（用于进度条式输出）
        """
        def _log():
            if overwrite:
                # 删除最后一行
                self.log_text.delete("end-2c linestart", "end-1c")
            self.log_text.insert("end", msg + '\n')
            if self.auto_scroll:
                self.log_text.see("end")
        self._ui(_log)
    
    def update_progress(self, cur, total, failed=0, elapsed=0, bitrate=0, speed=0, skipped=0):
        def _update():
            # 计算已处理数量（成功+失败+跳过）
            processed = cur + failed + skipped
            self.total_label.configure(text=f"{processed}/{total}")
            self.success_label.configure(text=f"成功:{cur}")
            self.failed_label.configure(text=f"失败:{failed}")
            self.skipped_label.configure(text=f"跳过:{skipped}")
            
            # 转换bitrate从kbits/s到MB/s (1 byte = 8 bits, 1 MB = 1024 KB)
            speed_mbs = (bitrate / 8) / 1024 if bitrate > 0 else 0
            self.speed_label.configure(text=f"速度:{speed_mbs:.2f}MB/s")
            
            # 显示倍率
            self.rate_label.configure(text=f"倍率:{speed:.2f}x" if speed > 0 else "倍率:0x")
            
            # 格式化总用时
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            self.total_time_label.configure(text=f"总共用时: {hours:02d}:{minutes:02d}:{seconds:02d}")
            
            # 综合进度：已完成文件数 + 当前文件进度，实现平滑推进
            if self.converter and self.converter.total_files > 0:
                file_idx = self.converter.current_file_index
                file_prog = self.converter.current_file_progress
                overall = (file_idx + file_prog) / self.converter.total_files
            else:
                overall = processed / total if total > 0 else 0
            overall = min(overall, 1.0)
            
            self.progress_pct.configure(text=f"{int(overall*100)}%")
            self.progress_bar.set(overall)
            # 同步更新任务栏图标进度动画
            self.set_taskbar_progress(overall)
        self._ui(_update)
    
    def update_current_progress(self, progress, remaining=0):
        """更新当前文件进度条和预计用时，同时更新总进度条实现平滑推进"""
        def _update():
            self.current_progress_bar.set(progress)
            self.current_progress_pct.configure(text=f"{int(progress*100)}%")
            
            # 同步更新Converter的当前文件进度
            if self.converter:
                self.converter.current_file_progress = progress
                # 综合计算总进度
                if self.converter.total_files > 0:
                    file_idx = self.converter.current_file_index
                    overall = (file_idx + progress) / self.converter.total_files
                    overall = min(overall, 1.0)
                    self.progress_bar.set(overall)
                    self.progress_pct.configure(text=f"{int(overall*100)}%")
                    # 同步更新任务栏图标进度动画（平滑推进）
                    self.set_taskbar_progress(overall)
            
            # 格式化预计剩余时间
            if remaining > 0:
                hours = int(remaining // 3600)
                minutes = int((remaining % 3600) // 60)
                seconds = int(remaining % 60)
                self.estimated_time_label.configure(text=f"预计用时: {hours:02d}:{minutes:02d}:{seconds:02d}")
            else:
                self.estimated_time_label.configure(text="预计用时: 00:00:00")
        self._ui(_update)
    
    def update_current(self, msg):
        # 更新当前文件标签（超长动态滚动）
        self._ui(lambda: self._set_marquee_text(msg))

    def _set_marquee_text(self, text):
        """设置当前文件显示文本，超过固定宽度时启动滚动。"""
        self._marquee_text = text
        self._marquee_pos = 0
        # 取消已有滚动任务
        if self._marquee_job is not None:
            try:
                self.after_cancel(self._marquee_job)
            except:
                pass
            self._marquee_job = None

        if len(text) <= self.marquee_len:
            self.current_file_label.configure(text=text)
        else:
            self._marquee_step()

    def _marquee_step(self):
        """滚动展示一帧，使用首尾间隔拼接实现循环滚动。"""
        text = self._marquee_text
        if len(text) <= self.marquee_len:
            self.current_file_label.configure(text=text)
            self._marquee_job = None
            return
        padded = text + "    "  # 循环间隔
        n = len(padded)
        start = self._marquee_pos % n
        view = (padded + padded)[start:start + self.marquee_len]
        self.current_file_label.configure(text=view)
        self._marquee_pos = (self._marquee_pos + 1) % n
        self._marquee_job = self.after(250, self._marquee_step)
    
    def update_stats(self, bitrate, speed, elapsed):
        """实时更新速度、倍率、总用时"""
        def _update():
            # 转换bitrate从kbits/s到MB/s
            speed_mbs = (bitrate / 8) / 1024 if bitrate > 0 else 0
            self.speed_label.configure(text=f"速度:{speed_mbs:.2f}MB/s")
            
            # 显示倍率
            self.rate_label.configure(text=f"倍率:{speed:.2f}x" if speed > 0 else "倍率:0x")
            
            # 格式化总用时
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            self.total_time_label.configure(text=f"总共用时: {hours:02d}:{minutes:02d}:{seconds:02d}")
        self._ui(_update)
    
    def start_conversion(self):
        input_paths = self.split_dir_paths(self.input_dir_var.get())
        output_dir = self.output_dir_var.get().strip()

        if not input_paths:
            self.show_dialog("错误: 输入目录不能为空", "", dialog_type="error")
            return

        missing = [p for p in input_paths if not os.path.isdir(p)]
        if missing:
            self.show_dialog("错误: 输入目录不存在", '\n'.join(missing), dialog_type="error")
            return
        
        if not output_dir:
            self.show_dialog("错误: 输出目录不能为空", "", dialog_type="error")
            return
        
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            self.show_dialog("错误", f"创建输出目录失败: {e}", dialog_type="error")
            return
        
        if self.converter and self.converter.is_running:
            self.show_dialog("警告: 任务运行中", "", dialog_type="warning")
            return
        
        try:
            self.config.crf = int(self.crf_var.get() or 18)
            self.config.target_ssim = float(self.ssim_var.get() or 0.95)
            self.config.preset = self.preset_var.get()
            self.config.out_format = self.out_format_var.get()
            # 同步当前编码器的最小质量值与步长，避免不同编码器之间 min_crf 不一致
            enc = ENCODERS.get(self.config.encoder, ENCODERS['CPU'])
            self.config.min_crf = enc['min_quality']
            self.config.crf_step = enc['quality_step']
        except:
            self.show_dialog("错误: 参数格式错误", "", dialog_type="error")
            return

        # 记住本次实际使用的目录，重启后不再退回上次"保存配置"时的路径
        self.config.input_paths = input_paths
        self.config.output_dir = output_dir
        try:
            self.config.save()
        except Exception as e:
            self.log(f"[警告] 目录未写入配置: {e}")
        
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.total_size_var.set("")
        self._set_marquee_text("初始化...")
        # 转换开始：进度条设为蓝色
        self.progress_bar.configure(progress_color=("#3B82F6", "#3B82F6"))
        self.current_progress_bar.configure(progress_color=("#3B82F6", "#3B82F6"))
        # 任务栏图标进度从 0 开始
        self.set_taskbar_progress(0.0)
        
        # 创建HEVC跳过回调
        skip_all_hevc = {"value": False}
        
        def skip_hevc_callback(file_path):
            # 按“跳过编码”设置决定：全部跳过 / 全部编码 时不弹窗
            policy = getattr(self.config, 'skip_encode', '每次询问')
            if policy == '全部跳过':
                return 'skip_all'
            if policy == '全部编码':
                return 'continue'

            # 如果已经选择跳过所有，直接返回
            if skip_all_hevc["value"]:
                return 'skip_all'
            
            # 创建自定义对话框
            dialog = ctk.CTkToplevel(self)
            dialog.title("")  # 不显示标题文字
            dialog.geometry("450x280")
            dialog.resizable(False, False)
            dialog.transient(self)
            dialog.grab_set()
            
            # 居中显示
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() // 2) - (450 // 2)
            y = (dialog.winfo_screenheight() // 2) - (280 // 2)
            dialog.geometry(f"+{x}+{y}")
            
            result_value = {"value": None}
            
            # 图标和标题区域
            header_frame = ctk.CTkFrame(dialog, fg_color="transparent")
            header_frame.pack(fill="x", padx=20, pady=(20, 10))
            
            ctk.CTkLabel(header_frame, text="❓", font=("Segoe UI", 36)).pack(side="left", padx=(0, 15))
            
            title_label = ctk.CTkLabel(header_frame, text="文件已是HEVC编码", 
                                      font=("Segoe UI", 15, "bold"),
                                      anchor="w")
            title_label.pack(side="left", fill="x", expand=True)
            
            # 文件名显示
            file_frame = ctk.CTkFrame(dialog, fg_color=("#E8E8E8", "#2B2B2B"), corner_radius=8)
            file_frame.pack(fill="x", padx=20, pady=(0, 10))
            
            ctk.CTkLabel(file_frame, text=Path(file_path).name, 
                        font=("Segoe UI", 12),
                        wraplength=400,
                        anchor="w").pack(padx=15, pady=10)
            
            # 提示文字
            ctk.CTkLabel(dialog, text="是否跳过该文件？", 
                        font=("Segoe UI", 12),
                        anchor="center").pack(fill="x", padx=20, pady=(5, 20))
            
            # 按钮区域
            button_frame = ctk.CTkFrame(dialog, fg_color="transparent")
            button_frame.pack(fill="x", padx=20, pady=(5, 20))
            
            def on_yes():
                result_value["value"] = 'skip'
                dialog.destroy()
            
            def on_no():
                result_value["value"] = 'continue'
                dialog.destroy()
            
            def on_cancel():
                skip_all_hevc["value"] = True
                result_value["value"] = 'skip_all'
                dialog.destroy()
            
            # 按钮样式与主UI一致
            btn_yes = ctk.CTkButton(button_frame, text="是 [Y]", command=on_yes, width=120, height=35,
                                   fg_color=("#4CAF50", "#388E3C"),
                                   hover_color=("#66BB6A", "#4CAF50"),
                                   font=("Segoe UI", 12, "bold"))
            btn_yes.pack(side="left", expand=True, padx=3)
            
            btn_no = ctk.CTkButton(button_frame, text="否 [N]", command=on_no, width=120, height=35,
                                  fg_color=("#2196F3", "#1976D2"),
                                  hover_color=("#42A5F5", "#2196F3"),
                                  font=("Segoe UI", 12, "bold"))
            btn_no.pack(side="left", expand=True, padx=3)
            
            btn_cancel = ctk.CTkButton(button_frame, text="全部跳过 [ESC]", command=on_cancel, width=120, height=35,
                                      fg_color=("#9E9E9E", "#616161"),
                                      hover_color=("#BDBDBD", "#757575"),
                                      font=("Segoe UI", 12, "bold"))
            btn_cancel.pack(side="left", expand=True, padx=3)
            
            # 键盘快捷键支持
            def on_key(event):
                key = event.char.lower()
                if key == 'y':
                    on_yes()
                elif key == 'n':
                    on_no()
                elif event.keysym == 'Escape':
                    on_cancel()
            
            dialog.bind("<Key>", on_key)
            dialog.focus_set()
            
            # 等待对话框关闭
            dialog.wait_window()
            
            return result_value["value"] if result_value["value"] else 'continue'
        
        self.converter = Converter(self.config, {
            'log': self.log,
            'progress': self.update_progress,
            'file_progress': self.update_current,
            'current_file_progress': self.update_current_progress,
            'stats': self.update_stats,
            'ssim_status': self._on_ssim_status
        })
        
        def worker():
            try:
                self.converter.run(input_paths, output_dir,
                                   self.recursive_enabled, skip_hevc_callback)
            except Exception as e:
                self.log(f"\n[错误] {e}")
            finally:
                def _on_finish():
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self._set_marquee_text("完成")
                    self.progress_bar.configure(progress_color=("#4CAF50", "#4CAF50"))
                    self.current_progress_bar.configure(progress_color=("#4CAF50", "#4CAF50"))
                    # 显示总文件大小变化
                    if self.converter and self.converter.size_map:
                        total_src = sum(s for s, _ in self.converter.size_map.values())
                        total_out = sum(o for _, o in self.converter.size_map.values())
                        self.total_size_var.set(Converter._format_size_pair(total_src, total_out))
                    # 任务栏图标进度填满后清除
                    self.set_taskbar_progress(1.0)
                    self.after(1500, self.clear_taskbar_progress)
                    # 自动关机：仅在未被用户中途停止时执行
                    if self.auto_shutdown and not (self.converter and self.converter.should_stop):
                        self._do_auto_shutdown()
                self._ui(_on_finish)
        
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()
    
    def stop_conversion(self):
        if self.converter and self.show_dialog("警告: 停止任务？", "", dialog_type="yesno"):
            self.converter.should_stop = True
            # 如果正在运行 SSIM 计算（同步阻塞），立即 terminate 让 calc_ssim 快速返回
            sp = getattr(self.converter, 'ssim_process', None)
            if sp is not None:
                try: sp.terminate()
                except: pass
            # 同样尝试终止正在编码的 ffmpeg
            cp = getattr(self.converter, 'current_process', None)
            if cp is not None:
                try: cp.terminate()
                except: pass
            self.stop_btn.configure(state="disabled")
            self._set_marquee_text("正在停止...")
            # 任务栏图标进度切换为暂停（黄色）状态
            self.taskbar.set_state(self._get_taskbar_hwnd(), TaskbarProgress.TBPF_PAUSED)


def main():
    app = ConverterGUI()
    app.mainloop()


if __name__ == '__main__':
    main()
