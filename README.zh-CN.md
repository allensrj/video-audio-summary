# Video Audio Summary｜视频与音频总结

**简体中文** · [English](README.md) · [Agent 说明](AGENTS.md)

把本地视频、音频以及 YouTube / Bilibili 视频链接，整理成带时间依据和关键截图的总结报告。适用于办公录屏、教学视频、课堂与会议录音、软件操作演示。

脚本负责准备证据，Agent 负责阅读转写、查看画面并撰写报告。**仅运行预处理脚本，不会自动得到最终总结。**

## 能做什么

| 输入 | 处理流程 |
| --- | --- |
| 本地视频 | 提取音频 → 转写或导入字幕 → 定频截图 → 匹配候选文字 → Agent 核对并总结 |
| 本地音频 | 转写或导入字幕 → Agent 核对并总结 |
| YouTube / Bilibili 链接 | 下载单个视频 → 优先取字幕，没有则本地转写 → 接入相同视频流程 |

- 使用 faster-whisper 本地识别，默认 `small` 模型、CPU / `int8`。
- 支持导入 SRT、VTT 和带时间戳的 JSON，跳过语音识别。
- 视频默认每 30 秒截图，可对重要操作区间加密补帧。
- 保留转写片段与截图编号，让报告结论能够追溯到原始证据。
- 校验缓存哈希后恢复任务；阶段失败时保留交接材料。
- 检查报告引用、图片路径和 Agent 声明的复核覆盖范围。

## 输入格式

流水线通过 FFmpeg/ffprobe 识别和解码媒体，不按文件扩展名限制本地输入。常见格式包括：

| 类型 | 常见文件扩展名 | 说明 |
| --- | --- | --- |
| 视频 | `.mp4`、`.mov`、`.mkv`、`.webm`、`.avi`、`.m4v`、`.flv`、`.wmv`、`.ts` | 可解码的视频轨道用于截图，音频轨道用于语音转写。 |
| 音频 | `.wav`、`.mp3`、`.m4a`、`.aac`、`.flac`、`.ogg`、`.opus`、`.wma`、`.aiff`、`.amr` | 本地 ASR 前会自动转换为单声道 16 kHz PCM WAV，通常无需手动转格式。 |
| 外部字幕 / 转写 | `.srt`、`.vtt`、`.json` | 通过 `--transcript` 导入；JSON 必须包含带 `start`、`end`、`text` 的时间片段，详见[数据约定](video-audio-summary/references/data-schema.md)。 |

以上是常见输入格式，并非完整清单，也不代表每种编码组合都已逐一测试。MP4、MKV 等容器可以包含不同编码，实际兼容性取决于所安装 FFmpeg 的能力、媒体元数据与时长是否有效，以及内部轨道能否解码。修改扩展名不等于转换格式。

可以先运行 `media_pipeline.py check --input /absolute/path/file` 检查轨道和时长；该命令检查元数据，实际解码在 `prepare` 阶段执行。没有音轨的视频仍可生成仅依据画面的总结，Agent 必须注明这一限制。损坏、加密或无法解码的媒体，需要先修复或提供可解码文件再处理。

## 环境要求

- Python 3.10+。
- `PATH` 中可运行 `ffmpeg` 和 `ffprobe`。
- 需要语音识别时：安装 `requirements-asr.txt`，并准备已缓存模型或允许首次下载。
- 需要在线下载时：安装 `requirements-download.txt`。YouTube 还需要 JavaScript 运行时，使用 Node ≥ 22 或 Deno ≥ 2.3 并加入 `PATH`，详见 [yt-dlp 官方说明](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。
- 完成图文报告的 Agent 需要具备读文件、运行命令和查看图片的能力。

## 安装

下载或克隆本仓库，在仓库根目录执行。下面使用 macOS / Linux shell：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r video-audio-summary/scripts/requirements-asr.txt
python -m pip install -r video-audio-summary/scripts/requirements-download.txt
ffmpeg -version
ffprobe -version
```

FFmpeg 通过操作系统的包管理器单独安装。Windows 可直接使用虚拟环境中的 `Scripts/python.exe`，不使用上面的激活命令。本项目实际测试环境为 macOS，尚未独立验证其他平台。

本地文件配已有字幕时，核心流水线只依赖 Python 标准库与 FFmpeg。Pillow 用于生成联系表，已包含在 ASR 依赖中；只有需要链接输入时才必须安装下载依赖。

可携带的技能是 **`video-audio-summary/` 整个目录**，也提供 [video-audio-summary.zip](video-audio-summary.zip)。把它放入 Agent 的技能目录时，保持内部结构完整。压缩包不包含 Python 虚拟环境和模型。

## 直接交给 Agent 使用

让 Agent 加载 [SKILL.md](video-audio-summary/SKILL.md)，或在请求中提供该文件的绝对路径。默认输出中文；可以明确要求英文报告。

> 使用 video-audio-summary 总结 `/absolute/path/lecture.mp4`，把报告放到 `/absolute/path/output/lecture`，包含知识点、时间依据和关键截图。

> 使用 video-audio-summary 总结这个 YouTube / Bilibili 链接：`<视频链接>`。报告放到 `/absolute/path/output/video`，区分讲解内容和画面独有的信息。

Agent 应完成 `report.md` 与 `review.json`，随后执行报告校验。[AGENTS.md](AGENTS.md) 是仓库级 Agent 使用与维护说明；[SKILL.md](video-audio-summary/SKILL.md) 是技能执行入口。

## 手动运行预处理

输入和输出使用绝对路径，每个任务使用独立输出目录，原媒体不能放在该输出目录内。以下命令在仓库根目录、虚拟环境已激活的情况下执行。

### 本地视频或音频

```bash
python video-audio-summary/scripts/media_pipeline.py check \
  --input /absolute/path/lecture.mp4

python video-audio-summary/scripts/media_pipeline.py prepare \
  --input /absolute/path/lecture.mp4 \
  --output /absolute/path/output/lecture \
  --allow-model-download
```

把输入替换为音频文件即可处理录音。模型下载需要显式开启；已有缓存时可省略 `--allow-model-download`，也可通过 `--model /absolute/path/model-directory` 指定本地模型。音频在本地识别。增加 `--language zh` 或 `--language en` 可指定识别语言；省略时自动检测。

已有字幕时使用 `--transcript /absolute/path/subtitles.srt`，跳过 ASR。字幕时间戳必须与原媒体时间轴一致。

### YouTube 或 Bilibili

```bash
python video-audio-summary/scripts/online_video.py \
  --url 'https://www.youtube.com/watch?v=jNQXAC9IVRw' \
  --output /absolute/path/output/online-video \
  -- --frame-interval 15 --allow-model-download
```

把链接替换为目标视频。Bilibili 链接中的 `?p=2` 表示 P2；没有 `p` 时处理 P1。YouTube 链接即使携带播放列表参数，也只取指定视频。起播时间参数不会裁剪视频。

在线参数放在分隔符 `--` **之前**，本地流水线参数放在其**之后**：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--max-height` | `1080` | 下载不超过此高度的最高可用画质 |
| `--subtitle-langs` | `zh,en` | 字幕语言优先级，作者/平台字幕优先于自动字幕 |
| `--prefer-asr` | 关闭 | 忽略平台字幕，使用本地语音识别 |
| `--cookies` | 无 | 显式提供 Netscape 格式 cookies 文件，只读加载 |
| `--frame-interval` | `30` | 截图间隔秒数，属于本地参数 |
| `--language` | 自动检测 | ASR 语言，不是报告语言或字幕语言 |

下载的视频与字幕保存在输出目录旁，例如 `online-video.source/`。完成报告核对与补帧前应保留此缓存。更多说明见[链接输入参考](video-audio-summary/references/online-video.md)。

### 完成报告并校验

预处理结束后，交给 Agent 阅读、看图、总结。写好报告和复核记录后执行：

```bash
python video-audio-summary/scripts/media_pipeline.py validate \
  --output /absolute/path/output/lecture
```

报告尚未生成时，可以加 `--prepared-only` 仅检查预处理材料；这不代表最终报告已通过验收。需要补帧时：

```bash
python video-audio-summary/scripts/media_pipeline.py frames \
  --output /absolute/path/output/lecture \
  --start 120 --end 150 --interval 2
```

补帧后重新核对报告和 `review.json`。

## 输出文件

```text
task/
├── report.md            # Agent 撰写的最终报告
├── review.json          # 实际复核范围与结论证据
├── transcript.md/json/srt
├── frames/              # 截图、索引与可选联系表
├── alignment.json       # 候选图文关联
├── source.json          # 在线任务的来源与字幕类型
├── manifest.json        # 输入、参数、阶段和哈希
├── handoff.json         # 状态、失败原因与后续动作
├── agent_tasks.md
├── validation.json
└── work/                # 工作音频、ASR 检查点、阅读分包
```

分享 `report.md` 时保留引用的图片；需要读者继续查看证据时，同时保留 `transcript.md` 和 `frames/index.md`。预处理退出码 `0` 表示材料就绪，`2` 表示部分失败或下载失败并有交接信息，`1` 表示参数或环境错误。报告校验成功返回 `0`，失败返回 `1`。

## 限制与恢复

定频截图可能漏掉短暂画面。自动转写与平台字幕可能误识别术语、数字、公式。时间邻近只能提供候选关联，不能证明图文语义一致。Agent 必须实际检查证据并说明遗漏；结构校验不能证明事实准确。

在线下载受平台变化、网络和账号权限影响，不支持正在直播、频道/播放列表入口或 DRM 内容。不会自动读取浏览器账户。下载失败时会生成 `task.source/handoff.json`，不能当成已经完成总结。

使用相同命令恢复任务。改变输入、模型、字幕或处理参数时使用新输出目录；不要并发写入同一个输出目录。本地脚本不向 ASR 服务上传媒体；Agent 自身如何读取文件/图片取决于其运行环境。

## 开发与验证

```bash
python -m unittest discover -s tests -v
```

18 项测试使用合成媒体和模拟平台响应，覆盖本地流水线与在线适配；另已完成 YouTube、Bilibili 的真实下载与预处理测试。具体范围见[验证记录](docs/VALIDATION.md)，成功样例不代表所有在线链接始终可用。

实现参考：[运行说明](video-audio-summary/references/runtime.md)、[数据约定](video-audio-summary/references/data-schema.md)、[报告指南](video-audio-summary/references/report-guide.md)、[报告模板](video-audio-summary/assets/report-template.md)。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
