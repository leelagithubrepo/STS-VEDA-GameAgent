from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.veda_live_metrics import observation_times
from veda.observation import capture_visible_ps5_feed
from veda.runtime_frames import BoundedCaptureSource


class RuntimeFrameTests(unittest.TestCase):
    def test_same_second_captures_never_overwrite_and_cadence_reads_both(self):
        def capture(command, **kwargs):
            Path(command[-1]).write_bytes(b'fake saved image')
        with TemporaryDirectory() as d, patch('veda.observation.subprocess.run', capture):
            first = capture_visible_ps5_feed(Path(d))
            second = capture_visible_ps5_feed(Path(d))
            self.assertNotEqual(first, second)
            self.assertEqual(len(observation_times(Path(d))), 2)

    def test_reusable_source_bounds_storage_and_preserves_archived_identity(self):
        counter = 0
        def capture(directory):
            nonlocal counter
            counter += 1
            path = directory / f'{counter}.dat'
            path.write_bytes(str(counter).encode())
            return path
        source = BoundedCaptureSource(capture=capture, max_frames=2)
        with TemporaryDirectory() as archive:
            first = source.next_frame()
            retained = source.retain(first, Path(archive))
            source.next_frame()
            third = source.next_frame()
            self.assertFalse(first.image_path.exists())
            self.assertEqual(retained.read_bytes(), b'1')
            self.assertTrue(third.ephemeral)
            self.assertNotEqual(first.frame_id, third.frame_id)
        source.close()
        self.assertFalse(third.image_path.exists())

    def test_oversized_capture_and_mutated_archive_are_rejected(self):
        def capture(directory):
            path = directory / 'one.dat'
            path.write_bytes(b'too big')
            return path
        source = BoundedCaptureSource(capture=capture, max_frame_bytes=3)
        with self.assertRaises(ValueError):
            source.next_frame()
        source.close()


if __name__ == '__main__':
    unittest.main()
