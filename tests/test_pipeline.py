import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'video-audio-summary/scripts/media_pipeline.py'
spec = importlib.util.spec_from_file_location('pipeline', SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='media-tests-')
        cls.root = Path(cls.temp.name)
        cls.video = cls.root / '课堂 $(literal) 空格.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=4:duration=4',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-filter_complex', '[1:a]asetpts=PTS+2/TB[a]',
                        '-map', '0:v', '-map', '[a]', '-c:v', 'ffv1', '-c:a', 'pcm_s16le', str(cls.video)], check=True)
        cls.srt = cls.root / '字幕.srt'
        cls.srt.write_text('1\n00:00:02,000 --> 00:00:03,500\n演示内容\n', encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def cli(self, *args, expected=0):
        p = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)
        self.assertEqual(p.returncode, expected, p.stdout + p.stderr)
        return p

    def test_video_report_resume_and_supplement(self):
        out = self.root / 'video-out'
        args = ('prepare', '--input', self.video, '--output', out, '--transcript', self.srt, '--frame-interval', 1, '--align-window', .2)
        self.cli(*args)
        rows = pipeline.read(out / 'alignment.json')
        self.assertEqual([r['time'] for r in rows], [0, 1, 2, 3])
        self.assertEqual(rows[0]['segment_ids'], [])
        self.assertEqual(rows[2]['segment_ids'], ['S000001'])
        self.cli('validate', '--output', out, '--prepared-only')
        self.cli('validate', '--output', out, expected=1)
        mtime = (out / 'frames/F/frame-000001.png').stat().st_mtime_ns
        self.cli(*args)
        self.assertEqual(mtime, (out / 'frames/F/frame-000001.png').stat().st_mtime_ns)
        self.cli('frames', '--output', out, '--start', 1, '--end', 2, '--interval', .25)
        rows = pipeline.read(out / 'alignment.json')
        self.assertEqual(len(rows), 8)
        (out / 'report.md').write_text('# 测试报告\n\n00:00:02 [S000001](transcript.md#S000001) 演示内容。\n\n![图 F000003](frames/F/frame-000003.png)\n', encoding='utf-8')
        pipeline.save(out / 'review.json', {'status': 'complete', 'reviewed_segment_ids': ['S000001'],
                      'reviewed_frame_ids': [r['id'] for r in rows], 'claims': [{'claim': '演示内容', 'evidence': ['S000001']}], 'limitations': ['合成测试']})
        self.cli('validate', '--output', out)
        (out / 'frames/F/frame-000003.png').unlink()
        self.cli('validate', '--output', out, expected=1)
        self.cli(*args)
        self.assertTrue((out / 'frames/F/frame-000003.png').exists())
        self.cli(*args, '--frame-interval', 2, expected=1)

    def test_delayed_audio_and_missing_model_handoff(self):
        out = self.root / 'offset-out'
        self.cli('prepare', '--input', self.video, '--output', out, '--model', '/nonexistent-model-for-test', expected=2)
        with wave.open(str(out / 'work/audio.wav')) as w:
            import array
            samples = array.array('h', w.readframes(w.getnframes()))
            self.assertTrue(all(v == 0 for v in samples[:30000]))
            self.assertGreater(max(abs(v) for v in samples[33000:]), 100)
            self.assertAlmostEqual(len(samples) / w.getframerate(), 4, places=1)
        h = pipeline.read(out / 'handoff.json')
        self.assertIn('transcript', h['failures'])
        self.assertTrue((out / 'frames/F/index.json').exists())
        self.assertFalse((out / 'report.md').exists())

    def test_subtitle_formats_and_rejection(self):
        path = self.root / 'sample.vtt'
        path.write_text('WEBVTT\n\n00:01.000 --> 00:02.000 align:start\nHello\nworld\n', encoding='utf-8')
        data = pipeline.import_transcript(path, 4)
        self.assertEqual(data['segments'][0]['text'], 'Hello\nworld')
        path = self.root / 'sample.json'
        pipeline.save(path, {'segments': [{'start': 2, 'end': 1, 'text': 'invalid'}]})
        with self.assertRaises(ValueError):
            pipeline.import_transcript(path, 4)
        for value in ('nan', 'inf', '-inf'):
            with self.assertRaises(ValueError):
                pipeline.number(value)

    def test_external_diagnostics_with_invalid_utf8(self):
        result = pipeline.run([sys.executable, '-c', "import sys; sys.stderr.buffer.write(bytes([0xe4, 0x41])); print('pts_time:30.1')"])
        self.assertIn('pts_time:30.1', result.stdout)
        self.assertIn('\ufffd', result.stderr)

    def test_transcription_recovery_realigns_existing_frames(self):
        out = self.root / 'recover-out'
        args = ['prepare', '--input', str(self.video), '--output', str(out), '--frame-interval', '1', '--align-window', '.2']
        with patch.object(pipeline, 'transcribe', side_effect=RuntimeError('temporary ASR failure')):
            self.assertEqual(pipeline.main(args), 2)
        self.assertTrue(all(not f['segment_ids'] for f in pipeline.read(out / 'alignment.json')))
        def recovered(out, info, args):
            pipeline.export_transcript(out, {'backend': 'test', 'duration': info['duration'],
                'segments': [{'id': 'S000001', 'start': 2, 'end': 3.5, 'text': 'recovered speech'}]})
        with patch.object(pipeline, 'transcribe', side_effect=recovered):
            self.assertEqual(pipeline.main(args), 0)
        self.assertEqual(pipeline.read(out / 'alignment.json')[2]['segment_ids'], ['S000001'])

    def test_static_silent_video_dedup(self):
        path = self.root / 'silent.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:size=64x64:rate=2:duration=3', '-c:v', 'ffv1', str(path)], check=True)
        out = self.root / 'silent-out'
        self.cli('prepare', '--input', path, '--output', out, '--frame-interval', 1)
        rows = pipeline.read(out / 'alignment.json')
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(set(r['path'] for r in rows)), 1)
        self.assertEqual(pipeline.read(out / 'transcript.json')['reason'], 'no_audio_stream')
        self.cli('validate', '--output', out, '--prepared-only')

    def test_audio_only_import(self):
        path = self.root / 'audio.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=4', str(path)], check=True)
        out = self.root / 'audio-out'
        self.cli('prepare', '--input', path, '--output', out, '--transcript', self.srt)
        self.assertFalse((out / 'frames').exists())
        self.cli('validate', '--output', out, '--prepared-only')

    def test_vfr_and_nonzero_origin(self):
        path = self.root / 'vfr.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=4:duration=4',
                        '-vf', "select='not(eq(mod(n,3),1))',setpts=PTS+5/TB", '-fps_mode', 'vfr', '-c:v', 'ffv1', str(path)], check=True)
        info = pipeline.probe(path)
        self.assertEqual(info['origin'], 5)
        self.assertAlmostEqual(info['duration'], 4)
        out = self.root / 'vfr-out'
        self.cli('prepare', '--input', path, '--output', out, '--frame-interval', 1)
        rows = pipeline.read(out / 'alignment.json')
        self.assertEqual([r['time'] for r in rows], [0, 1.25, 2, 3])
        self.assertEqual([r['source_pts'] for r in rows], [5, 6.25, 7, 8])


if __name__ == '__main__':
    unittest.main()
