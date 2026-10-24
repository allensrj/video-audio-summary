# Video Audio Summary

[简体中文](README.zh-CN.md) · **English** · [Agent instructions](AGENTS.md)

An Agent skill that turns local video, audio, and YouTube/Bilibili links into evidence-backed reports with timestamps and key screenshots. Designed for office recordings, tutorials, lectures, meetings, and software demonstrations.

Python scripts prepare the evidence. An Agent reads the transcript, inspects the images, and writes the final report. **Running the preprocessing script alone does not generate a finished summary.**

## What it does

| Input | Processing |
| --- | --- |
| Local video | Extract audio → transcribe or import subtitles → sample frames → align candidate transcript segments → Agent reviews and summarizes |
| Local audio | Transcribe or import subtitles → Agent reviews and summarizes |
| YouTube / Bilibili link | Download one video → prefer available subtitles, fall back to local ASR → use the same video workflow |

- Local speech recognition with faster-whisper; the default model is `small`, running on CPU with `int8`.
- Import SRT, VTT, or timestamped JSON instead of running ASR.
- Sample video frames every 30 seconds by default; add denser samples for important steps.
- Preserve timestamped transcript and frame IDs so readers can trace claims to evidence.
- Resume matching tasks with hash checks, and retain handoff instructions when a stage fails.
- Validate report references, image paths, and declared review coverage.

## Requirements

- Python 3.10+.
- `ffmpeg` and `ffprobe` available on `PATH`.
- For ASR: the packages in `requirements-asr.txt` and a locally cached model, or permission to download one.
- For online videos: the packages in `requirements-download.txt`. YouTube also needs a supported JavaScript runtime; use Node ≥ 22 or Deno ≥ 2.3 on `PATH`. See the [yt-dlp runtime documentation](https://github.com/yt-dlp/yt-dlp/wiki/EJS).
- An Agent with file reading, command execution, and image inspection capabilities to finish video reports.

## Setup

Download or clone this repository, then run the following from its root. These commands use a macOS/Linux shell:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r video-audio-summary/scripts/requirements-asr.txt
python -m pip install -r video-audio-summary/scripts/requirements-download.txt
ffmpeg -version
ffprobe -version
```

Install FFmpeg separately with your operating system's package manager. On Windows, use the virtual environment's `Scripts/python.exe` instead of the activation command above. The tested environment is macOS; other platforms have not been independently verified.

For local files with existing subtitles, the core pipeline uses only Python's standard library and FFmpeg. Pillow adds contact sheets; it is included in the ASR requirements. Install the download requirements only when you need URL inputs.

The portable skill is the **`video-audio-summary/` directory**, also available as [video-audio-summary.zip](video-audio-summary.zip). Keep its directory structure intact when adding it to your Agent's skill location. A virtual environment and downloaded models are not included in the archive.

## Ask your Agent

Load [SKILL.md](video-audio-summary/SKILL.md) in your Agent, or provide its absolute path along with the request. The skill currently defaults to Chinese reports; request English when desired.

> Use video-audio-summary to summarize `/absolute/path/lecture.mp4`. Write an English report in `/absolute/path/output/lecture`, including key concepts, timestamps, and screenshots.

> Use video-audio-summary to summarize this YouTube/Bilibili link: `<video URL>`. Save the report in `/absolute/path/output/video` and distinguish spoken explanations from information visible only on screen.

The Agent should finish `report.md` and `review.json`, then run the report validator. [AGENTS.md](AGENTS.md) describes this workflow and repository maintenance rules; [SKILL.md](video-audio-summary/SKILL.md) is the operational skill entry point.

## Run preprocessing yourself

Use absolute input/output paths and a separate output directory for each task. Keep the input file outside that output directory. Run these examples from the repository root with the virtual environment active.

### Local video or audio

```bash
python video-audio-summary/scripts/media_pipeline.py check \
  --input /absolute/path/lecture.mp4

python video-audio-summary/scripts/media_pipeline.py prepare \
  --input /absolute/path/lecture.mp4 \
  --output /absolute/path/output/lecture \
  --allow-model-download
```

The same commands accept an audio file as input. Model downloads are opt-in: omit `--allow-model-download` when using a cached model, or use `--model /absolute/path/model-directory`. Audio is transcribed locally. Set `--language en` or `--language zh` to guide recognition; omitting it enables automatic language detection.

With existing subtitles, use `--transcript /absolute/path/subtitles.srt`; this skips ASR. Subtitle timestamps must match the original media timeline.

### YouTube or Bilibili

```bash
python video-audio-summary/scripts/online_video.py \
  --url 'https://www.youtube.com/watch?v=jNQXAC9IVRw' \
  --output /absolute/path/output/online-video \
  -- --frame-interval 15 --allow-model-download
```

Replace the URL with your video link. A Bilibili URL with `?p=2` selects P2; without `p`, it selects P1. YouTube URLs containing a playlist still select only the named video. Start-time parameters do not trim the video.

Online options go **before** the `--` separator; local pipeline options go **after** it:

| Option | Default | Meaning |
| --- | --- | --- |
| `--max-height` | `1080` | Highest available video at or below this height |
| `--subtitle-langs` | `zh,en` | Preferred subtitle languages; platform-provided tracks precede automatic captions |
| `--prefer-asr` | Off | Ignore platform subtitles and use local ASR |
| `--cookies` | None | Explicitly supplied Netscape-format cookie file; loaded read-only |
| `--frame-interval` | `30` | Seconds between frame samples; local pipeline option |
| `--language` | Auto | ASR language; does not set the report or subtitle language |

Downloaded media and subtitles live in a sibling directory, e.g. `online-video.source/`. Keep that cache until review and any supplemental frame extraction are complete. See the [online input reference](video-audio-summary/references/online-video.md) for details.

### Finish and validate

Give the prepared directory to an Agent to read, inspect, and summarize. After it writes the report and review record:

```bash
python video-audio-summary/scripts/media_pipeline.py validate \
  --output /absolute/path/output/lecture
```

Add `--prepared-only` to check preprocessing artifacts before a report exists. This does not validate a finished report. For additional frames:

```bash
python video-audio-summary/scripts/media_pipeline.py frames \
  --output /absolute/path/output/lecture \
  --start 120 --end 150 --interval 2
```

Recheck the report and `review.json` after adding frames.

## Outputs

```text
task/
├── report.md            # Written by the Agent
├── review.json          # Actual review coverage and claim evidence
├── transcript.md/json/srt
├── frames/              # Images, index, and optional contact sheets
├── alignment.json       # Candidate speech/image associations
├── source.json          # Online source and subtitle provenance, when applicable
├── manifest.json        # Input identity, settings, stages, and hashes
├── handoff.json         # Status, failures, and next steps
├── agent_tasks.md
├── validation.json
└── work/                # Audio, ASR checkpoints, and reading packets
```

Share `report.md` with its referenced images. Keep `transcript.md` and `frames/index.md` as well when sharing the linked evidence. A preparation exit code of `0` means evidence is ready, `2` indicates a partial/download failure with handoff information, and `1` indicates a parameter or environment error. Report validation returns `0` on success and `1` on failure.

## Limitations and recovery

Frame sampling can miss brief screen changes. Automatic transcription and platform subtitles can misrecognize words, numbers, or formulas. Timestamp proximity provides candidate associations, not proof of semantic agreement. The Agent must inspect the evidence and disclose gaps; structural validation cannot prove factual accuracy.

Online availability depends on platform behavior, network access, and account permissions. Live streams, channel/playlist inputs, and DRM-protected content are not supported. Browser cookies are never read automatically. A failed download creates `task.source/handoff.json`; it is not a completed summary.

Repeat the same command to resume. Use a new output directory when changing inputs, models, subtitles, or processing settings. Do not run concurrent jobs against one output directory. Local processing does not upload media to an ASR service; an Agent's own file/image handling follows that Agent's environment.

## Development and verification

```bash
python -m unittest discover -s tests -v
```

The 18 tests exercise the local pipeline and online adapter with synthetic media and mocked platform responses. Separate real YouTube and Bilibili download/preprocessing checks passed. See [validation notes](docs/VALIDATION.md) for the exact scope; these results do not guarantee every online video will remain accessible.

Implementation references: [runtime](video-audio-summary/references/runtime.md), [data schema](video-audio-summary/references/data-schema.md), [report guide](video-audio-summary/references/report-guide.md), and [report template](video-audio-summary/assets/report-template.md).
