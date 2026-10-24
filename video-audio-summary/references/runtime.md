# 运行说明

YouTube/Bilibili 链接使用独立的 `online_video.py` 入口，参见 [链接输入说明](online-video.md)。本地文件接口和已有任务恢复方式不变。

## 环境

Python 3.10+，PATH 中需要 `ffmpeg`、`ffprobe`。核心预处理和字幕导入仅依赖标准库。ASR 与联系表为可选依赖，使用虚拟环境：

```bash
python3 -m venv /absolute/project/.venv
/absolute/project/.venv/bin/python -m pip install -r <skill>/scripts/requirements-asr.txt
```

已有可用运行环境则直接使用，不重复安装。FFmpeg 通过所在平台的包管理器安装；缺少时报告工具名称及安装需求。`check` 不下载模型；它检查库存在及媒体信息，不证明模型已缓存或可运行。

首次 ASR 使用：

```bash
python <skill>/scripts/media_pipeline.py prepare --input /absolute/audio.m4a --output /absolute/output/audio --model small --language zh --allow-model-download
```

`--model` 也可使用本地模型目录。默认 CPU/int8；CUDA 需环境实际支持。`--language` 不传则自动检测；混合语言内容应抽查效果。官方后端说明：[faster-whisper](https://github.com/SYSTRAN/faster-whisper)。

## 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--frame-interval` | 30 | 视频采样网格秒数，必须 > 0 |
| `--align-window` | 15 | 截图前后候选语音窗口秒数 |
| `--chunk-seconds` | 600 | ASR 分块长度，必须为正整数 |
| `--model` | small | 模型名称或本地路径 |
| `--device` | cpu | cpu/cuda/auto |
| `--compute-type` | int8 | 后端计算精度 |
| `--transcript` | 无 | 导入 SRT/VTT/JSON，跳过音频提取和 ASR |
| `--allow-model-download` | 否 | 允许后端下载缺失模型；不改变任务缓存身份 |

首版使用不重叠音频切块。边界处可能断句，`boundary_review_seconds` 明确提示 Agent 复核；不声称提供无损跨块语言上下文。音频工作文件保留时间轴中的空白与延迟，不能再手动删静音后导入时间戳。

首次采样解码整个视频，原尺寸 PNG 可能占用较大空间；按实际时长选择间隔。只合并字节一致的相邻 PNG，保留每次采样记录；不声称近似画面去重。补帧也会从源文件解码，适合离线处理，不保证实时速度。

`validate --prepared-only` 只检查预处理；默认检查报告。存在失败阶段时最终校验不会通过，即使 Agent 已交付带限制的部分报告，也必须说明尚未完成。

恢复使用完全相同的参数。若输入/参数改变，指定新输出目录；不自动删除旧成果。失败的 ASR 保留已完成块与音频副本。若确需重新识别某一块，在确认目录后移走对应 `work/asr-*.json` 并重试；原媒体不变。

不支持多个进程同时写同一输出目录。模型版本变化、语言/模型参数变化或人工改动中间材料时，使用新输出目录。可选依赖版本范围是兼容边界，实际运行版本由 manifest 记录；不保证所有平台和 GPU 组合。
