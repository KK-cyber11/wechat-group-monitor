#!/usr/bin/env python3
"""Capture on-screen WeChat windows without activating them.

Prints one JSON object to stdout. Does not click, scroll, type, or write
the formal message archive.
"""

import argparse
import json
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OCR_BIN = Path(__file__).resolve().parent / "passive_ocr"

def load_groups():
    path = ROOT / "groups.local.json"
    if not path.exists():
        path = ROOT / "groups.json"
    if not path.exists():
        raise SystemExit("先在 groups.json 里写上要监控的群名")
    data = json.loads(path.read_text())
    return tuple((item["id"], item["match"]) for item in data["groups"])

GROUPS = load_groups()


def norm(text: str) -> str:
    chars = []
    for ch in text.lower():
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            chars.append(ch)
    return "".join(chars)


def similar(a: str, b: str) -> bool:
    if a == b:
        return True
    na, nb = norm(a), norm(b)
    if na == nb:
        return True
    if not na or not nb:
        return False
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if shorter in longer and len(shorter) / len(longer) >= 0.82:
        return True

    def bigrams(s: str):
        return [s[i : i + 2] for i in range(max(len(s) - 1, 1))]

    aa, bb = bigrams(na), bigrams(nb)
    ca, cb = Counter(aa), Counter(bb)
    inter = sum((ca & cb).values())
    return (2 * inter) / (len(aa) + len(bb)) >= 0.9


def front_app() -> str:
    try:
        out = subprocess.check_output(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first application process whose frontmost is true',
            ],
            text=True,
            timeout=5,
        )
        return out.strip()
    except Exception as exc:  # noqa: BLE001
        return f"unknown:{exc.__class__.__name__}"


def title_matches(text: str, key: str) -> bool:
    folded = text.replace("\\", "").replace("（", "(").replace("）", ")")
    return key.lower() in folded.lower()


def sidebar_rows(lines):
    rows = []
    for line in lines:
        if line["x"] > 0.24 or line["y"] > 0.95:
            continue
        rows.append(line)
    rows.sort(key=lambda item: item["y"], reverse=True)
    return rows


def field_for_group(lines, key: str):
    rows = sidebar_rows(lines)
    for index, line in enumerate(rows):
        if line["x"] > 0.16 or not title_matches(line["text"], key):
            continue
        preview = ""
        stamp = ""
        for other in rows[index : index + 4]:
            if abs(other["y"] - line["y"]) <= 0.02 and other["x"] > line["x"] + 0.04:
                stamp = other["text"]
            if line["y"] - 0.045 <= other["y"] < line["y"] - 0.008 and other["x"] <= 0.2:
                preview = other["text"]
                break
        return norm(stamp) + "|" + norm(preview)
    return ""


def open_title(lines):
    candidates = [
        line for line in lines if line["y"] > 0.94 and line["x"] > 0.2 and len(line["text"]) > 1
    ]
    if not candidates:
        return ""
    return sorted(candidates, key=lambda line: line["x"])[0]["text"]


def open_group(title: str):
    for gid, key in GROUPS:
        if title_matches(title, key):
            return gid
    return ""


def chat_tail(lines):
    chat = [
        line
        for line in lines
        if line["x"] >= 0.26 and 0.15 < line["y"] < 0.9 and norm(line["text"])
    ]
    chat.sort(key=lambda line: line["y"])
    tail = chat[:10]
    return " ".join(line["text"].strip() for line in tail)


def canonical_fp(windows):
    parts = []
    briefs = []
    for gid, key in GROUPS:
        best = ""
        for window in windows:
            found = field_for_group(window["lines"], key)
            if len(found) > len(best):
                best = found
        parts.append(f"{gid}={best}")
    open_parts = []
    for window in windows:
        title = open_title(window["lines"])
        gid = open_group(title)
        if not gid:
            continue
        tail = norm(chat_tail(window["lines"]))[:180]
        open_parts.append(f"{gid}:{tail}")
        briefs.append(
            {
                "id": gid,
                "title": title,
                "tail": chat_tail(window["lines"]),
                "window_id": window.get("window_id"),
            }
        )
    parts.append("open=" + ",".join(open_parts))
    return ";".join(parts), briefs


def parse_fp(fp: str):
    fields = {}
    for part in fp.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key] = value
    return fields


def group_ids():
    return [gid for gid, _key in GROUPS]


def empty_fp():
    parts = [f"{gid}=" for gid in group_ids()]
    parts.append("open=")
    return ";".join(parts)


def merge_fp(prev: str, current: str):
    if not prev:
        return current, False, []
    old, new = parse_fp(prev), parse_fp(current)
    changed = []
    merged = []
    for key in group_ids() + ["open"]:
        before, after = old.get(key, ""), new.get(key, "")
        if similar(before, after):
            merged.append(f"{key}={before}")
        else:
            merged.append(f"{key}={after}")
            changed.append({"field": key, "before": before, "after": after})
    return ";".join(merged), bool(changed), changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prev", default="")
    args = parser.parse_args()
    before = front_app()
    try:
        if not OCR_BIN.exists():
            raise RuntimeError(f"missing {OCR_BIN}")
        with tempfile.TemporaryDirectory(prefix="wechat-passive-") as tmp:
            proc = subprocess.run(
                [str(OCR_BIN), tmp],
                check=False,
                capture_output=True,
                text=True,
                timeout=25,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip()[-500:] or f"ocr exit {proc.returncode}")
            payload = json.loads(proc.stdout or '{"windows":[]}')
            windows = payload.get("windows") or []
            if not windows:
                current = empty_fp()
                briefs = []
            else:
                current, briefs = canonical_fp(windows)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc), "focus_before": before}, ensure_ascii=False))
        return
    after = front_app()
    fp, changed, changes = merge_fp(args.prev.strip(), current)
    visible = []
    for gid, key in GROUPS:
        field = parse_fp(fp).get(gid, "")
        visible.append({"id": gid, "key": key, "sidebar": field})
    result = {
        "ok": True,
        "changed": changed,
        "fp": fp,
        "changes": changes,
        "open": briefs,
        "groups": visible,
        "window_count": len(windows) if "windows" in dir() else len(briefs),
        "focus_before": before,
        "focus_after": after,
        "stole_focus": before != after and after in {"WeChat", "微信", "Weixin"},
    }
    # window_count fix: windows may be out of scope if exception path not taken
    result["window_count"] = len(locals().get("windows") or [])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
