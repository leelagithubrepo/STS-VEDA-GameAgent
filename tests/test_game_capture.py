"""Passive capture adapter tests; every OS/window/capture call is mocked."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from veda import game_capture


def window(ident=41, **values):
    return dict({'id':ident,'owner':'QuickTime Player','title':'Movie Recording',
                 'width':320.0,'height':180.0,'on_screen':True},**values)


def png(width=320,height=180):
    def chunk(kind,body):
        return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
    raw=(b'\0'+b'\x20\x20\x20'*width)*height
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))


class GameWindowSelectionTests(unittest.TestCase):
    def test_exact_title_owner_and_optional_id_disambiguate(self):
        rows=[window(),window(42),window(43,title='Camera')]
        with self.assertRaisesRegex(ValueError,'exactly one'):game_capture.select_game_window(rows)
        self.assertEqual(game_capture.select_game_window(rows,window_id=42)['id'],42)
        for wrong in ('movie recording','Movie Recording ',None,''):
            with self.assertRaises(ValueError):game_capture.select_game_window([window(title=wrong)])
        with self.assertRaises(ValueError):game_capture.select_game_window([window(owner='Other App')])

    def test_geometry_and_id_must_be_finite_and_well_typed(self):
        for key in ('width','height'):
            for value in (float('nan'),float('inf'),-float('inf'),False,None,'320',0,-1):
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    game_capture.select_game_window([window(**{key:value})])
        for value in (True,False,0,-1,'41',1.5):
            with self.subTest(value=value),self.assertRaises(ValueError):
                game_capture.select_game_window([window()],window_id=value)
        for rows in ({},None,[window()]*65):
            with self.assertRaises(ValueError):game_capture.select_game_window(rows)

    def test_returned_identity_is_a_defensive_copy_and_background_is_not_foreground(self):
        row=window(on_screen=False)
        selected=game_capture.select_game_window([row])
        row['width']=999
        self.assertEqual(selected['width'],320)
        self.assertFalse(selected['on_screen'])


class GameCaptureTests(unittest.TestCase):
    def fake_capture(self,command,**kwargs):
        self.assertEqual(command[:6],['/usr/sbin/screencapture','-x','-o','-l','41','-t'])
        self.assertEqual(command[6],'png')
        self.assertEqual(kwargs,{'check':True,'capture_output':True,'timeout':15})
        Path(command[-1]).write_bytes(png())
        return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')

    def test_exact_window_capture_hash_and_receipt_do_not_claim_pixels_verified(self):
        with TemporaryDirectory() as directory, patch.object(game_capture,'list_game_windows',side_effect=[[window()],[window()]]), \
             patch.object(game_capture.subprocess,'run',side_effect=self.fake_capture) as run:
            path=game_capture.capture_game_window(directory)
            receipt=json.loads(path.with_suffix('.capture.json').read_text())
            self.assertEqual(receipt['image_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(receipt['image_path'],str(path.resolve()))
            self.assertEqual(receipt['dimensions'],[320,180])
            self.assertEqual(receipt['window'],window())
            self.assertLessEqual(receipt['capture_requested_at'],receipt['capture_completed_at'])
            self.assertFalse(receipt['pixel_content_verified'])
            self.assertFalse(receipt['controller_input_sent'])
            self.assertEqual(run.call_count,1)

    def test_initial_ambiguity_or_bad_id_never_invokes_capture(self):
        with TemporaryDirectory() as directory, patch.object(game_capture,'list_game_windows',return_value=[window(),window(42)]), \
             patch.object(game_capture.subprocess,'run') as run:
            for selected in (None,99,False,-1):
                with self.subTest(selected=selected),self.assertRaises(ValueError):
                    game_capture.capture_game_window(directory,window_id=selected)
            run.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_window_disappearance_replacement_geometry_or_title_change_cleans_files(self):
        changed=[[],[window(99)],[window(width=321)],[window(height=181)],
                 [window(title='different')],[window(owner='other')],[window(),window()]]
        for after in changed:
            with self.subTest(after=after),TemporaryDirectory() as directory, \
                 patch.object(game_capture,'list_game_windows',side_effect=[[window()],after]), \
                 patch.object(game_capture.subprocess,'run',side_effect=self.fake_capture) as run:
                with self.assertRaises(ValueError):game_capture.capture_game_window(directory)
                self.assertEqual(list(Path(directory).iterdir()),[])
                self.assertEqual(run.call_count,1,'must not fall back to foreground/another window')

    def test_source_file_mutation_during_reenumeration_is_rejected(self):
        with TemporaryDirectory() as directory:
            calls=[]
            def listing():
                calls.append(1)
                if len(calls)==2:
                    next(Path(directory).glob('*.png')).write_bytes(png(321,180))
                return [window()]
            with patch.object(game_capture,'list_game_windows',side_effect=listing), \
                 patch.object(game_capture.subprocess,'run',side_effect=self.fake_capture):
                with self.assertRaisesRegex(ValueError,'image changed'):game_capture.capture_game_window(directory)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_capture_failure_or_invalid_output_never_falls_back_and_cleans_up(self):
        def failure(command,**kwargs):
            Path(command[-1]).write_bytes(b'partial')
            raise subprocess.CalledProcessError(1,command,stderr=b'synthetic permission denied')
        def invalid(command,**kwargs):
            Path(command[-1]).write_bytes(b'not png')
        for operation in (failure,invalid):
            with self.subTest(operation=operation),TemporaryDirectory() as directory, \
                 patch.object(game_capture,'list_game_windows',return_value=[window()]), \
                 patch.object(game_capture.subprocess,'run',side_effect=operation) as run:
                with self.assertRaises((ValueError,subprocess.CalledProcessError)):
                    game_capture.capture_game_window(directory)
                self.assertEqual(list(Path(directory).iterdir()),[])
                self.assertEqual(run.call_count,1)

    def test_receipt_write_failure_removes_image_and_partial_receipt(self):
        original=Path.write_text
        def failed_write(path,text,*args,**kwargs):
            if str(path).endswith('.capture.json'):
                original(path,'partial receipt')
                raise OSError('synthetic disk full')
            return original(path,text,*args,**kwargs)
        with TemporaryDirectory() as directory, patch.object(game_capture,'list_game_windows',return_value=[window()]), \
             patch.object(game_capture.subprocess,'run',side_effect=self.fake_capture), \
             patch.object(Path,'write_text',failed_write):
            with self.assertRaisesRegex(OSError,'disk full'):game_capture.capture_game_window(directory)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_passive_window_listing_is_bounded_and_never_activates_app(self):
        with patch.object(game_capture,'_probe_binary',return_value=Path('/synthetic/probe')), \
             patch.object(game_capture.subprocess,'run',return_value=SimpleNamespace(stdout=b'[]')) as run:
            self.assertEqual(game_capture.list_game_windows(),[])
            run.assert_called_once_with(['/synthetic/probe'],capture_output=True,check=True,timeout=5)
        with patch.object(game_capture,'_probe_binary',return_value=Path('/synthetic/probe')), \
             patch.object(game_capture.subprocess,'run',return_value=SimpleNamespace(stdout=b' '*65537)):
            with self.assertRaisesRegex(ValueError,'exceeds bound'):game_capture.list_game_windows()

    def test_probe_cache_is_keyed_by_source_and_compile_failures_are_cleaned(self):
        with TemporaryDirectory() as directory,patch.object(game_capture,'ROOT',Path(directory)):
            source=Path(directory)/'scripts/game_windows.swift';source.parent.mkdir();source.write_text('synthetic source')
            def compile_probe(command,**kwargs):
                self.assertEqual(command[:2],['/usr/bin/xcrun','swiftc'])
                self.assertEqual(kwargs,{'capture_output':True,'check':True,'timeout':60})
                Path(command[-1]).write_bytes(b'synthetic binary not executed')
            with patch.object(game_capture.subprocess,'run',side_effect=compile_probe) as run:
                first=game_capture._probe_binary()
                self.assertEqual(first,game_capture._probe_binary());self.assertEqual(run.call_count,1)
                source.write_text('synthetic changed source')
                second=game_capture._probe_binary()
                self.assertNotEqual(first,second);self.assertEqual(run.call_count,2)
            source.write_text('synthetic failure')
            with patch.object(game_capture.subprocess,'run',side_effect=subprocess.TimeoutExpired('synthetic',60)):
                with self.assertRaises(subprocess.TimeoutExpired):game_capture._probe_binary()
            self.assertEqual(set((Path(directory)/'artifacts/capture-tools').iterdir()),{first,second})


if __name__=='__main__':unittest.main()
