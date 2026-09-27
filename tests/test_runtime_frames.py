from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.veda_live_metrics import observation_times
from veda.observation import capture_visible_ps5_feed
from veda.runtime_frames import (BoundedCaptureSource, CaptureFailure, RuntimeFrame,
                                 snapshot_frame, retain_snapshot)


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

    def test_pending_snapshot_survives_cache_eviction_and_source_cleanup(self):
        count = 0
        def capture(directory):
            nonlocal count
            count += 1
            path = directory / f'{count}.dat'
            path.write_bytes(f'frame {count}'.encode())
            return path
        source = BoundedCaptureSource(capture=capture, max_frames=2)
        first = source.next_frame()
        pending = snapshot_frame(first)
        source.next_frame()
        source.next_frame()
        source.close()
        self.assertFalse(first.image_path.exists())
        with TemporaryDirectory() as archive:
            receipt = retain_snapshot(pending, Path(archive))
            self.assertEqual(Path(receipt['image_path']).read_bytes(), b'frame 1')
            self.assertEqual(receipt['sha256'], first.sha256)
            self.assertTrue(receipt['durable'])
            self.assertFalse(receipt['ephemeral'])
            self.assertEqual(receipt['original_image_path'], str(first.image_path))
            self.assertEqual(retain_snapshot(pending, Path(archive)), receipt)
            self.assertEqual(len(list(Path(archive).iterdir())), 1)
            Path(receipt['image_path']).write_bytes(b'corrupted archive')
            with self.assertRaisesRegex(ValueError, 'existing retained evidence'):
                retain_snapshot(pending, Path(archive))
            self.assertEqual(Path(receipt['image_path']).read_bytes(), b'corrupted archive')
            self.assertEqual(len(list(Path(archive).iterdir())), 1, 'no staging-file leak')

    def test_snapshot_rejects_changed_source_and_invalid_size_budget(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'source.dat'
            path.write_bytes(b'frame')
            frame = RuntimeFrame.from_path(path)
            with self.assertRaisesRegex(ValueError, 'byte budget'):
                snapshot_frame(frame, max_bytes=2)
            for value in (False, -2, 0, 1.5):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    snapshot_frame(frame, max_bytes=value)
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'changed before archival'):
                snapshot_frame(frame)

    def test_default_provider_runtime_error_is_a_structured_capture_failure(self):
        def failing_capture(directory):
            raise RuntimeError('synthetic capture unavailable')
        source = BoundedCaptureSource(capture=failing_capture)
        try:
            with self.assertRaisesRegex(CaptureFailure, 'synthetic capture unavailable'):
                source.next_frame()
        finally:
            source.close()

    def test_slow_capture_does_not_restart_the_freshness_clock(self):
        tick = [100]
        def capture(directory):
            tick[0] += 10_000_000_000
            path = directory / 'slow.dat'
            path.write_bytes(b'synthetic captured bytes')
            return path
        source = BoundedCaptureSource(capture=capture, clock=lambda: tick[0])
        try:
            frame = source.next_frame()
            self.assertEqual(100, frame.acquired_ns)
            self.assertGreater(tick[0] - frame.acquired_ns, 5_000_000_000)
        finally:
            source.close()


if __name__ == '__main__':
    unittest.main()
