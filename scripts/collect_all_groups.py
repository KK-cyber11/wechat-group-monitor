#!/usr/bin/env python3
"""Check every group listed in groups.json.

Empty checks only screenshot the current window. A group is collected when its
sidebar preview changes, its saved tail is not on screen, or it has never been
collected. Collection may bring WeChat forward, then restores the previous
chat and the previous frontmost app.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import shutil
import hashlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import passive_latest_check as base  # noqa: E402
import collection_store
import capture_engine as engine

OCR = SCRIPTS / "passive_ocr"
INPUT = SCRIPTS / "wx_input"
SEEN = ROOT / "data" / "monitor" / "seen.json"
PERF = {}


def measured(name, duration):
    item = PERF.setdefault(name, {"calls": 0, "seconds": 0.0})
    item["calls"] += 1
    item["seconds"] = round(item["seconds"] + duration, 4)

def load_groups():
    path = ROOT / "groups.local.json"
    if not path.exists():
        path = ROOT / "groups.json"
    if not path.exists():
        raise SystemExit("先在 groups.json 里写上要监控的群名")
    data = json.loads(path.read_text())
    return tuple((item["id"], item["match"]) for item in data["groups"])

GROUPS = load_groups()


def front_app():
    return base.front_app()


def activate(name):
    if not name or name.startswith("unknown"):
        return
    safe = name.replace("\\", "").replace('"', "")
    subprocess.check_call(
        ["osascript", "-e", f'tell application "System Events" to set frontmost of process "{safe}" to true'],
        timeout=5,
    )


def capture():
    started = time.monotonic()
    proc = subprocess.run([str(OCR), "/tmp/wechat-collect-ocr", "--main-window", "--cache-unchanged"], capture_output=True, text=True, timeout=25)
    measured("screenshot_ocr", time.monotonic() - started)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-400:] or f"ocr exit {proc.returncode}")
    windows = json.loads(proc.stdout or "{}").get("windows") or []
    if not windows:
        raise RuntimeError("no on-screen WeChat window")
    window = max(windows, key=lambda item: item.get("width", 0) * item.get("height", 0))
    if window.get("ocr_cache_hit"):
        measured("ocr_cache_hits", 0)
    return window


class DesktopAdapter:
    """Grounded continuous capture; every UI result is locally checked and saved."""
    def __init__(self, gid, key, output=None, record_evidence=True):
        self.gid = gid
        self.key = key
        self.output = output or ROOT / 'data/monitor/evidence' / (time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())) / gid
        self.output.mkdir(parents=True, exist_ok=True)
        self.pending = ROOT / 'data/monitor/motions' / (gid + '.json')
        if self.pending.exists():
            raise RuntimeError('unresolved prior motion; review required: ' + str(self.pending))
        self.frames = 0
        self.settle_seconds = .08
        self.record_evidence = record_evidence

    def save_progress(self, screens, window):
        path = ROOT / 'data/monitor/backfill-progress' / (self.gid + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        collection_store.atomic(path, json.dumps({'title': self.key, 'screens': screens,
            'layout': {k:window.get(k) for k in ('window_id','x','y','width','height')},
            'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'evidence_directory': str(self.output), 'status': 'paused'},ensure_ascii=False,indent=2))

    @staticmethod
    def texts(window):
        return ordered_texts(window)

    @staticmethod
    def signature(window):
        # Vision result order is not stable across invocations, even on one image.
        rows = [(base.norm(line['text']), round(line['x'], 2), round(line['y'], 2))
                for line in window['lines'] if line['x'] >= .26 and .15 < line['y'] < .9]
        return sorted(rows, key=lambda row: (-row[2], row[1], row[0]))

    def check(self, window, previous):
        if not base.title_matches(base.open_title(window['lines']), self.key):
            raise RuntimeError('chat title changed; no further input')
        if not engine.same_layout(window, previous):
            raise RuntimeError('window geometry changed; no further input')

    def archive(self, window, purpose):
        if not self.record_evidence:
            return
        if window.get('_archived_for') == str(self.output):
            return
        self.frames += 1
        stem = self.output / ('%04d-%s' % (self.frames, purpose))
        image_path = Path(window.get('image', ''))
        if image_path.is_file():
            target = stem.with_suffix('.png')
            shutil.copy2(image_path, target)
            window['evidence_image'] = str(target)
            window['evidence_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
        window['_archived_for'] = str(self.output)
        collection_store.atomic(stem.with_suffix('.json'), json.dumps(window, ensure_ascii=False, indent=2))

    def preflight(self, previous):
        if front_app() not in {'WeChat', '微信', 'Weixin'}:
            raise RuntimeError('foreground app changed; no desktop input')
        fresh = capture()
        self.check(fresh, previous)
        if self.signature(fresh) != self.signature(previous):
            self.archive(fresh, 'unexpected-preflight')
            raise RuntimeError('viewport changed before input; checkpoint unchanged')
        return fresh

    def settled(self, previous):
        time.sleep(self.settle_seconds)
        first = capture()
        self.check(first, previous)
        deadline = time.monotonic() + 1.5
        while True:
            time.sleep(.06)
            second = capture()
            self.check(second, previous)
            stable_pixels = first.get('image_fingerprint') == second.get('image_fingerprint') and first.get('image_fingerprint') is not None
            stable_text = len(self.signature(first)) >= 3 and self.signature(first) == self.signature(second)
            if stable_pixels or stable_text:
                self.settle_seconds = max(.04, self.settle_seconds * .85)
                return second
            if time.monotonic() >= deadline:
                self.archive(second, 'unsettled')
                raise RuntimeError('chat did not settle within budget')
            self.settle_seconds = min(.35, self.settle_seconds * 1.5)
            first = second

    def move(self, window, pixels):
        self.pending.parent.mkdir(parents=True, exist_ok=True)
        collection_store.atomic(self.pending, json.dumps({'source_fingerprint': window.get('image_fingerprint'),
            'title': self.key, 'pixels': pixels, 'evidence_directory': str(self.output),
            'requested_at': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False))
        x = window['x'] + window['width'] * .6
        y = window['y'] + window['height'] * .45
        started = time.monotonic()
        subprocess.check_call([str(INPUT), 'scroll-pixels', str(x), str(y), str(int(pixels))], timeout=5)
        result = self.settled(window)
        measured('scroll_and_settle', time.monotonic() - started)
        self.archive(result, 'motion')
        self.pending.unlink()
        return result

    def jump_latest(self, window, button):
        # Journal before input, just like scrolling. An unknown input result must
        # stop restoration, not replay a potentially completed click.
        self.pending.parent.mkdir(parents=True, exist_ok=True)
        collection_store.atomic(self.pending, json.dumps({'action': 'latest-button',
            'source_fingerprint': window.get('image_fingerprint'), 'title': self.key,
            'button': button, 'requested_at': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False))
        started = time.monotonic()
        subprocess.check_call([str(INPUT), 'click', str(button['x'] + button['w'] / 2),
                               str(button['y'] + button['h'] / 2)], timeout=5)
        result = self.settled(window)
        measured('latest_button_click', time.monotonic() - started)
        self.pending.unlink()
        return result

    @staticmethod
    def wait_for_history(stalled):
        time.sleep(min(.6, .2 * stalled))


def sidebar_line(window, key):
    rows = [line for line in window["lines"] if line["x"] < 0.22 and line["y"] < 0.93]
    rows.sort(key=lambda line: line["y"], reverse=True)
    for line in rows:
        if base.title_matches(line["text"], key):
            return line
    return None


def click_line(window, line):
    cx = window["x"] + (line["x"] + min(line["w"], 0.08) / 2) * window["width"]
    cy = window["y"] + (1 - (line["y"] + line["h"] / 2)) * window["height"]
    left = window["x"] + 36
    right = window["x"] + window["width"] * 0.18
    cx = min(max(cx, left), right)
    subprocess.check_call([str(INPUT), "click", f"{cx:.1f}", f"{cy:.1f}"], timeout=5)


def switch_to(window, key):
    started = time.monotonic()
    # One fresh observation grounds the sidebar click; no model round trip.
    window = capture()
    line = sidebar_line(window, key)
    if line is None:
        raise RuntimeError('target chat not in visible sidebar: ' + key)
    click_line(window, line)
    deadline = time.monotonic() + 3
    previous = None
    while time.monotonic() < deadline:
        current = capture()
        if not engine.same_layout(current, window):
            raise RuntimeError('window changed while switching chat')
        if base.title_matches(base.open_title(current['lines']), key):
            if previous is not None and DesktopAdapter.signature(current) == DesktopAdapter.signature(previous):
                measured('switch_chat', time.monotonic() - started)
                return current
            previous = current
        time.sleep(.04)
    raise RuntimeError('target chat did not settle: ' + key)


def focus_message_list(window):
    x = window["x"] + window["width"] * 0.45
    y = window["y"] + window["height"] * 0.42
    subprocess.check_call([str(INPUT), "click", f"{x:.1f}", f"{y:.1f}"], timeout=5)


def scroll_chat(window, lines):
    x = window["x"] + window["width"] * 0.45
    y = window["y"] + window["height"] * 0.42
    subprocess.check_call([str(INPUT), "scroll", f"{x:.1f}", f"{y:.1f}", str(lines)], timeout=5)


def ordered_texts(window):
    """Newest message texts first. Short name-only lines are not messages."""
    chat = [line for line in window["lines"] if line["x"] >= 0.26 and 0.15 < line["y"] < 0.9]
    chat.sort(key=lambda line: line["y"])
    texts = []
    for line in chat:
        normalized = base.norm(line["text"])
        if len(normalized) < 4 or (texts and texts[-1] == normalized):
            continue
        texts.append(normalized)
    return texts


def as_anchor(texts):
    """The three newest messages, oldest to newest. One repeated sentence is not enough."""
    return list(reversed(texts[:3]))


def anchor_hits(texts, anchor):
    if len(anchor) != 3:
        return []
    chrono = list(reversed(texts))
    return [i for i in range(len(chrono) - 2) if chrono[i:i + 3] == anchor]


def messages_after(texts, hit):
    chrono = list(reversed(texts))
    return chrono[hit + 3:]


def overlaps(previous, current):
    # Current is older; require an ordered contiguous overlap, not common words.
    return overlap_size(list(reversed(current)), list(reversed(previous))) > 0


def overlap_size(older, newer):
    for size in range(min(len(older), len(newer)), 0, -1):
        if older[-size:] == newer[:size]:
            return size
    return 0


def chain_after_anchor(chain, anchor):
    # Preserve repeated names and identical messages; global set() creates holes.
    chrono = list(reversed(chain[-1]))
    for screen in reversed(chain[:-1]):
        newer = list(reversed(screen))
        size = overlap_size(chrono, newer)
        if not size:
            return None
        chrono.extend(newer[size:])
    hits = [i for i in range(len(chrono)-2) if chrono[i:i+3] == anchor]
    return chrono[hits[0]+3:] if len(hits) == 1 else None


def load_seen():
    if not SEEN.exists():
        return {}
    return json.loads(SEEN.read_text()).get("groups", {})


def save_seen(groups):
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    collection_store.atomic(SEEN, json.dumps({"groups": groups}, ensure_ascii=False, indent=2))


def latest_button(window):
    """Require a uniquely labelled AX button in the message pane's lower right.

    Unlabelled arrows, message text, ambiguous controls and unavailable AX all
    fall back to scrolling; no hard-coded click coordinate or OCR guessing.
    """
    started = time.monotonic()
    try:
        proc = subprocess.run([str(INPUT), 'latest-buttons'] +
            [str(window[k]) for k in ('x', 'y', 'width', 'height')],
            capture_output=True, text=True, timeout=2)
        if proc.returncode:
            return None
        controls = json.loads(proc.stdout).get('buttons', [])
        matches = []
        label_pattern = r'(?:回到|返回|跳到|跳转到)(?:最底部|底部|最新(?:消息|信息)?)|(?:\d+条)?新消息|(?:jump|scroll|back)to(?:bottom|latest(?:messages?)?)'
        for button in controls:
            if button.get('role') != 'AXButton':
                continue
            if not any(re.fullmatch(label_pattern, re.sub(r'\s+', '', label).lower())
                       for label in button.get('labels', [])):
                continue
            x, y, w, h = (float(button[k]) for k in ('x', 'y', 'w', 'h'))
            cx = (x + w / 2 - window['x']) / window['width']
            cy = (y + h / 2 - window['y']) / window['height']
            if (w > 0 and h > 0 and .65 <= cx < 1 and .45 <= cy <= .9
                    and x >= window['x'] and y >= window['y']
                    and x + w <= window['x'] + window['width']
                    and y + h <= window['y'] + window['height']):
                matches.append(button)
        return matches[0] if len(matches) == 1 else None
    except (subprocess.TimeoutExpired, OSError, ValueError, TypeError, KeyError):
        return None
    finally:
        measured('latest_button_probe', time.monotonic() - started)


def same_viewport(first, second):
    if first.get('image_fingerprint') and second.get('image_fingerprint'):
        return first['image_fingerprint'] == second['image_fingerprint']
    return DesktopAdapter.signature(first) == DesktopAdapter.signature(second)


def pin_latest(window, key):
    """Return to the newest edge locally, checking layout/title and stability."""
    adapter = DesktopAdapter('restore-' + base.norm(key), key, record_evidence=False)
    deadline = time.monotonic() + 20
    window = adapter.preflight(window)
    button = latest_button(window)
    if button is not None:
        # Recheck the viewport after the AX probe; never click a stale control.
        fresh = adapter.preflight(window)
        if same_viewport(window, fresh):
            window = adapter.jump_latest(fresh, button)
        else:
            window = fresh
    # A click is not proof of reaching latest. The same downward edge check
    # confirms a successful jump and handles absent/no-op/partial buttons alike.
    unchanged = 0
    for _ in range(40):
        if time.monotonic() >= deadline:
            raise RuntimeError('latest-edge restoration budget exhausted')
        window = adapter.preflight(window)
        # Returning to latest is not historical collection: large downward steps
        # need no page overlap, but still require fresh title/layout/stability checks.
        newer = adapter.move(window, -int(window.get('height', 949) * 2))
        unchanged = unchanged + 1 if same_viewport(window, newer) else 0
        window = newer
        if unchanged >= 2:
            return window
    raise RuntimeError('latest edge not confirmed')


def collect_gap(window, key, saved_tail):
    frames = [base.chat_tail(window["lines"])]
    needle = base.norm(saved_tail)[:40]
    if not needle or needle in base.norm(frames[0]):
        return window, frames, True
    previous = chat_blob(window)
    stuck = 0
    for _ in range(40):
        scroll_chat(window, 8)
        time.sleep(0.35)
        window = capture()
        if not base.title_matches(base.open_title(window["lines"]), key):
            break
        text = base.chat_tail(window["lines"])
        frames.append(text)
        if needle in base.norm(text):
            window = pin_latest(window, key)
            frames.append(base.chat_tail(window["lines"]))
            return window, frames, True
        current = chat_blob(window)
        if base.similar(previous, current):
            stuck += 1
            if stuck >= 2:
                break
        else:
            stuck = 0
        previous = current
    window = pin_latest(window, key)
    frames.append(base.chat_tail(window["lines"]))
    return window, frames, needle in base.norm(frames[-1])


def main():
    global _collection_lock
    path = ROOT / "data/monitor/collect.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        _collection_lock = handle
        return _main_locked()


def _main_locked():
    lock_path = ROOT / "data" / "monitor" / "collect.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = _collection_lock
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return {"ok": True, "changed": False, "busy": True}
    pending = list((ROOT / 'data/monitor/motions').glob('*.json'))
    if pending:
        return {'ok': False, 'error': 'unresolved desktop motion requires review',
                'pending_motions': [str(p) for p in pending]}
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    run_started = time.monotonic()
    run_deadline = run_started + 210
    group_timings = {}
    backfill_stats = {}
    PERF.clear()
    visits = []
    before = front_app()
    opened = False
    moved_from = None
    try:
        try:
            window = capture()
        except RuntimeError as exc:
            if str(exc) != "no on-screen WeChat window":
                raise
            subprocess.check_call(["open", "-b", "com.tencent.xinWeChat"], timeout=10)
            opened = True
            time.sleep(0.5)
            window = capture()
        # A partially off-screen sidebar cannot receive its calculated clicks.
        if window["x"] + 36 < 0 or window["y"] < 0:
            moved_from = (int(window["x"]), int(window["y"]))
            subprocess.check_call(["osascript", "-e", 'tell application "System Events" to tell process "WeChat" to set position of window 1 to {0, 40}'], timeout=5)
            time.sleep(0.3)
            window = capture()
    except Exception as exc:  # noqa: BLE001
        if opened and before not in {"WeChat", "微信"}:
            activate(before)
        return {"ok": False, "error": str(exc), "focus_before": before}
    seen = load_seen()
    original_title = base.open_title(window["lines"])
    original_key = ""
    for _, key in GROUPS:
        if base.title_matches(original_title, key):
            original_key = key
    reports = []
    switched = opened or moved_from is not None
    abort_desktop = False
    try:
        # An unfinished accepted frontier gets first service, instead of repeatedly
        # starting at the newest screen and spending the same budget again.
        progress_root = ROOT / 'data/monitor/backfill-progress'
        ordered_groups = sorted(GROUPS, key=lambda pair: not (progress_root / (pair[0]+'.json')).exists())
        for gid, key in ordered_groups:
            if time.monotonic() >= run_deadline:
                reports.append({"id": gid, "key": key, "action": "budget-exhausted"})
                continue
            if selected_groups() and gid not in selected_groups():
                continue
            line = sidebar_line(window, key)
            sidebar = base.field_for_group(window["lines"], key) if line else ""
            previous = seen.get(gid, {})
            progress_path = progress_root / (gid + '.json')
            progress = json.loads(progress_path.read_text()) if progress_path.exists() else None
            anchor = previous.get("anchor") or []
            if line is None and not base.title_matches(base.open_title(window["lines"]), key):
                reports.append({"id": gid, "key": key, "action": "not-in-sidebar"})
                continue
            if not base.title_matches(base.open_title(window["lines"]), key):
                if before and front_app() not in {"WeChat", "微信"}:
                    activate("WeChat")
                switched = True
                try:
                    window = switch_to(window, key)
                except RuntimeError as exc:
                    reports.append({'id': gid, 'key': key, 'action': 'switch-failed', 'error': str(exc)})
                    abort_desktop = True
                    break
                if not base.title_matches(base.open_title(window["lines"]), key):
                    reports.append({"id": gid, "key": key, "action": "switch-failed", "title": base.open_title(window["lines"])})
                    continue
            visits.append(gid)
            if front_app() not in {'WeChat', '微信', 'Weixin'}:
                activate('WeChat')
                switched = True
            if progress and (progress.get('status') == 'needs_review' or
                    not engine.same_layout(window, progress['layout']) or
                    ordered_texts(window) != progress['screens'][-1]):
                reports.append({'id':gid,'key':key,'action':'hole','backfill_status':'resume_frontier_changed'})
                abort_desktop = True
                break
            if previous.get('problem') and not progress:
                # A prior supervised history test may have left this chat scrolled up.
                window = pin_latest(window, key)
            texts = ordered_texts(window)
            hits = anchor_hits(texts, anchor)
            # Recover only this group's missing span during its current visit.
            backfill = len(anchor) == 3 and (len(hits) == 0 or progress is not None)
            chain = [texts]
            hole = False
            group_started = time.monotonic()
            engine_stats = None
            if backfill:
                try:
                    adapter = DesktopAdapter(gid, key)
                    result = engine.collect(window, anchor, adapter,
                        min(run_deadline, time.monotonic() + 90),
                        resume_screens=progress['screens'] if progress else None)
                    window = result.pop('window')
                    chain = result.pop('screens')
                    engine_stats = result
                    texts = chain[-1]
                    hits = anchor_hits(texts, anchor)
                    if result['status'] == 'connected':
                        hits = [0]  # engine has checked the complete stitched chain
                    hole = result['status'] not in {'connected', 'baseline_required'}
                except Exception as exc:
                    hole = True
                    engine_stats = {'status': 'needs_review', 'error': str(exc)}
            group_timings[gid] = round(time.monotonic() - group_started, 3)
            if engine_stats:
                backfill_stats[gid] = engine_stats
            if hole:
                edge = list(reversed(texts[-3:])) if len(texts) >= 3 else list(reversed(texts))
                seen[gid] = {**previous, "sidebar": sidebar, "connected": False,
                    "problem": "hole", "hole_edge": edge,
                    "backlog_since": previous.get('backlog_since') or started_at,
                    "last_attempt_at": time.strftime('%Y-%m-%d %H:%M:%S'),
                    "backfill_status": (engine_stats or {}).get('status', 'hole')}
                already = previous.get("problem") == "hole" and previous.get("hole_edge") == edge
                reports.append({"id": gid, "key": key, "action": "hole", "repeat_problem": already, "backfill": engine_stats, "title": base.open_title(window["lines"])})
                if engine_stats and engine_stats.get('status') in {'needs_review', 'hole', 'ambiguous', 'boundary_without_anchor'}:
                    if progress_path.exists():
                        incomplete=json.loads(progress_path.read_text());incomplete['status']='needs_review'
                        collection_store.atomic(progress_path,json.dumps(incomplete,ensure_ascii=False,indent=2))
                    abort_desktop = True
                    break
                if engine_stats and engine_stats.get('status') in {'budget_exhausted','resume_frontier_changed'}:
                    # Preserve the accepted viewport for exact next-round resumption.
                    abort_desktop = True
                    break
                if backfill:
                    window = pin_latest(window, key)
                continue
            if len(anchor) != 3:
                seen[gid] = {"anchor": as_anchor(texts), "sidebar": sidebar, "connected": True}
                reports.append({"id": gid, "key": key, "action": "baseline", "title": base.open_title(window["lines"])})
                continue
            if len(hits) != 1:
                kind = "ambiguous" if len(hits) > 1 else "gap"
                seen[gid] = {**previous, "sidebar": sidebar, "problem": kind}
                already = previous.get("problem") == kind
                reports.append({"id": gid, "key": key, "action": "unchanged" if already else kind, "title": base.open_title(window["lines"])})
                if backfill:
                    window = pin_latest(window, key)
                continue
            fresh = chain_after_anchor(chain, anchor)
            if fresh is None:
                seen[gid] = {**previous, "sidebar": sidebar, "problem": "hole"}
                reports.append({"id": gid, "key": key, "action": "hole", "title": base.open_title(window["lines"])})
                continue
            seen[gid] = {"anchor": as_anchor(chain[0]), "sidebar": sidebar, "connected": True}
            if fresh:
                append_group_summary(gid, base.open_title(window["lines"]) or key, fresh)
            save_seen(seen)  # durable capture first, then atomic checkpoint
            if progress_path.exists():
                progress_path.unlink()  # temporary frontier only; committed evidence remains
            reports.append({
                "id": gid,
                "key": key,
                "action": "captured" if fresh else "unchanged",
                "title": base.open_title(window["lines"]),
                "new": fresh,
            })
            if backfill:
                window = pin_latest(window, key)
    finally:
        if switched:
            try:
                window = capture()
                back = sidebar_line(window, original_key) if original_key else None
                if not abort_desktop and back and not base.title_matches(base.open_title(window["lines"]), original_key):
                    click_line(window, back)
                    time.sleep(0.5)
            except Exception:
                pass
            if moved_from:
                try:
                    x, y = moved_from
                    subprocess.check_call(["osascript", "-e", f'tell application "System Events" to tell process "WeChat" to set position of window 1 to {{{x}, {y}}}'], timeout=5)
                except Exception:
                    pass
            if before and before not in {"WeChat", "微信"}:
                try:
                    activate(before)
                except Exception:
                    pass
    save_seen(seen)
    changed = any(item.get("action") in {"captured", "gap", "ambiguous", "switch-failed", "needs-backfill", "hole"} for item in reports)
    return {
        "ok": True,
        "changed": changed,
        "elapsed_seconds": round(time.monotonic() - run_started, 3),
        "performance": PERF,
        "backfill": backfill_stats,
        "group_backfill_seconds": group_timings,
        "over_interval": time.monotonic() - run_started >= 300,
        "incomplete_groups": [item["id"] for item in reports if item.get("action") in {"hole", "gap", "ambiguous", "budget-exhausted", "switch-failed", "not-in-sidebar"}],
        "focus_before": before,
        "focus_after": front_app(),
        "original_title": original_title,
        "started_at": started_at,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "visits": visits,
        "runner": os.environ.get("WECHAT_MONITOR_RUNNER", "manual"),
        "groups": reports,
    }


def visible_time_span(lines):
    found = []
    for line in lines:
        text = str(line)
        for match in re.finditer(r"(?:\d{1,2}月\d{1,2}日|\d{1,2}/\d{1,2}|昨天|前天)?\s*\d{1,2}:\d{2}", text):
            label = re.sub(r"\s+", " ", match.group(0)).strip()
            if label not in found:
                found.append(label)
    if not found:
        return "屏幕上没有显示这段消息的发送时间。"
    if len(found) == 1:
        return f"屏幕上的消息时间：{found[0]}。"
    return f"屏幕上的消息时间：{found[0]} 到 {found[-1]}。"


def append_group_summary(gid, title, lines):
    if lines:
        return collection_store.append_capture(ROOT, gid, title, lines, visible_time_span(lines))


def selected_groups():
    if "--groups" not in sys.argv:
        return set()
    value = sys.argv[sys.argv.index("--groups") + 1]
    ids = set(value.split(","))
    if not ids <= {gid for gid, _ in GROUPS}:
        raise ValueError("Unknown group id")
    return ids


def chat_blob(window):
    return "|".join(ordered_texts(window))


def announce(result):
    if not result.get("ok"):
        raise SystemExit(result.get("error") or "check failed")
    captured = [item for item in result.get("groups", []) if str(item.get("action", "")).startswith("captured")]
    if not captured:
        print("NO_REPLY")
        return
    lines = []
    for item in captured:
        title = item.get("title") or item.get("key")
        fresh = "；".join(item.get("new") or [])
        lines.append(f"{title}：{fresh or item.get('action')}")
    if result.get("focus_before") and result.get("focus_after") and result["focus_before"] != result["focus_after"]:
        lines.append(f"前台从 {result['focus_before']} 变成了 {result['focus_after']}。")
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        result = main()
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    result.setdefault("runner", os.environ.get("WECHAT_MONITOR_RUNNER", "manual"))
    result.setdefault("finished_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    collection_store.record_run(ROOT, result)
    if "--announce" in sys.argv:
        announce(result)
    else:
        print(json.dumps(result, ensure_ascii=False))
