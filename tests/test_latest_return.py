import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import collect_all_groups as c


def frame(fingerprint='bottom'):
    return dict(x=0, y=40, width=1000, height=800, image_fingerprint=fingerprint,
                window_id=1, lines=[])


def button(label='回到最新消息', **changes):
    return dict(role='AXButton', labels=[label], x=850, y=620, w=100, h=30, **changes)


class ButtonDetectionTests(unittest.TestCase):
    def detect(self, controls):
        result = SimpleNamespace(returncode=0, stdout=json.dumps({'buttons': controls}))
        with patch.object(c.subprocess, 'run', return_value=result):
            return c.latest_button(frame())

    def test_explicit_labels_and_new_message_count(self):
        for label in ['回到底部', '回到最新', '返回最新消息', '12条新消息', 'Jump to latest messages']:
            self.assertIsNotNone(self.detect([button(label)]), label)

    def test_no_guess_from_arrow_message_text_or_wrong_region(self):
        for item in [button('↓'), button('大家点击回到最新消息'),
                     dict(button(), role='AXStaticText'), dict(button(), x=100),
                     dict(button(), y=800), dict(button(), w=500)]:
            self.assertIsNone(self.detect([item]))

    def test_ambiguous_or_absent_buttons_fall_back(self):
        self.assertIsNone(self.detect([]))
        self.assertIsNone(self.detect([button(), button('新消息')]))

    def test_probe_failure_falls_back_without_input(self):
        for error in [subprocess.TimeoutExpired('probe', 2), OSError('AX unavailable')]:
            with patch.object(c.subprocess, 'run', side_effect=error):
                self.assertIsNone(c.latest_button(frame()))


class ReturnTests(unittest.TestCase):
    def run_return(self, control, pages, jump_result=None, jump_error=None):
        moves = []; jumps = []; archives = []
        class Adapter:
            def __init__(self, *args, **kwargs):
                assert kwargs['record_evidence'] is False
            def preflight(self, w): return w
            def jump_latest(self, w, b):
                jumps.append(b)
                if jump_error: raise jump_error
                return jump_result
            def move(self, w, pixels):
                moves.append(pixels)
                return pages.pop(0)
        with patch.object(c, 'DesktopAdapter', Adapter), patch.object(c, 'latest_button', return_value=control):
            result = c.pin_latest(frame('older'), '测试群')
        return result, moves, jumps

    def test_button_jump_is_verified_at_edge(self):
        result, moves, jumps = self.run_return(button(), [frame(), frame()], frame())
        self.assertEqual(result['image_fingerprint'], 'bottom')
        self.assertEqual(len(jumps), 1)
        self.assertEqual(len(moves), 2)

    def test_no_button_scrolls_until_two_unchanged_observations(self):
        _, moves, jumps = self.run_return(None, [frame('middle'), frame(), frame(), frame()])
        self.assertEqual(len(moves), 4)
        self.assertTrue(all(p < 0 for p in moves))
        self.assertFalse(jumps)

    def test_ineffective_or_partial_jump_falls_back(self):
        _, moves, jumps = self.run_return(button(), [frame(), frame(), frame()], frame('older'))
        self.assertEqual(len(jumps), 1)
        self.assertEqual(len(moves), 3)

    def test_unknown_click_result_stops_without_replay(self):
        with self.assertRaises(TimeoutError):
            self.run_return(button(), [], jump_error=TimeoutError('unknown click result'))

    def test_images_with_same_ocr_are_not_the_same_viewport(self):
        self.assertFalse(c.same_viewport(frame('image-a'), frame('image-b')))

    def test_return_does_not_archive_collection_frames(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(c, 'ROOT', Path(directory)):
            adapter = c.DesktopAdapter('restore-test', '测试群', record_evidence=False)
            adapter.archive(frame(), 'motion')
            self.assertEqual(adapter.frames, 0)
            self.assertEqual(list(adapter.output.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
