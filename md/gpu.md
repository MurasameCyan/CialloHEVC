## 🤖 Assistant

您希望针对 **NVIDIA NVENC**、**AMD AMF**、**Intel QSV** 三种 GPU 编码方案，分别给出 **SSIM ≥ 0.95** 时的推荐参数。以下是基于常见 1080p / 4K 内容（复杂度中等）的经验值，您可以作为起点使用，并通过自动 SSIM 循环脚本进一步微调。

---

## 1. 参数对比与推荐值（SSIM > 0.95）

| GPU 方案 | 编码器 | 质量参数 | 推荐范围 | 预设/速度 | 备注 |
|----------|--------|----------|----------|------------|------|
| **NVIDIA** | `hevc_nvenc` | `-cq` | **20 ~ 24** | `-preset p4` (推荐) 或 `p2` | `p4` 平衡，`p2` 质量更好但稍慢 |
| **AMD** | `hevc_amf` | `-qp_i / -qp_p` | **22 ~ 26** | `-quality quality` (推荐) 或 `balanced` | 固定 QP，数值越小画质越好 |
| **Intel** | `hevc_qsv` | `-global_quality` | **20 ~ 24** | `-preset medium` (推荐) 或 `slow` | `global_quality` 类似于 CRF |

### 说明
- **SSIM > 0.95** 通常对应“视觉近似无损”水平，1080p 视频在上述范围内基本满足。
- **4K 视频**需要更低的 CQ/QP（建议降 2~4 个单位），或使用质量更优的预设。
- **动画/静态内容**可用略高的值（如 +2），**运动剧烈或噪点多**的内容需要更低的值。
- 如果使用 **自动 SSIM 循环脚本**，可将初始值设为推荐范围的上限（如 24/26/24），并设定步长 2 ~ 3，目标 SSIM = 0.95。

---

## 2. 示例 FFmpeg 命令片段（达到 SSIM > 0.95）

### NVIDIA
```bash
ffmpeg -hwaccel auto -i input.mp4 -c:v hevc_nvenc -preset p4 -cq 22 -tag:v hvc1 -c:a copy -movflags +faststart output.mp4
```

### AMD
```bash
ffmpeg -hwaccel auto -i input.mp4 -c:v hevc_amf -quality quality -qp_i 24 -qp_p 24 -tag:v hvc1 -c:a copy -movflags +faststart output.mp4
```

### Intel
```bash
ffmpeg -hwaccel auto -i input.mp4 -c:v hevc_qsv -preset medium -global_quality 22 -tag:v hvc1 -c:a copy -movflags +faststart output.mp4
```

---

## 3. 三种方案达到相同 SSIM 时的差异（供选型参考）

| 项目 | NVIDIA NVENC | AMD AMF | Intel QSV |
|------|--------------|---------|-----------|
| **推荐参数（起始值）** | `-cq 22 -preset p4` | `-qp 24 -quality quality` | `-global_quality 22 -preset medium` |
| **编码速度** | 较快（p4 比 p2 快 20%） | 较快 | 最快 |
| **文件大小（同 SSIM）** | 最小（NVENC 压缩效率最好） | 稍大（约 +5~10%） | 居中 |
| **画质平滑性** | 优秀（`p2` 模式） | 良好（固定 QP 无起伏） | 良好（`slow` 预设更好） |

> 对大多数用户，**NVENC p4 / cq 22** 是兼顾文件大小、速度和画质的稳妥选择；如果追求极致压缩率可改用 `p2` 并降 `cq` 至 20。

---

## 4. 自动调参建议（与之前脚本结合）

如果您使用之前的 **SSIM 循环脚本**，只需修改初始参数（如 `CQ=22`、`QP=24`、`GLOBAL_QUALITY=22`），并将步长设为 2 ~ 3，目标 SSIM 设为 0.95，脚本会自动降低参数直到达标。

例如 NVIDIA 版脚本中：
```bat
set "CQ=22"
set "MIN_CQ=16"
set "CQ_STEP=2"
set "TARGET_SSIM=0.95"
```

AMD 版改为：
```bat
set "QP=24"
set "MIN_QP=18"
set "QP_STEP=2"
```

Intel 版改为：
```bat
set "QUALITY=22"
set "MIN_QUALITY=16"
set "QUALITY_STEP=2"
```

---

## 🔧 通用建议
- **优先使用硬件编码器的质量预设**（NVENC 的 p2 / p4，AMF 的 quality，QSV 的 medium / slow）。
- **不要盲目追求低数值**：低于 18 时文件大小会急剧增加，而 SSIM 提升有限（边际递减）。
- **批量处理**推荐开启自动 SSIM 循环，确保每个视频恰好达标，节省体积。

如果您需要针对特定分辨率或内容类型（如动漫、纪录片、游戏录像）的更详细参数，可以告诉我您的使用场景，我会进一步微调推荐值。
