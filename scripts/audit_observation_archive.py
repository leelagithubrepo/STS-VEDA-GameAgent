#!/usr/bin/env python3
"""Run a resumable, read-only native-reader audit of reviewed saved images."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from veda.archive_regression import reader_fingerprint,run_sweep
from veda.native_ocr import NativeTextReader


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True,help='label-free manifest with independently reviewed viewports')
    parser.add_argument('--native-helper',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--resume',action='store_true',help='verify and continue an unchanged audit; retained failures are not rerun')
    parser.add_argument('--max-new',type=int,help='optional bounded batch; remaining images stay pending')
    parser.add_argument('--timeout-seconds',type=float,default=5)
    args=parser.parse_args(argv)
    try:
        reader=NativeTextReader(args.native_helper,timeout_seconds=args.timeout_seconds)
        def fingerprint():
            return {**reader_fingerprint(ROOT,args.native_helper),
                    'timeout_seconds':args.timeout_seconds,'cli_sha256':__import__('hashlib').sha256(Path(__file__).read_bytes()).hexdigest()}
        summary=run_sweep(args.manifest,output_dir=args.output_dir,reader=reader,fingerprint=fingerprint,
                          resume=args.resume,max_new=args.max_new,
                          progress=lambda row:print(json.dumps(row),flush=True))
    except (ValueError,OSError,TypeError,KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary,indent=2,sort_keys=True))
    return 0 if summary['processing_complete'] else 2


if __name__=='__main__':raise SystemExit(main())
