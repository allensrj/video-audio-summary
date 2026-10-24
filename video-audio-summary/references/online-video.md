# YouTube / Bilibili 链接输入

## 环境与调用

在线入口负责下载一个视频、选择并规范化字幕，然后调用现有本地流水线。最终仍由 Agent 阅读转写、查看截图、写报告并校验。它不会只根据标题或简介编写总结。

使用已有 Python 环境安装可选下载依赖：

```bash
python -m pip install -U -r <skill>/scripts/requirements-download.txt
python <skill>/scripts/online_video.py --url 'https://www.youtube.com/watch?v=jNQXAC9IVRw' --output /absolute/output/task
python <skill>/scripts/online_video.py --url 'https://www.bilibili.com/video/BV…?p=2' --output /absolute/output/task-p2 -- --frame-interval 15 --language zh --allow-model-download
```

YouTube 的 JavaScript 挑战需要运行时和配套组件。`yt-dlp[default]` 包含 EJS 组件；脚本检测 PATH 中的 Deno 或 Node 并启用。按当前官方要求，使用 Deno ≥ 2.3 或 Node ≥ 22；本项目实测环境为 Node 22。脚本不自动拉取远程 JS 组件。参见 [yt-dlp EJS 官方说明](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。

必须安装 FFmpeg、ffprobe。没有可用字幕且模型尚未缓存时，遵循本地流程配置 faster-whisper，并在允许下载模型时传入 `--allow-model-download`。来源平台会接收下载请求；音视频不上传给转写服务。

## 参数与边界

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--url` | 必填 | YouTube watch/youtu.be/shorts/embed；Bilibili BV/av 或 b23.tv 短链接 |
| `--output` | 必填 | 独立报告材料目录，最终报告为其中的 `report.md` |
| `--max-height` | 1080 | 下载不超过此高度的最高可用画质，保留音轨；没有匹配画质则明确失败 |
| `--subtitle-langs` | zh,en | 语言优先级，逗号分隔；优先作者/平台字幕，再选自动字幕 |
| `--prefer-asr` | 关闭 | 不取平台字幕，直接使用本地 ASR |
| `--cookies` | 无 | 用户明确提供的 Netscape 格式 cookies 文件，只读加载 |
| `--` 后的参数 | 本地默认值 | 支持 frame-interval、align-window、model、device、compute-type、language、chunk-seconds、transcript、allow-model-download；使用 `--参数 值` 格式 |

`--language` 控制 ASR 语言，`--subtitle-langs` 控制下载字幕语言，两者都不是报告语言。显式 `--transcript` 优先于下载字幕。平台自动字幕会标记为 `automatic_caption`；Bilibili 的 `ai-` 字幕也按自动字幕标记。不把弹幕、聊天、评论、简介当成口述内容。

仅处理一个视频：YouTube URL 携带播放列表时只取该视频；Bilibili 无 `p` 时取 P1，有 `p` 时只取指定分 P。链接里的起播时间不裁剪视频，始终处理完整选定视频。频道、合集入口、正在直播和未处理完成的直播回放不支持。旧格式返回多个媒体段时明确停止，不误把第一段当作完整视频。

## 产物、恢复与报告

对于输出 `/absolute/output/task`：

- `task.source/` 保存下载的视频、原始字幕、规范化 `captions.json` 和 `download.json`；它在输出目录旁，便于沿用本地文件不可放进输出目录的约定。
- `task/` 保存原有转写、截图、对齐与交接材料，并增加 `source.json` 来源记录。报告仍写入 `task/report.md`，随后执行原有 `validate`。
- 下载失败时，读取 `task.source/handoff.json`，其中有错误和后续动作；本次尚无可总结的媒体，不能声称报告完成。

同一命令重试会验证缓存哈希，复用完整下载及已完成预处理。中断下载保留临时文件供 yt-dlp 续传。链接/画质/字幕策略变化或完整缓存被修改时，使用新输出目录。不要手工覆盖旧任务输入。补帧仍使用 `media_pipeline.py frames --output ...`。下载缓存需保留到报告与补帧完成；不要把 cookies 文件放进任务或 Skill 包。

报告注明标题、来源链接、作者（平台有提供时）、完整选定视频时长和文字依据类型。实际阅读 `source.json` 的字幕范围、警告及转写：起止时间跨度不能证明语音完整覆盖。检查开头、结尾与长空白；自动字幕存在误识别，翻译字幕可能丢失术语；必要时另建目录做 ASR 或注明未覆盖内容。截图仍需 Agent 实际查看，不能仅凭字幕推断画面。

## 获取失败时

下载能力取决于平台、网络与访问权限，并非所有链接都保证可用。先检查原链接是否可播放、网络和下载器版本，按原命令重试。只有平台明确需要登录且用户已经提供/授权时才使用 cookies；不擅自读取浏览器账户，不上传 cookies，不绕过 DRM、付费或地区限制。没有授权时可请求用户提供已下载媒体或字幕，保留真实失败状态。

下载器基于 [yt-dlp 官方项目](https://github.com/yt-dlp/yt-dlp)。使用 Python API，不加载用户 CLI 配置，不持久化平台完整响应、临时签名下载地址或 cookies。
