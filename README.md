# CialloHEVC

来都来了 不点个⭐再走吗~?

基于 FFmpeg 的批量 HEVC 转码工具，带 SSIM 质量自动校准。给一个或多个目录，它会逐个转码，并自动调整质量参数直到画质达标。

界面用 CustomTkinter 写的，单文件 EXE，免安装。

## 特性

- **SSIM 自动校准**：按设定的 CRF 起点转码后计算 SSIM，未达目标值则自动降 CRF 重试，直到达标或触到质量下限
- **四种编码器**：CPU（x265）、GPU/N（NVENC）、GPU/A（AMF）、GPU/I（QSV），各自带独立的质量参数范围和预设
- **FFmpeg 一键获取**：界面内直连或反代下载并解压，也可以指定本机已有的 ffmpeg 路径
- **批量与断点**：支持自定义输入格式列表，可跳过已是 HEVC 的文件（全部跳过 / 每次询问 / 全部编码）
- **实时进度**：当前文件进度、总进度、速度、倍率、成功/失败/跳过计数
- **日志**：可选生成逐文件 SSIM 日志和转码汇总日志
- **转码历史统计**：累计文件数、平均 SSIM、总体积压缩对比
- **多目录输入**：点「浏览」弹系统原生文件夹框，按住 Ctrl / Shift 一次选多个（老系统上会退化成反复弹单选框，日志面板里会写明原因）；也可以直接往输入框里粘贴多个路径，用 `;` 分隔。输入框和输出框只有一行，鼠标悬停会把所有目录展开成一行一个
- **↻ 递归子目录**：开启后连子目录一起扫，状态记忆在 `config.json`
- **🔗 目录联动**：开启后输出跟随输入，每个文件转码到自己所在的那个目录，不会把多个来源的成品挤进同一个文件夹；此时输出框照抄输入框的全部目录并锁定，状态记忆在 `config.json`
- **自动关机**：长任务跑完自动关机，启用前会二次确认
- **深浅色主题**

## 下载

到 [Releases](../../releases) 下载 `CialloHEVC.exe`，双击即可运行。

首次使用需要 FFmpeg：在界面「核心设置」里点「直连下载」或「反代下载」，也可以在「自定义路径」填本机 ffmpeg.exe 的位置。

## 从源码运行

需要 Python 3.10+（开发环境为 3.13）。

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
1run_gui.bat
```

## 自行打包

```bash
.venv\Scripts\python.exe -m pip install pyinstaller
1pack_gui.bat
```

产物在 `dist\CialloHEVC.exe`，脚本会同时复制一份到项目根目录。

## 测试

```bash
.venv\Scripts\python.exe -m unittest discover -s test -p "test_*.py"
```

用项目 venv 里的 python 跑，并且保持 `-s test`：用例之间互相 import，换成别的发现方式会报 ImportError。

测试全部使用替身，不调用真实 ffmpeg，也不访问网络。少数用例需要真实窗口布局才能量出控件坐标，检测到 `CI=true` 时自动跳过。


## 说明

- `convert_icon.py` 用于把 `icon.png` 转成 `icon.ico`，仓库里没有包含 `icon.png`，需要自备图源才能用；打包所需的 `icon.ico` 已在仓库中
- FFmpeg 本体不随仓库分发，请通过界面下载或自行准备
- 开着 🔗 转多个目录时，逐文件的成品各回各家，但整轮的汇总日志只写进第一个目录（一轮任务只有一份汇总）
