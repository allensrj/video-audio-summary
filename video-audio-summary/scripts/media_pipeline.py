#!/usr/bin/env python3
"""Local media evidence pipeline. Python 3.10+, FFmpeg; ASR/Pillow optional."""
import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import math
import re
import shlex
import shutil
import subprocess
import sys
import wave
from pathlib import Path

VERSION = '1.0.0'


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(args):
    # Container metadata may contain truncated or legacy encoded byte strings.
    # Decode diagnostics tolerantly; media timestamps are ASCII and unaffected.
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if p.returncode:
        raise RuntimeError(p.stderr[-4000:] or f'Command failed: {args[0]}')
    return p


def timestamp(seconds, subtitle=False):
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02}:{m:02}:{s:02}' + (f',{ms:03}' if subtitle else '')


def number(value):
    n = float(value)
    if not math.isfinite(n):
        raise ValueError('时间必须是有限数值')
    return n


def probe(path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path]).stdout)
    fmt = data['format']
    origin = number(fmt.get('start_time', 0))
    duration = number(fmt.get('duration', 0))
    if duration <= 0:
        raise ValueError('无法取得有效媒体时长')
    audio = [s for s in data['streams'] if s['codec_type'] == 'audio']
    video = [s for s in data['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')]
    choose = lambda streams: next((s for s in streams if s.get('disposition', {}).get('default')), streams[0] if streams else None)
    if not audio and not video:
        raise ValueError('文件没有可处理的音频或视频流')
    # Some containers report an absolute end as format.duration when start_time
    # is nonzero. Derive the timeline end from packets, without buffering them.
    if origin != 0:
        cmd = ['ffprobe', '-v', 'error', '-show_packets', '-show_entries',
               'packet=stream_index,pts_time,duration_time', '-of', 'compact=p=0:nk=0', str(path)]
        allowed = {str(s['index']) for s in audio + video}
        last = origin
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as process:
            for line in process.stdout:
                fields = dict(part.split('=', 1) for part in line.strip().split('|') if '=' in part)
                if fields.get('stream_index') in allowed and fields.get('pts_time', 'N/A') != 'N/A':
                    length = fields.get('duration_time', '0')
                    last = max(last, float(fields['pts_time']) + (float(length) if length != 'N/A' else 0))
            if process.wait():
                raise RuntimeError('无法扫描媒体时间轴')
        if last > origin:
            duration = last - origin
    return {'duration': duration, 'origin': origin, 'audio': choose(audio), 'video': choose(video), 'streams': data['streams']}


def normalize_segments(rows, duration):
    result = []
    for row in rows:
        start, end = number(row['start']), number(row['end'])
        text = str(row['text']).strip()
        if not (0 <= start < duration and start < end <= duration + 0.25):
            raise ValueError(f'转写时间超出范围或无效: {start}..{end}, duration={duration}')
        if text:
            result.append({**row, 'start': start, 'end': min(end, duration), 'text': text})
    result.sort(key=lambda r: (r['start'], r['end']))
    for i, row in enumerate(result, 1):
        row['id'] = f'S{i:06}'
    return result


def import_transcript(path, duration):
    path = Path(path)
    if path.suffix.lower() == '.json':
        data = read(path)
        rows = data if isinstance(data, list) else data['segments']
    else:
        content = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
        pattern = r'(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})'
        timing = re.compile(r'^' + pattern + r'\s+-->\s+' + pattern + r'(?:\s.*)?$')
        rows = []
        lines = content.splitlines()
        for i, line in enumerate(lines):
            match = timing.match(line.strip())
            if not match:
                continue
            g = match.groups()
            def sec(parts):
                h, m, s, ms = parts
                if int(m) >= 60 or int(s) >= 60:
                    raise ValueError('字幕时间格式无效')
                return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
            body = []
            for following in lines[i + 1:]:
                if not following.strip():
                    break
                body.append(following)
            rows.append({'start': sec(g[:4]), 'end': sec(g[4:]), 'text': '\n'.join(body)})
        if not rows:
            raise ValueError('未找到有效 SRT/VTT 字幕；JSON 需要 segments/start/end/text')
    return {'backend': 'import', 'source': str(path.resolve()), 'segments': normalize_segments(rows, duration)}


def export_transcript(out, data):
    save(out / 'transcript.json', data)
    md = ['# 原始转写\n', '识别结果未经人工校订；修改请另存并记录依据。\n']
    srt = []
    for i, s in enumerate(data['segments'], 1):
        md.append(f"<a id=\"{s['id']}\"></a>\n\n## {s['id']} {timestamp(s['start'])}–{timestamp(s['end'])}\n\n{s['text']}\n")
        srt.append(f"{i}\n{timestamp(s['start'], True)} --> {timestamp(s['end'], True)}\n{s['text']}\n")
    (out / 'transcript.md').write_text('\n'.join(md), encoding='utf-8')
    (out / 'transcript.srt').write_text('\n'.join(srt), encoding='utf-8')
    # Reading packets are bounded by time; all segments remain in the transcript.
    packets = []
    for start in range(0, max(1, math.ceil(data['duration'])), 600):
        rows = [s for s in data['segments'] if s['end'] > max(0, start - 15) and s['start'] < start + 600]
        name = f'work/read-{start:08}.md'
        (out / name).write_text('\n\n'.join(f"[{s['id']}] {timestamp(s['start'])}–{timestamp(s['end'])} {s['text']}" for s in rows) or '本区间无识别文本。', encoding='utf-8')
        packets.append({'start': start, 'end': min(start + 600, data['duration']), 'path': name, 'segments': [s['id'] for s in rows]})
    save(out / 'work/reading-index.json', packets)


def transcribe(out, info, args):
    if args.transcript:
        data = import_transcript(args.transcript, info['duration'])
    elif not info['audio']:
        data = {'backend': 'none', 'reason': 'no_audio_stream', 'segments': []}
    else:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError('缺少 faster-whisper；安装 scripts/requirements-asr.txt 或用 --transcript 导入字幕。') from e
        model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type,
                             local_files_only=not args.allow_model_download)
        audio_duration = probe(out / 'work/audio.wav')['duration']
        all_rows, languages = [], []
        # Non-overlapping chunks avoid unreliable text deduplication. Boundary context is
        # explicitly flagged for Agent review; each completed chunk is independently cached.
        for i, start in enumerate(range(0, math.ceil(audio_duration), args.chunk_seconds)):
            end = min(start + args.chunk_seconds, audio_duration)
            cache = out / f'work/asr-{i:05}.json'
            if cache.exists():
                chunk = read(cache)
            else:
                wav = out / f'work/chunk-{i:05}.wav'
                run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', start, '-i', out / 'work/audio.wav', '-t', end - start, '-c:a', 'pcm_s16le', wav])
                # FFmpeg already created canonical PCM; passing samples avoids a
                # second decoder and PyAV version-specific file-open arguments.
                import numpy as np
                with wave.open(str(wav)) as pcm:
                    if (pcm.getframerate(), pcm.getnchannels(), pcm.getsampwidth()) != (16000, 1, 2):
                        raise ValueError('ASR 工作音频必须为 16kHz 单声道 PCM16')
                    samples = np.frombuffer(pcm.readframes(pcm.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
                segments, meta = model.transcribe(samples, language=args.language, vad_filter=True, word_timestamps=True)
                rows = []
                for s in segments:
                    rows.append({'start': s.start + start, 'end': s.end + start, 'text': s.text,
                                 'avg_logprob': s.avg_logprob, 'no_speech_prob': s.no_speech_prob,
                                 'words': [{'start': w.start + start, 'end': w.end + start, 'word': w.word} for w in (s.words or [])]})
                chunk = {'start': start, 'end': end, 'language': meta.language, 'segments': rows}
                save(cache, chunk)
            all_rows.extend(chunk['segments'])
            languages.append(chunk['language'])
        data = {'backend': 'faster-whisper', 'model': args.model, 'languages': languages,
                'boundary_review_seconds': list(range(args.chunk_seconds, math.ceil(audio_duration), args.chunk_seconds)),
                'segments': normalize_segments(all_rows, info['duration'])}
    data['duration'] = info['duration']
    export_transcript(out, data)


def extract_frames(source, out, info, interval, start=0, end=None, prefix='F'):
    end = min(end if end is not None else info['duration'], info['duration'])
    folder = out / 'frames' / prefix
    folder.mkdir(parents=True, exist_ok=True)
    # Remove only this pipeline-owned extraction's PNGs before retry.
    for p in folder.glob('frame-*.png'):
        p.unlink()
    # Select first real frame at/after each grid boundary, retaining actual PTS.
    expr = f"gte(t,{start})*lt(t,{end})*if(isnan(prev_selected_t),1,gt(floor((t-{start})/{interval}),floor((prev_selected_t-{start})/{interval})))"
    vf = f"setpts=PTS-({info['origin']})/TB,select='{expr}',showinfo"
    p = run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-copyts', '-i', source, '-map', f"0:{info['video']['index']}",
             '-an', '-vf', vf, '-fps_mode', 'vfr', folder / 'frame-%06d.png'])
    pts = [float(t) for t in re.findall(r'\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:([-+\d.eE]+)', p.stderr)]
    files = sorted(folder.glob('frame-*.png'))
    if len(files) != len(pts) or not files:
        raise RuntimeError('截图与真实时间戳数量不一致，或指定范围没有视频帧')
    records, previous_hash, previous_path = [], None, None
    for i, (path, t) in enumerate(zip(files, pts), 1):
        sha = digest(path)
        duplicate = sha == previous_hash
        if duplicate:
            path.unlink()
            actual_path = previous_path
        else:
            actual_path = path.relative_to(out).as_posix()
        records.append({'id': f'{prefix}{i:06}', 'time': t, 'source_pts': t + info['origin'],
                        'planned_time': start + max(0, math.floor((t - start + 1e-6) / interval)) * interval,
                        'path': actual_path, 'duplicate_of': records[-1]['id'] if duplicate else None})
        previous_hash, previous_path = sha, actual_path
    save(folder / 'index.json', records)
    return records


def align(out, records, window):
    data = read(out / 'transcript.json') if (out / 'transcript.json').exists() else {'segments': []}
    rows = []
    for f in records:
        matches = [s for s in data['segments'] if s['start'] <= f['time'] + window and s['end'] >= f['time'] - window]
        rows.append({**f, 'method': 'time_window_candidate' if matches else 'no_matching_speech',
                     'segment_ids': [s['id'] for s in matches], 'text': '\n'.join(s['text'] for s in matches)})
    save(out / 'alignment.json', rows)
    md = ['# 截图索引\n\n时间接近仅表示候选关联；需要 Agent 看图核对。\n']
    for f in rows:
        md.append(f"<a id=\"{f['id']}\"></a>\n\n## {f['id']} {timestamp(f['time'])}\n\n![{f['id']}](../{f['path']})\n\n候选：{', '.join(f['segment_ids']) or '无对应语音'}\n")
    (out / 'frames/index.md').write_text('\n'.join(md), encoding='utf-8')
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    unique = [r for r in rows if not r['duplicate_of']]
    for batch in range(0, len(unique), 24):
        page = unique[batch:batch + 24]
        sheet = Image.new('RGB', (960, math.ceil(len(page) / 3) * 210), 'white')
        draw = ImageDraw.Draw(sheet)
        for i, f in enumerate(page):
            with Image.open(out / f['path']) as im:
                im.thumbnail((312, 180))
                x, y = (i % 3) * 320, (i // 3) * 210
                sheet.paste(im, (x, y))
                draw.text((x + 2, y + 183), f"{f['id']} {timestamp(f['time'])}", fill='black')
        sheet.save(out / f'frames/contact-{batch // 24 + 1:03}.jpg')


def handoff(out, manifest, argv):
    failures = {k: v.get('error') for k, v in manifest['stages'].items() if v['status'] == 'failed'}
    state = 'partial' if failures else 'awaiting_agent'
    argv = list(argv)
    for flag in ('--input', '--output', '--transcript'):
        if flag in argv:
            index = argv.index(flag) + 1
            argv[index] = str(Path(argv[index]).resolve())
    finished = [read(p) for p in sorted((out / 'work').glob('asr-*.json'))]
    covered = [(p['start'], p['end']) for p in finished]
    pending = []
    if 'transcript' in failures:
        step = manifest['signature']['config']['chunk_seconds']
        pending = [[t, min(t + step, manifest['media']['duration'])]
                   for t in range(0, math.ceil(manifest['media']['duration']), step)
                   if not any(a <= t < b for a, b in covered)]
    save(out / 'handoff.json', {'status': state, 'stages': manifest['stages'], 'failures': failures,
                               'transcription_completed_ranges': covered, 'transcription_pending_ranges': pending,
                               'resume_command': shlex.join([sys.executable, str(Path(__file__).resolve()), *argv]),
                               'next': ['阅读全部转写分包并形成主题摘要', '视频逐批看图并核对候选关联', '撰写 report.md 与 review.json', '运行 validate'],
                               'artifacts': sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file() and p.name != 'handoff.json')})
    (out / 'agent_tasks.md').write_text(f'''# Agent 后续任务

预处理状态：{state}。脚本没有生成最终总结。

1. 阅读 manifest.json 和 handoff.json，明确失败阶段与范围，必要时按恢复命令重试。
2. 阅读 transcript.md 或 work/reading-index.json 中的全部分包，记录覆盖区间和证据 ID；先形成文字初稿。
3. 视频任务阅读 frames/index.md、alignment.json；逐批查看截图，关键图打开原图。时间匹配不是语义证明。
4. 检查切块边界、数字、术语、结论和图文冲突。需要时通过 frames 子命令补帧。
5. 按 Skill 的 references/report-guide.md 及 assets/report-template.md 撰写 report.md。
6. 创建 review.json，记录实际复核范围、关键结论引用和限制；格式见 references/data-schema.md。
7. 运行 validate。只能报告实际完成的范围，不得将预处理成功当成报告完成。

失败信息：{json.dumps(failures, ensure_ascii=False)}
''', encoding='utf-8')


def prepare(args, argv):
    source, out = Path(args.input).resolve(), Path(args.output).resolve()
    if not source.is_file():
        raise ValueError('输入文件不存在')
    if source.is_relative_to(out):
        raise ValueError('输出目录不能包含输入文件，请使用独立子目录')
    info = probe(source)
    config = {k: v for k, v in vars(args).items() if k not in ('command', 'output', 'allow_model_download')}
    config['input'] = str(source)
    signature = {'version': VERSION, 'source_sha256': digest(source), 'config': config,
                 'transcript_sha256': digest(args.transcript) if args.transcript else None}
    mp = out / 'manifest.json'
    if mp.exists():
        manifest = read(mp)
        if manifest['signature'] != signature:
            raise ValueError('输入、参数或脚本版本已变化；请使用新的 --output 目录，避免覆盖旧报告。')
    else:
        if out.exists() and any(out.iterdir()):
            raise ValueError('输出目录非空且无本工具 manifest；请使用空目录')
        out.mkdir(parents=True, exist_ok=True)
        manifest = {'signature': signature, 'source': str(source), 'media': info, 'stages': {}, 'prepare_argv': argv,
                    'tools': {t: run([t, '-version']).stdout.splitlines()[0] for t in ('ffmpeg', 'ffprobe')}}
        for package in ('faster-whisper', 'Pillow', 'ctranslate2'):
            try:
                manifest['tools'][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                manifest['tools'][package] = None
    (out / 'work').mkdir(exist_ok=True)
    save(mp, manifest)
    def stage(name, fn, products):
        old = manifest['stages'].get(name, {})
        if old.get('status') == 'complete' and all((out / p).is_file() and digest(out / p) == h for p, h in old.get('hashes', {}).items()) and old.get('hashes'):
            return
        manifest['stages'][name] = {'status': 'running'}
        save(mp, manifest)
        try:
            fn()
            paths = products() if callable(products) else products
            manifest['stages'][name] = {'status': 'complete', 'hashes': {p: digest(out / p) for p in paths}}
        except Exception as e:
            manifest['stages'][name] = {'status': 'failed', 'error': str(e)}
        save(mp, manifest)
        handoff(out, manifest, argv)
    if info['audio'] and not args.transcript:
        def audio():
            run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-copyts', '-i', source, '-map', f"0:{info['audio']['index']}",
                 '-vn', '-af', f"asetpts=PTS-({info['origin']})/TB,aresample=16000:async=1:first_pts=0", '-ac', '1', '-c:a', 'pcm_s16le', out / 'work/audio.wav'])
        stage('audio', audio, ['work/audio.wav'])
    def transcript():
        if manifest['stages'].get('audio', {}).get('status') == 'failed':
            raise RuntimeError('音频提取失败，先恢复 audio 阶段；不能转写不完整工作文件。')
        transcribe(out, info, args)
    stage('transcript', transcript, lambda: ['transcript.json', 'transcript.md', 'transcript.srt', 'work/reading-index.json'] + [str(p.relative_to(out)) for p in (out / 'work').glob('read-*.md')])
    if info['video']:
        stage('frames', lambda: extract_frames(source, out, info, args.frame_interval),
              lambda: ['frames/F/index.json'] + sorted({f['path'] for f in read(out / 'frames/F/index.json')}))
        if manifest['stages']['frames']['status'] == 'complete':
            records = [f for p in sorted((out / 'frames').glob('*/index.json')) for f in read(p)]
            # Alignment depends on transcript recovery and supplemental frames;
            # regenerate this inexpensive stage rather than reuse stale matches.
            manifest['stages'].pop('alignment', None)
            stage('alignment', lambda: align(out, records, args.align_window), ['alignment.json', 'frames/index.md'])
    handoff(out, manifest, argv)
    print(json.dumps({'output': str(out), 'status': read(out / 'handoff.json')['status']}, ensure_ascii=False))
    return 2 if any(s['status'] == 'failed' for s in manifest['stages'].values()) else 0


def validate(args):
    out = Path(args.output).resolve()
    errors = []
    m = read(out / 'manifest.json')
    duration = m['media']['duration']
    data = read(out / 'transcript.json') if (out / 'transcript.json').exists() else {'segments': []}
    normalize_segments(data['segments'], duration)
    frames = read(out / 'alignment.json') if (out / 'alignment.json').exists() else []
    ids = {s['id'] for s in data['segments']} | {f['id'] for f in frames}
    for f in frames:
        if not (0 <= f['time'] <= duration + .25) or not (out / f['path']).is_file():
            errors.append(f"无效截图 {f['id']}")
        if any(s not in ids for s in f['segment_ids']):
            errors.append(f"无效转写关联 {f['id']}")
    for name, stage in m['stages'].items():
        if stage['status'] != 'complete':
            errors.append(f'阶段未完成: {name}')
        for p, sha in stage.get('hashes', {}).items():
            if not (out / p).is_file() or digest(out / p) != sha:
                errors.append(f'产物缺失或改变: {p}')
    if not args.prepared_only:
        report = out / 'report.md'
        review = out / 'review.json'
        if not report.is_file() or not review.is_file():
            errors.append('需要 Agent 完成 report.md 和 review.json')
        else:
            text = report.read_text(encoding='utf-8')
            if '{{' in text or 'TODO' in text:
                errors.append('报告仍有模板占位符')
            refs = set(re.findall(r'\b(?:S\d{6}|F\d{6}|X\d+F\d{6})\b', text))
            if (ids and not refs) or refs - ids:
                errors.append('报告缺少有效证据 ID，或引用不存在的证据')
            for target in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text):
                target = target.strip('<>')
                if not (out / target).is_file():
                    errors.append(f'图片不存在: {target}')
            r = read(review)
            if r.get('status') not in ('complete', 'partial'):
                errors.append('review.status 必须是 complete 或 partial')
            if not isinstance(r.get('limitations'), list):
                errors.append('review.limitations 必须是列表')
            if not isinstance(r.get('claims'), list) or (ids and not r['claims']):
                errors.append('review.claims 需要关键结论及证据')
            if not ids and not r.get('limitations'):
                errors.append('没有识别证据时必须说明限制，不能据此断言原媒体没有内容')
            for claim in r.get('claims', []):
                if not claim.get('evidence') or set(claim['evidence']) - ids:
                    errors.append('关键结论引用无效')
            reviewed = set(r.get('reviewed_segment_ids', []))
            if reviewed - ids:
                errors.append('复核片段 ID 无效')
            if r.get('status') == 'complete' and reviewed != {s['id'] for s in data['segments']}:
                errors.append('完整报告必须记录全部转写片段已复核')
            seen = set(r.get('reviewed_frame_ids', []))
            if seen - {f['id'] for f in frames}:
                errors.append('复核截图 ID 无效')
            if frames and r.get('status') == 'complete' and not {f['id'] for f in frames if not f['duplicate_of']} <= seen:
                errors.append('完整视频报告必须复核全部不重复采样截图')
            if r.get('status') == 'partial' and not r.get('limitations'):
                errors.append('部分报告必须说明限制')
    result = {'valid': not errors, 'mode': 'prepared' if args.prepared_only else 'report', 'errors': errors,
              'note': '校验仅检查结构和引用；语义质量需要 Agent 实际核对。'}
    save(out / 'validation.json', result)
    if not args.prepared_only and (out / 'handoff.json').exists():
        h = read(out / 'handoff.json')
        if not errors:
            h['status'] = read(out / 'review.json')['status']
            h['report'] = 'report.md'
            h['next'] = []
        else:
            h['status'] = 'partial' if h.get('failures') else 'awaiting_agent'
        h['validation_errors'] = errors
        save(out / 'handoff.json', h)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check')
    check.add_argument('--input', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--input', required=True)
    prep.add_argument('--output', required=True)
    prep.add_argument('--transcript', help='SRT/VTT/JSON，时间相对原媒体起点')
    prep.add_argument('--frame-interval', type=number, default=30)
    prep.add_argument('--align-window', type=number, default=15)
    prep.add_argument('--model', default='small')
    prep.add_argument('--device', choices=['cpu', 'cuda', 'auto'], default='cpu')
    prep.add_argument('--compute-type', default='int8')
    prep.add_argument('--language', help='例如 zh/en；默认自动检测')
    prep.add_argument('--chunk-seconds', type=int, default=600)
    prep.add_argument('--allow-model-download', action='store_true')
    frames = sub.add_parser('frames', help='向已有任务补充指定范围截图')
    frames.add_argument('--output', required=True)
    frames.add_argument('--start', type=number, required=True)
    frames.add_argument('--end', type=number, required=True)
    frames.add_argument('--interval', type=number, default=2)
    val = sub.add_parser('validate')
    val.add_argument('--output', required=True)
    val.add_argument('--prepared-only', action='store_true')
    args = p.parse_args(argv)
    try:
        if args.command == 'check':
            result = {'python': sys.version.split()[0], 'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe'),
                      'faster_whisper': bool(importlib.util.find_spec('faster_whisper')), 'pillow': bool(importlib.util.find_spec('PIL'))}
            result['media'] = probe(args.input)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['ffmpeg'] else 1
        if args.command == 'prepare':
            if args.frame_interval <= 0 or args.align_window < 0 or args.chunk_seconds <= 0:
                raise ValueError('间隔必须大于零，匹配窗口不能为负')
            return prepare(args, argv)
        if args.command == 'frames':
            out = Path(args.output).resolve()
            m = read(out / 'manifest.json')
            if not m['media']['video'] or not 0 <= args.start < args.end <= m['media']['duration'] or args.interval <= 0:
                raise ValueError('无视频或补帧区间/间隔无效')
            if digest(m['source']) != m['signature']['source_sha256']:
                raise ValueError('源文件已改变，不能补帧')
            prefix = f"X{len(list((out / 'frames').glob('X*'))) + 1}F"
            extract_frames(m['source'], out, m['media'], args.interval, args.start, args.end, prefix)
            paths = [f'frames/{prefix}/index.json'] + sorted({f['path'] for f in read(out / f'frames/{prefix}/index.json')})
            m['stages'][prefix] = {'status': 'complete', 'hashes': {p: digest(out / p) for p in paths}}
            records = [f for path in sorted((out / 'frames').glob('*/index.json')) for f in read(path)]
            align(out, records, m['signature']['config']['align_window'])
            m['stages']['alignment'] = {'status': 'complete', 'hashes': {p: digest(out / p) for p in ['alignment.json', 'frames/index.md']}}
            save(out / 'manifest.json', m)
            handoff(out, m, m['prepare_argv'])
            print(f'补帧完成: {prefix}；重新复核报告和 review.json。')
            return 0
        return validate(args)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        print(f'错误: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
