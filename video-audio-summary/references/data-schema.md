# 数据与时间约定

所有 JSON 使用 UTF-8。时间为秒，原媒体 format.start_time 作为源时间轴原点；规范化时间 = 源 PTS 秒 - origin。视频使用真实解码帧 PTS；音频按同一 origin 补齐延迟、保留时间间隙。外部字幕必须已经在这个时间轴上。

## 转写

可导入 JSON 数组或 `{"segments": [...]}`，每项至少有 `start`、`end`、`text`，要求 0 ≤ start < end ≤ 时长（容许 0.25 秒封装舍入）。排序后重新编号 S000001…；已有 ID 不保留。重叠字幕可保留，不去掉真实重复发言。

`transcript.json` 包含 backend、duration、segments，以及来源/模型/语言等适用字段。ASR 的 avg_logprob/no_speech_prob 是模型信号，不等于“事实准确率”。词级时间可选。原始识别文本保留，Agent 修订另存。

`work/reading-index.json` 按 10 分钟创建阅读分包，附 15 秒前文及片段 ID。分包为阅读方便，不能用分包数量判断语音是否连续。

## 图片与候选关联

`frames/F/index.json` 存首轮采样；`frames/X1F/index.json` 等存补帧。每次采样有 id、time、source_pts、planned_time、path、duplicate_of。重复画面保留独立采样时间，但共用同一图片路径；采样点之间不保证画面持续相同。

`alignment.json` 合并采样记录，增加 segment_ids、text、method。`time_window_candidate` 仅表示时间窗口相交，`no_matching_speech` 表示没有候选语音。Agent 不能据此自动确认语义一致。

## 状态

`manifest.json` 保存输入 SHA256、处理参数、媒体流、工具版本和阶段状态/产物 SHA256。`handoff.json` 保存失败原因、阶段状态、材料路径和恢复命令。完成预处理后状态仍是 awaiting_agent；部分失败为 partial。

## Agent 的 review.json

示例仅演示字段；填写真实 ID 和实际审查结果：

```json
{
  "status": "complete",
  "reviewed_segment_ids": ["S000001", "S000002"],
  "reviewed_frame_ids": ["F000001"],
  "claims": [
    {"claim": "报告中的一条关键结论", "evidence": ["S000002", "F000001"]}
  ],
  "limitations": ["按 30 秒间隔采样，快速切换画面可能未覆盖"]
}
```

音频 reviewed_frame_ids 为空列表。complete 要求全部转写和全部不重复采样图已经复核；只完成部分时用 partial，并说明遗漏范围。报告用 `[S000001](transcript.md#S000001)` 和 `[F000001](frames/index.md#F000001)` 引用；同时写可读时间。

validator 检查文件、引用和复核声明，无法证明 Agent 真的看过材料，也不能判定结论是否符合证据。仍需人工/Agent 内容抽查。
