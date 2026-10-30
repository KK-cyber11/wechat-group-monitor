#!/usr/bin/env python3
"""Check all three registered WeChat groups.

Empty checks only screenshot the current window. A group is collected when its
sidebar preview changes, its saved tail is not on screen, or it has never been
collected. Collection may bring WeChat forward, then restores the previous
chat and the previous frontmost app.
"""

import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import passive_latest_check as base  # noqa: E402

OCR = SCRIPTS / "passive_ocr"
INPUT = SCRIPTS / "wx_input"
SEEN = ROOT / "data" / "monitor" / "seen.json"

def load_groups():
    path = Path(__file__).resolve().parents[1] / "groups.json"
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
    proc = subprocess.run([str(OCR), "/tmp/wechat-collect-ocr"], capture_output=True, text=True, timeout=25)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-400:] or f"ocr exit {proc.returncode}")
    windows = json.loads(proc.stdout or "{}").get("windows") or []
    if not windows:
        raise RuntimeError("no on-screen WeChat window")
    return max(windows, key=lambda item: item.get("width", 0) * item.get("height", 0))


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
    """Adjacent screens must share visible messages. Seeing both ends is not enough."""
    if not previous or not current:
        return False
    need = 2 if min(len(previous), len(current)) >= 4 else 1
    return len(set(previous) & set(current)) >= need


def chain_after_anchor(chain, anchor):
    chrono = []
    seen = set()
    for screen in reversed(chain):
        for text in reversed(screen):
            if text not in seen:
                seen.add(text)
                chrono.append(text)
    hits = [i for i in range(len(chrono) - 2) if chrono[i:i + 3] == anchor]
    if len(hits) != 1:
        return None
    return chrono[hits[0] + 3:]


def load_seen():
    if not SEEN.exists():
        return {}
    return json.loads(SEEN.read_text()).get("groups", {})


def save_seen(groups):
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps({"groups": groups}, ensure_ascii=False, indent=2))


def pin_latest(window, key):
    """Scroll toward the newest edge until the screen stops changing."""
    previous = chat_blob(window)
    for _ in range(4):
        scroll_chat(window, -8)
        time.sleep(0.35)
        window = capture()
        current = chat_blob(window)
        title = base.open_title(window["lines"])
        if not base.title_matches(title, key):
            break
        if base.similar(previous, current):
            break
        previous = current
    return window


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
    lock_path = ROOT / "data" / "monitor" / "collect.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return {"ok": True, "changed": False, "busy": True}
    before = front_app()
    try:
        window = capture()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "focus_before": before}
    seen = load_seen()
    original_title = base.open_title(window["lines"])
    original_key = ""
    for _, key in GROUPS:
        if base.title_matches(original_title, key):
            original_key = key
    reports = []
    switched = False
    try:
        for gid, key in GROUPS:
            line = sidebar_line(window, key)
            sidebar = base.field_for_group(window["lines"], key) if line else ""
            previous = seen.get(gid, {})
            anchor = previous.get("anchor") or []
            if line is None and not base.title_matches(base.open_title(window["lines"]), key):
                reports.append({"id": gid, "key": key, "action": "not-in-sidebar"})
                continue
            if not base.title_matches(base.open_title(window["lines"]), key):
                if before and front_app() not in {"WeChat", "微信"}:
                    activate("WeChat")
                    time.sleep(0.2)
                switched = True
                click_line(window, line)
                time.sleep(0.6)
                window = capture()
                if not base.title_matches(base.open_title(window["lines"]), key):
                    reports.append({"id": gid, "key": key, "action": "switch-failed", "title": base.open_title(window["lines"])})
                    continue
            texts = ordered_texts(window)
            hits = anchor_hits(texts, anchor)
            backfill = "--backfill" in sys.argv
            if previous.get("problem") == "hole" and not backfill:
                reports.append({"id": gid, "key": key, "action": "needs-backfill", "title": base.open_title(window["lines"])})
                window = capture()
                continue
            if len(anchor) == 3 and len(hits) != 1 and not backfill:
                seen[gid] = {**previous, "sidebar": sidebar, "problem": "needs-backfill"}
                reports.append({"id": gid, "key": key, "action": "needs-backfill", "title": base.open_title(window["lines"])})
                window = capture()
                continue
            stuck = 0
            previous_blob = "|".join(texts[:5])
            chain = [texts]
            hole = False
            if backfill and len(anchor) == 3 and len(hits) != 1:
                focus_message_list(window)
            for _ in range(80):
                if not backfill or len(hits) == 1 or len(anchor) != 3 or hole:
                    break
                scroll_chat(window, 24 if stuck else 12)
                time.sleep(0.9 if stuck else 0.35)
                window = capture()
                if not base.title_matches(base.open_title(window["lines"]), key):
                    break
                new_texts = ordered_texts(window)
                if not overlaps(texts, new_texts):
                    hole = True
                    break
                texts = new_texts
                chain.append(texts)
                hits = anchor_hits(texts, anchor)
                blob = "|".join(texts[:5])
                if blob == previous_blob:
                    stuck += 1
                    if stuck >= 4:
                        break
                else:
                    stuck = 0
                previous_blob = blob
            if hole:
                edge = list(reversed(texts[-3:])) if len(texts) >= 3 else list(reversed(texts))
                seen[gid] = {**previous, "sidebar": sidebar, "problem": "hole", "hole_edge": edge}
                already = previous.get("problem") == "hole" and previous.get("hole_edge") == edge
                reports.append({"id": gid, "key": key, "action": "unchanged" if already else "hole", "title": base.open_title(window["lines"])})
                window = capture()
                continue
            if len(anchor) != 3:
                seen[gid] = {"anchor": as_anchor(texts), "sidebar": sidebar, "connected": True}
                reports.append({"id": gid, "key": key, "action": "baseline", "title": base.open_title(window["lines"])})
                window = capture()
                continue
            if len(hits) != 1:
                kind = "ambiguous" if len(hits) > 1 else "gap"
                seen[gid] = {**previous, "sidebar": sidebar, "problem": kind}
                already = previous.get("problem") == kind
                reports.append({"id": gid, "key": key, "action": "unchanged" if already else kind, "title": base.open_title(window["lines"])})
                window = capture()
                continue
            fresh = chain_after_anchor(chain, anchor)
            if fresh is None:
                seen[gid] = {**previous, "sidebar": sidebar, "problem": "hole"}
                reports.append({"id": gid, "key": key, "action": "hole", "title": base.open_title(window["lines"])})
                window = capture()
                continue
            seen[gid] = {"anchor": as_anchor(chain[0]), "sidebar": sidebar, "connected": True}
            if fresh:
                append_group_summary(gid, base.open_title(window["lines"]) or key, fresh)
            reports.append({
                "id": gid,
                "key": key,
                "action": "captured" if fresh else "unchanged",
                "title": base.open_title(window["lines"]),
                "new": fresh,
            })
            window = capture()
    finally:
        if switched:
            try:
                window = capture()
                back = sidebar_line(window, original_key) if original_key else None
                if back and not base.title_matches(base.open_title(window["lines"]), original_key):
                    click_line(window, back)
                    time.sleep(0.5)
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
        "focus_before": before,
        "focus_after": front_app(),
        "original_title": original_title,
        "groups": reports,
    }


def append_group_summary(gid, title, lines):
    if not lines:
        return
    path = ROOT / "data" / "groups" / gid / "summary.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# {title}\n")
    stamp = time.strftime("%Y-%m-%d %H:%M")
    with path.open("a") as handle:
        handle.write(f"\n## {stamp}\n\n")
        for line in lines:
            text = str(line).strip()
            if text:
                handle.write(f"- {text}\n")


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
    if "--announce" in sys.argv:
        announce(result)
    else:
        print(json.dumps(result, ensure_ascii=False))
