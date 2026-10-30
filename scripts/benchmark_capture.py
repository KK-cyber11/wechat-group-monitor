#!/usr/bin/env python3
"""Read-only supervised live benchmark; never changes production checkpoints.

Runs the continuous engine to an observed time label in one named group, then
restores latest view and previous chat/app. Takes the existing collection lock.
"""
import argparse
import fcntl
import hashlib
import json
import subprocess
import time
from pathlib import Path
import collect_all_groups as c
import capture_engine as engine


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--group',required=True)
    parser.add_argument('--until',required=True,help='normalized visible marker, e.g. 昨天22:53')
    parser.add_argument('--seconds',type=int,default=60)
    args=parser.parse_args()
    groups=dict(c.GROUPS)
    if args.group not in groups:raise ValueError('unknown group')
    production=c.ROOT
    output=production/'data/monitor/tests'/time.strftime('speed-live-%Y%m%d-%H%M%S')
    output.mkdir(parents=True)
    lock=(production/'data/monitor/collect.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    before_app=c.front_app()
    before_hash=hashlib.sha256(c.SEEN.read_bytes()).hexdigest() if c.SEEN.exists() else None
    # Pending motion/evidence also live under the isolated output root.
    c.ROOT=output
    original_key=None
    report={}
    try:
        c.activate('WeChat')
        subprocess.check_call(['osascript','-e','tell application "System Events" to tell process "WeChat" to click menu item "微信" of menu 1 of menu bar item "窗口" of menu bar 1'],timeout=5)
        first=c.capture()
        for key in groups.values():
            if c.base.title_matches(c.base.open_title(first['lines']),key):original_key=key
        w=first if c.base.title_matches(c.base.open_title(first['lines']),groups[args.group]) else c.switch_to(first,groups[args.group])
        w=c.pin_latest(w,groups[args.group])
        adapter=c.DesktopAdapter(args.group,groups[args.group],output/'frames')
        marker=c.base.norm(args.until)
        def boundary(window):
            return any(marker in c.base.norm(line['text']) for line in window['lines'] if line['x']>=.26)
        result=engine.collect(w,[],adapter,time.monotonic()+args.seconds,stop_when=boundary)
        w=result.pop('window');result.pop('screens')
        report.update(result)
        report['performance']=dict(c.PERF)
        report['output']=str(output)
        report['history_ocr_tolerance']='>=.95 similarity on lines >=18 chars; variants explicit; never used for production anchor commits'
        # Persist the measurement before any optional desktop restoration. A host
        # disconnect must not erase a completed capture measurement.
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        if result['status'] not in {'needs_review','hole'}:
            c.pin_latest(w,groups[args.group])
            if original_key and original_key != groups[args.group]:c.switch_to(c.capture(),original_key)
    finally:
        c.ROOT=production
        try:
            if before_app:c.activate(before_app)
        finally:
            report['production_checkpoint_unchanged']=before_hash==(hashlib.sha256(c.SEEN.read_bytes()).hexdigest() if c.SEEN.exists() else None)
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            fcntl.flock(lock,fcntl.LOCK_UN)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
