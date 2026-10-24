# Validation / 验证记录

Recorded on 2026-10-24. These notes describe observed results, not a guarantee that every input or platform will work.

记录日期：2026-10-24。以下描述实际验证结果，不代表所有输入或平台均保证可用。

## Automated tests / 自动化测试

Run from the repository root after setting up the environment:

```bash
python -m unittest discover -s tests -v
```

**18 tests passed**: eight local-pipeline tests and ten online-adapter tests. They use generated media and mocked platform responses, with real FFmpeg processing; the unit suite does not download public videos.

**18 项测试通过**：8 项本地流水线测试与 10 项在线适配测试。使用合成媒体、模拟平台响应和真实 FFmpeg 处理，单元测试不下载公开视频。

| Area / 范围 | Coverage / 覆盖内容 |
| --- | --- |
| Local media / 本地媒体 | Audio-only files, delayed audio, silent video, identical frames, Unicode/special-character paths |
| Timeline / 时间轴 | Nonzero origin, variable frame rate, SRT/VTT/JSON parsing, invalid timestamps |
| Recovery / 恢复 | Failed transcription, realignment after recovery, hash-based reuse, changed settings |
| Reporting / 报告 | Supplemental frames, report references, missing images and invalid evidence |
| URLs / 链接 | Single-video normalization, Bilibili part selection, unsupported inputs and redirects |
| Subtitles / 字幕 | Caption preference, malformed-caption fallback, forced ASR, explicit transcript precedence, rolling-caption cleanup |
| Download state / 下载状态 | Failure handoff and retry, altered cache, occupied output directory, argument override rejection |
| Credentials / 凭据 | Explicit cookie file loaded read-only; cookie value absent from persisted task JSON |

The official skill-creator `quick_validate.py` also passed for the skill directory. It is a host-provided validation tool, not a bundled project dependency.

Skill 目录通过官方 skill-creator 的 `quick_validate.py` 校验。该工具由宿主环境提供，不作为项目依赖打包。

## Real input checks / 真实输入验证

| Input / 输入 | Observed result / 结果 |
| --- | --- |
| Synthetic English audio / 合成英语语音 | Local `tiny.en` ASR completed; an Agent reviewed the transcript and produced a report that passed final structural validation. |
| Synthetic slide recording / 合成幻灯片录屏 | Audio, frames, and alignment completed; the Agent inspected the images and produced a report with screenshots that passed final validation. |
| Approximately 16-minute Chinese tutorial / 约 16 分钟中文教学录屏 | Local `small` ASR, 763 transcript segments, 33 sampled frames, and an Agent-reviewed report with seven screenshots; final validation passed. The user-provided media and report are not published here. |
| YouTube: [Me at the zoo](https://www.youtube.com/watch?v=jNQXAC9IVRw) | About 19 seconds, 360p, English platform subtitles; six segments and four sampled frames. Download and preprocessing validation passed. |
| Bilibili: [BV12N4y1M7rh, P1](https://www.bilibili.com/video/BV12N4y1M7rh?p=1) | About 314 seconds, 360p. Captions required login, so local Chinese `small` ASR was used: 135 segments and six sampled frames. Download and preprocessing validation passed. |

The two online checks validated acquisition and preprocessing, not completed summary reports. Temporary downloaded media, signed URLs, local machine paths, and user recordings are excluded from the repository.

两项在线测试验证了获取与预处理能力，没有为这两段公开视频生成正式总结报告。临时下载媒体、签名地址、本机路径及用户录屏不纳入仓库。

## Tested environment / 已测环境

- macOS arm64; Python 3.14.
- FFmpeg 9.0.2; Node 22.22.1.
- faster-whisper 1.2.1; ctranslate2 4.8.3; Pillow 12.3.0.
- yt-dlp 2026.10.21; yt-dlp-ejs 0.8.0.

## Remaining limits / 尚未验证的范围

Windows/Linux, other hardware/GPU combinations, noisy or overlapping speech, speaker diarization, board formulas, and very long recordings were not comprehensively tested. Live short-link redirects, additional Bilibili parts, and authenticated platform downloads were not each exercised against the live sites; routing/selection/read-only behavior has offline coverage.

尚未全面验证 Windows/Linux、其他硬件/GPU、噪声与多人同时发言、说话人区分、板书公式和超长录音。短链接在线重定向、更多 Bilibili 分 P 和登录后下载也未逐项联网验证；其路由、选择或只读行为由离线测试覆盖。

Frame sampling can miss short visual events. ASR and platform captions may be incomplete or inaccurate. Automated report validation checks structure and declarations; factual accuracy requires actual evidence review. Online services may change after these tests.

定频截图可能漏掉短暂画面，ASR 和平台字幕可能有遗漏或错误。自动校验检查结构与复核声明，事实准确性依赖实际证据核对。平台行为可能在测试后发生变化。
