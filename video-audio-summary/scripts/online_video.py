#!/usr/bin/env python3
"""Fetch one YouTube/Bilibili video, then run the local evidence pipeline."""
import argparse
import copy
import html
import http.cookiejar
import importlib.metadata
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import media_pipeline as pipeline


def canonical_url(value):
    p = urlsplit(value.strip())
    if p.scheme not in ('http', 'https') or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('请输入 YouTube/Bilibili 单个视频的 HTTP(S) 链接')
    host, path, q = (p.hostname or '').lower(), p.path.rstrip('/'), parse_qs(p.query)
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'www.youtu.be'):
        vid = path[1:] if host.endswith('youtu.be') else (q.get('v', [''])[0] if path == '/watch' else '')
        if path.startswith(('/shorts/', '/embed/')):
            vid = path.split('/')[-1]
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
            raise ValueError('只支持单个 YouTube 视频；频道、播放列表和直播入口不支持')
        return 'https://www.youtube.com/watch?' + urlencode({'v': vid})
    if host in ('bilibili.com', 'www.bilibili.com', 'm.bilibili.com'):
        match = re.fullmatch(r'/video/(BV[A-Za-z0-9]+|av\d+)', path)
        part = q.get('p', ['1'])[0]
        if not match or not part.isdigit() or int(part) < 1:
            raise ValueError('只支持 Bilibili /video/BV… 或 /video/av… 视频链接')
        return f'https://www.bilibili.com/video/{match[1]}?p={int(part)}'
    if host in ('b23.tv', 'www.b23.tv') and re.fullmatch(r'/[A-Za-z0-9]+', path):
        return 'https://b23.tv' + path
    raise ValueError('仅支持 YouTube、Bilibili 和 b23.tv 视频链接')


def safe_error(error):
    # Signed media URLs and account tokens do not belong in persistent logs.
    return re.sub(r'https?://\S+', '[远程地址]', str(error))[-2000:]


class Logger:
    def __init__(self):
        self.warnings = []

    def debug(self, message):
        pass

    def warning(self, message):
        self.warnings.append(safe_error(message))

    def error(self, message):
        self.warning(message)


def options(cache, height, logger):
    runtimes = {name: {'path': shutil.which(name)} for name in ('deno', 'node') if shutil.which(name)}
    return {'noplaylist': True, 'playlist_items': '1', 'cachedir': False,
            'socket_timeout': 25, 'retries': 3, 'fragment_retries': 3,
            'quiet': True, 'noprogress': True, 'logger': logger,
            'js_runtimes': runtimes, 'remote_components': set(),
            'outtmpl': str(cache / 'media.%(ext)s'), 'merge_output_format': 'mkv',
            'format': f'bestvideo[height<={height}]+bestaudio/best[height<={height}]',
            'writesubtitles': True, 'writeautomaticsub': True,
            'subtitleslangs': ['-all'], 'skip_download': False}


def select_caption(info, languages):
    for kind, key in (('platform_subtitle', 'subtitles'), ('automatic_caption', 'automatic_captions')):
        tracks = info.get(key) or {}
        for preferred in languages:
            # Exact language first; exclude chat, danmaku, and unsupported formats.
            keys = sorted(tracks, key=lambda k: (k != preferred, k))
            for lang in keys:
                base = lang.removeprefix('ai-').lower()
                if base != preferred.lower() and not base.startswith(preferred.lower() + '-'):
                    continue
                if lang in ('live_chat', 'danmaku'):
                    continue
                for ext in ('json3', 'vtt', 'srt'):
                    for track in tracks[lang]:
                        if track.get('ext') == ext:
                            return {**track, 'language': lang,
                                    'kind': 'automatic_caption' if lang.startswith('ai-') else kind}
    return None


def normalize_caption(path, duration):
    if path.suffix == '.json3':
        rows = []
        for event in pipeline.read(path).get('events', []):
            text = ''.join(s.get('utf8', '') for s in event.get('segs', []))
            if text.strip() and event.get('dDurationMs', 0) > 0:
                start = event['tStartMs'] / 1000
                rows.append({'start': start, 'end': start + event['dDurationMs'] / 1000, 'text': text})
    else:
        # Parse before clamping so malformed timestamps are not silently accepted.
        rows = pipeline.import_transcript(path, duration + 30)['segments']
    clean = []
    for row in sorted(rows, key=lambda r: (r['start'], r['end'])):
        start, end = pipeline.number(row['start']), pipeline.number(row['end'])
        if start < 0 or end <= start or end > duration + 30:
            raise ValueError('平台字幕时间轴异常，改用 ASR')
        if start >= duration:
            continue
        text = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', row['text']))).strip()
        if not text:
            continue
        # Rolling captions repeat the previous line while its cue is still active.
        previous = clean[-1] if clean else None
        if previous and start < previous['end']:
            if text == previous['text']:
                previous['end'] = max(previous['end'], min(end, duration))
                continue
            if text.startswith(previous['text'] + ' '):
                text = text[len(previous['text']):].strip()
        clean.append({'start': start, 'end': min(end, duration), 'text': text})
    result = pipeline.normalize_segments(clean, duration)
    if not result:
        raise ValueError('平台字幕为空，改用 ASR')
    return result


def validate_info(info):
    if not info or info.get('_type') in ('playlist', 'multi_video') or 'entries' in info:
        raise ValueError('链接返回合集或多个片段，请提供具体视频/分 P 链接')
    extractor = info.get('extractor_key', '').lower()
    if extractor not in ('youtube', 'bilibilibv', 'bilibili'):
        raise ValueError('短链接未指向支持的 YouTube/Bilibili 视频')
    if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming', 'post_live'):
        raise ValueError('直播/尚未完成处理的视频暂不支持；请使用已结束并可下载的回放')
    if info.get('has_drm'):
        raise ValueError('该视频受 DRM 保护，无法通过此流程获取')


def fetch(args, cache, state, factory=None):
    if factory is None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as e:
            raise RuntimeError('缺少 yt-dlp；安装 scripts/requirements-download.txt') from e
        factory = YoutubeDL
    logger = Logger()
    with factory(options(cache, args.max_height, logger)) as ydl:
        if args.cookies:
            jar = http.cookiejar.MozillaCookieJar(str(Path(args.cookies).resolve()))
            try:
                jar.load(ignore_discard=True, ignore_expires=False)
            except (OSError, ValueError) as e:
                # CookieJar parse errors may include the offending secret line.
                raise RuntimeError('无法读取 cookies；请检查文件权限及 Netscape 格式') from None
            for cookie in jar:
                ydl.cookiejar.set_cookie(cookie)
        info = ydl.extract_info(state['request']['url'], download=False)
        validate_info(info)
        state['source'] = {k: info.get(k) for k in ('id', 'title', 'uploader', 'duration', 'language', 'extractor_key')}
        state['source']['url'] = canonical_url(info.get('webpage_url') or state['request']['url'])
        state['caption'] = {'kind': 'local_asr', 'reason': '用户选择 ASR' if args.prefer_asr else '没有匹配语言的可用字幕'}
        pipeline.save(cache / 'download.json', state)
        # Fetch media separately so a subtitle error cannot abort the video download.
        ydl.params.update(writesubtitles=False, writeautomaticsub=False)
        captured = []
        ydl.add_postprocessor_hook(lambda event: captured.append(event.get('info_dict', {}).get('filepath')))
        downloaded = ydl.process_ie_result(copy.deepcopy(info), download=True)
        candidates = [downloaded.get('filepath'), *reversed(captured), ydl.prepare_filename(downloaded)]
        source = next((Path(p) for p in candidates if p and Path(p).is_file()), None)
        if not source or source.parent.resolve() != cache.resolve() or source.name.startswith('.'):
            raise RuntimeError('下载后未找到完整媒体文件；保留缓存以便重试')
        media = pipeline.probe(source)
        if not media['video']:
            raise RuntimeError('下载结果没有视频轨道，无法生成图文总结')
        state['media'] = source.name
        state['hashes'] = {source.name: pipeline.digest(source)}
        caption = None if args.prefer_asr else select_caption(info, [s.strip() for s in args.subtitle_langs.split(',') if s.strip()])
        if caption:
            try:
                raw = cache / ('caption.' + caption['ext'])
                if caption.get('data') is not None:
                    data = caption['data'].encode('utf-8')
                else:
                    from yt_dlp.networking import Request
                    with ydl.urlopen(Request(caption['url'], headers={**(info.get('http_headers') or {}), **(caption.get('http_headers') or {})})) as response:
                        data = response.read(16 * 1024 * 1024 + 1)
                if len(data) > 16 * 1024 * 1024:
                    raise ValueError('字幕超过 16 MiB 上限')
                raw.write_bytes(data)
                segments = normalize_caption(raw, media['duration'])
                pipeline.save(cache / 'captions.json', {'segments': segments})
                state['caption'] = {'kind': caption['kind'], 'language': caption['language'], 'raw': raw.name,
                                    'normalized': 'captions.json', 'segments': len(segments),
                                    'first_start': segments[0]['start'], 'last_end': max(s['end'] for s in segments)}
                state['hashes'].update({p: pipeline.digest(cache / p) for p in (raw.name, 'captions.json')})
            except Exception as e:
                state['caption']['reason'] = '平台字幕读取失败，改用 ASR：' + safe_error(e)
        state.pop('error', None)
        state.update(status='complete', warnings=logger.warnings)
        try:
            state['yt_dlp_version'] = importlib.metadata.version('yt-dlp')
        except importlib.metadata.PackageNotFoundError:
            state['yt_dlp_version'] = None
        pipeline.save(cache / 'download.json', state)
    return state


def execute(args, pipeline_args, factory=None):
    url = canonical_url(args.url)
    if args.max_height <= 0 or not args.subtitle_langs.strip(', '):
        raise ValueError('max-height 必须为正数，subtitle-langs 不能为空')
    # Local parameters are forwarded as an argv list, never through a shell.
    allowed_values = {'--frame-interval', '--align-window', '--model', '--device', '--compute-type', '--language', '--chunk-seconds', '--transcript'}
    i = 0
    while i < len(pipeline_args):
        token = pipeline_args[i]
        if token in allowed_values and i + 1 < len(pipeline_args):
            i += 2
        elif token == '--allow-model-download':
            i += 1
        else:
            raise ValueError(f'不支持的流水线参数: {token}；使用 --参数 值 格式')
    out = Path(args.output).resolve()
    cache = out.with_name(out.name + '.source')
    request = {'url': url, 'max_height': args.max_height, 'subtitle_langs': args.subtitle_langs,
               'prefer_asr': args.prefer_asr}
    state_file = cache / 'download.json'
    if state_file.exists():
        state = pipeline.read(state_file)
        if state.get('request') != request:
            raise ValueError('链接/下载参数改变，请使用新输出目录')
    else:
        if (cache.exists() and any(cache.iterdir())) or (out.exists() and any(out.iterdir())):
            raise ValueError('输出或下载缓存目录已被其他任务使用，请指定新目录')
        state = {'request': request, 'status': 'pending'}
    cache.mkdir(parents=True, exist_ok=True)
    if out.exists() and any(out.iterdir()):
        manifest = out / 'manifest.json'
        if not manifest.exists() or Path(pipeline.read(manifest)['source']).parent != cache:
            raise ValueError('输出目录不属于当前下载任务，请使用新目录')
    valid = state.get('status') == 'complete' and state.get('hashes') and all(
        (cache / name).is_file() and pipeline.digest(cache / name) == sha for name, sha in state['hashes'].items())
    if not valid:
        if state.get('status') == 'complete':
            raise ValueError('下载缓存缺失或被修改，请使用新输出目录重新获取')
        state['status'] = 'downloading'
        pipeline.save(state_file, state)
        try:
            state = fetch(args, cache, state, factory)
        except Exception as e:
            state.update(status='failed', error=safe_error(e))
            pipeline.save(state_file, state)
            pipeline.save(cache / 'handoff.json', {
                'status': 'blocked_download', 'error': safe_error(e),
                'next': ['检查网络及原视频是否可播放；必要时更新 yt-dlp 后用原命令重试。',
                         '如平台要求登录，仅在用户提供或授权后使用 --cookies；不自动读取浏览器账户。',
                         '仍无法获取时，请用户提供可访问链接、已有视频或带时间轴字幕；不可伪造报告。']})
            print(f'下载失败，交接信息：{cache / "handoff.json"}', file=sys.stderr)
            return 2
    local = ['prepare', '--input', str(cache / state['media']), '--output', str(out)]
    if '--transcript' not in pipeline_args and state['caption'].get('normalized'):
        local += ['--transcript', str(cache / state['caption']['normalized'])]
    if (cache / 'handoff.json').exists():
        pipeline.save(cache / 'handoff.json', {'status': 'download_complete', 'next': ['继续本地预处理和 Agent 报告复核。']})
    code = pipeline.main(local + pipeline_args)
    if code in (0, 2) and (out / 'manifest.json').exists():
        origin = {**state['source'], 'download_request': request, 'caption': state['caption'],
                  'warnings': state.get('warnings', []), 'cache': str(cache), 'yt_dlp_version': state.get('yt_dlp_version')}
        if '--transcript' in pipeline_args:
            origin['caption'] = {'kind': 'user_supplied', 'note': '本次使用显式传入的 --transcript'}
        pipeline.save(out / 'source.json', origin)
        tasks = out / 'agent_tasks.md'
        if tasks.exists():
            with tasks.open('a', encoding='utf-8') as f:
                f.write('\n## 在线来源\n阅读 source.json，报告注明视频链接、作者、字幕来源及限制。平台自动字幕须注明可能误识别；检查开头、结尾和长空白，字幕不代表已完整复核音频。\n')
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-height', type=int, default=1080)
    parser.add_argument('--subtitle-langs', default='zh,en', help='字幕语言优先级，逗号分隔；作者字幕优先')
    parser.add_argument('--prefer-asr', action='store_true', help='忽略平台字幕，使用本地 ASR')
    parser.add_argument('--cookies', help='用户明确提供的 Netscape cookies 文件；只读、不复制')
    parser.add_argument('pipeline_args', nargs=argparse.REMAINDER, help='-- 后转发本地流水线参数')
    args = parser.parse_args(argv)
    rest = args.pipeline_args[1:] if args.pipeline_args[:1] == ['--'] else args.pipeline_args
    try:
        return execute(args, rest)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as e:
        print('错误: ' + safe_error(e), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
