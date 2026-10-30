#!/usr/bin/env python3
"""Keep OCR and a lightly condensed reading of the same capture together."""
import argparse
import fcntl
import hashlib
import json
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

@contextmanager
def locked(root):
    directory = root / 'data/monitor'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'reading.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield directory

def atomic(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(value)
    temp.replace(path)

def append_capture(root, gid, title, lines, message_time):
    body = '\n'.join('- ' + str(line) for line in lines)
    capture_id = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:12]
    record = dict(capture_id=capture_id, group_id=gid, title=title,
                  captured_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  message_time=message_time, lines=list(lines),
                  sha256=hashlib.sha256(body.encode()).hexdigest())
    with locked(root) as monitor:
        records = monitor / 'captures'
        records.mkdir(exist_ok=True)
        atomic(records / (capture_id + '.json'), json.dumps(record, ensure_ascii=False, indent=2))
        ensure_excerpt(root, record)
    return capture_id

def ensure_excerpt(root, record):
    path = root / 'data/groups' / record['group_id'] / 'summary.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else '# 群聊记录\n'
    marker = '<!-- capture:' + record['capture_id'] + ' -->'
    if marker not in text:
        block = '\n## 采集于 ' + record['captured_at'] + '（Asia/Shanghai）\n\n'
        block += record['message_time'] + '\n\n'
        block += marker + '\n\n<!-- reading:' + record['capture_id'] + ' -->\n\n'
        block += '### OCR 摘抄\n\n' + '\n'.join('- ' + str(line) for line in record['lines']) + '\n'
        atomic(path, text + block)
    return path

def due(root):
    # Read only: safe for a quiet OpenClaw trigger. No screenshots or UI input.
    entries = []
    records = root / 'data/monitor/captures'
    if records.exists():
        for path in sorted(records.glob('*.json')):
            record = json.loads(path.read_text())
            summary = root / 'data/groups' / record['group_id'] / 'summary.md'
            marker = '<!-- condensed:' + record['capture_id'] + ' -->'
            if not summary.exists() or marker not in summary.read_text():
                entries.append(record)
            if len(entries) >= 3:
                break
    watch = root / 'data/monitor/watch.json'
    return {'empty': not entries, 'captures': entries,
            'watch': json.loads(watch.read_text()) if watch.exists() else {'people': []}}

def commit(root, result):
    saved = []
    with locked(root) as monitor:
        for entry in result['readings']:
            cid = entry['capture_id']
            if not re.fullmatch(r'[A-Za-z0-9-]+', cid):
                raise ValueError('Invalid capture id')
            record = json.loads((monitor / 'captures' / (cid + '.json')).read_text())
            if entry['source_sha256'] != record['sha256']:
                raise ValueError('Reading does not match the captured source')
            reading = entry['text'].strip()
            if not reading:
                raise ValueError('Empty reading')
            path = ensure_excerpt(root, record)
            text = path.read_text()
            marker = '<!-- condensed:' + cid + ' -->'
            if marker not in text:
                anchor = '<!-- reading:' + cid + ' -->'
                assert text.count(anchor) == 1
                text = text.replace(anchor, '### 简要整理\n\n' + reading + '\n\n' + marker, 1)
                atomic(path, text)
            saved.append({'capture_id': cid, 'path': str(path)})
    return {'ok': True, 'saved': saved}

def record_run(root, result):
    with locked(root) as monitor:
        atomic(monitor / 'last-run.json', json.dumps(result, ensure_ascii=False, indent=2))
        with (monitor / 'runs.jsonl').open('a') as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + '\n')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['due', 'commit'])
    parser.add_argument('file', nargs='?')
    args = parser.parse_args()
    result = due(ROOT) if args.action == 'due' else commit(ROOT, json.loads(Path(args.file).read_text()))
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
