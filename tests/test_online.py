import argparse
import copy
import http.cookiejar
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'video-audio-summary/scripts'
sys.path.insert(0, str(SCRIPTS))
import online_video as online


class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='online-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / 'fixture.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=size=160x90:rate=2:duration=3', '-f', 'lavfi', '-i',
                        'sine=duration=3', '-c:v', 'ffv1', '-c:a', 'pcm_s16le', str(self.video)], check=True)
        self.info = {'id': 'jNQXAC9IVRw', 'extractor_key': 'Youtube',
                     'webpage_url': 'https://www.youtube.com/watch?v=jNQXAC9IVRw',
                     'title': '测试', 'uploader': 'fixture', 'duration': 3,
                     'subtitles': {'zh': [{'ext': 'srt', 'data': '1\n00:00:00,000 --> 00:00:02,500\n课堂演示\n'}]}}
        self.calls = 0
        self.opts = None
        self.args = argparse.Namespace(url=self.info['webpage_url'], output=str(self.root / '结果 $(literal)'),
                                       max_height=360, subtitle_langs='zh,en', prefer_asr=False, cookies=None)

    def factory(self, opts):
        owner = self
        owner.opts = opts
        class FakeYDL:
            params = dict(opts)
            cookiejar = http.cookiejar.CookieJar()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download):
                owner.calls += 1
                return copy.deepcopy(owner.info)
            def add_postprocessor_hook(self, hook): pass
            def process_ie_result(self, info, download):
                target = Path(opts['outtmpl'].replace('%(ext)s', 'mkv'))
                shutil.copyfile(owner.video, target)
                return {**info, 'filepath': str(target)}
            def prepare_filename(self, info): return info['filepath']
        return FakeYDL()

    @property
    def cache(self): return Path(self.args.output + '.source')

    def test_url_identity_and_single_part(self):
        self.assertEqual(online.canonical_url('https://youtu.be/jNQXAC9IVRw?t=3&list=xxx'), self.args.url)
        self.assertEqual(online.canonical_url('https://m.youtube.com/shorts/jNQXAC9IVRw'), self.args.url)
        self.assertEqual(online.canonical_url('https://www.bilibili.com/video/BV12N4y1M7rh/?p=2&share_source=x'),
                         'https://www.bilibili.com/video/BV12N4y1M7rh?p=2')
        for url in ('https://youtube.com.evil.test/watch?v=jNQXAC9IVRw', 'https://youtube.com/playlist?list=x',
                    'https://user:secret@youtu.be/jNQXAC9IVRw', 'file:///tmp/a', 'https://live.bilibili.com/123'):
            with self.assertRaises(ValueError): online.canonical_url(url)

    def test_full_pipeline_and_cached_resume(self):
        rest = ['--frame-interval', '1']
        self.assertEqual(online.execute(self.args, rest, self.factory), 0)
        out = Path(self.args.output)
        self.assertEqual(online.pipeline.read(out / 'source.json')['caption']['kind'], 'platform_subtitle')
        self.assertEqual(online.pipeline.read(out / 'transcript.json')['segments'][0]['text'], '课堂演示')
        self.assertEqual(online.pipeline.main(['validate', '--output', str(out), '--prepared-only']), 0)
        self.assertEqual(online.execute(self.args, rest, self.factory), 0)
        self.assertEqual(self.calls, 1)
        self.assertTrue(self.opts['noplaylist'])
        self.assertFalse(self.opts['cachedir'])
        self.assertEqual(self.opts['remote_components'], set())
        self.args.max_height = 720
        with self.assertRaises(ValueError): online.execute(self.args, rest, self.factory)

    def test_subtitle_failure_falls_back_to_asr(self):
        self.info['subtitles']['zh'][0]['data'] = 'not a caption'
        with patch.object(online.pipeline, 'main', return_value=2) as main:
            self.assertEqual(online.execute(self.args, [], self.factory), 2)
            self.assertNotIn('--transcript', main.call_args.args[0])
        state = online.pipeline.read(self.cache / 'download.json')
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(state['caption']['kind'], 'local_asr')
        self.assertIn('字幕读取失败', state['caption']['reason'])

    def test_forced_asr_and_explicit_subtitle_precedence(self):
        self.args.prefer_asr = True
        with patch.object(online.pipeline, 'main', return_value=2) as main:
            online.execute(self.args, [], self.factory)
            self.assertNotIn('--transcript', main.call_args.args[0])
        self.args.output = str(self.root / 'explicit')
        self.args.prefer_asr = False
        explicit = str(self.root / 'my.srt')
        with patch.object(online.pipeline, 'main', return_value=2) as main:
            online.execute(self.args, ['--transcript', explicit], self.factory)
            command = main.call_args.args[0]
            self.assertEqual(command.count('--transcript'), 1)
            self.assertEqual(command[-1], explicit)

    def test_failure_handoff_and_retry(self):
        def fail(opts): raise RuntimeError('HTTP 403 https://cdn.example/a?token=secret')
        self.assertEqual(online.execute(self.args, [], fail), 2)
        handoff = online.pipeline.read(self.cache / 'handoff.json')
        self.assertEqual(handoff['status'], 'blocked_download')
        self.assertNotIn('secret', handoff['error'])
        self.assertEqual(online.execute(self.args, [], self.factory), 0)
        self.assertNotIn('error', online.pipeline.read(self.cache / 'download.json'))
        self.assertEqual(online.pipeline.read(self.cache / 'handoff.json')['status'], 'download_complete')

    def test_playlist_live_and_unrelated_redirect_refused(self):
        for fields in ({'_type': 'playlist', 'entries': []}, {'is_live': True},
                       {'has_drm': True}, {'extractor_key': 'Generic'}):
            with self.assertRaises(ValueError): online.validate_info({**self.info, **fields})

    def test_caption_selection_and_rolling_cleanup(self):
        info = {'subtitles': {'danmaku': [{'ext': 'xml'}], 'en': [{'ext': 'vtt'}]},
                'automatic_captions': {'zh': [{'ext': 'json3'}]}}
        self.assertEqual(online.select_caption(info, ['zh', 'en'])['language'], 'en')
        self.assertIsNone(online.select_caption(info, ['fr']))
        info = {'subtitles': {'ai-zh': [{'ext': 'srt'}]}}
        self.assertEqual(online.select_caption(info, ['zh'])['kind'], 'automatic_caption')
        raw = self.root / 'caption.json3'
        online.pipeline.save(raw, {'events': [
            {'tStartMs': 0, 'dDurationMs': 1500, 'segs': [{'utf8': 'Hello'}]},
            {'tStartMs': 500, 'dDurationMs': 1500, 'segs': [{'utf8': 'Hello world'}]},
            {'tStartMs': 2100, 'dDurationMs': 900, 'segs': [{'utf8': 'Hello'}]}]})
        self.assertEqual([r['text'] for r in online.normalize_caption(raw, 3)], ['Hello', 'world', 'Hello'])
        raw = self.root / 'caption.vtt'
        raw.write_text('WEBVTT\n\n00:00.000 --> 00:02.000\n<c>Hello &amp; world</c>\n', encoding='utf8')
        self.assertEqual(online.normalize_caption(raw, 3)[0]['text'], 'Hello & world')

    def test_tampered_cache_and_existing_output_refused(self):
        self.assertEqual(online.execute(self.args, [], self.factory), 0)
        (self.cache / 'captions.json').write_text('{}')
        with self.assertRaises(ValueError): online.execute(self.args, [], self.factory)
        self.args.output = str(self.root / 'occupied')
        Path(self.args.output).mkdir()
        (Path(self.args.output) / 'keep.txt').write_text('keep')
        with self.assertRaises(ValueError): online.execute(self.args, [], self.factory)
        self.assertEqual((Path(self.args.output) / 'keep.txt').read_text(), 'keep')

    def test_cookies_read_only_and_not_persisted(self):
        cookies = self.root / 'cookies.txt'
        original = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSESSION\tprivate-cookie-value\n'
        cookies.write_text(original)
        self.args.cookies = str(cookies)
        self.assertEqual(online.execute(self.args, [], self.factory), 0)
        self.assertEqual(cookies.read_text(), original)
        for path in [*self.cache.glob('*.json'), *Path(self.args.output).glob('*.json')]:
            self.assertNotIn('private-cookie-value', path.read_text())

    def test_no_input_or_output_override(self):
        for rest in (['--input', 'x'], ['--output', 'x'], ['--frame-interval=1']):
            with self.assertRaises(ValueError): online.execute(self.args, rest, self.factory)
        self.assertEqual(self.calls, 0)


if __name__ == '__main__':
    unittest.main()
