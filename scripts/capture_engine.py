"""Continuous screenshot collection policy. No model calls, scheduler or commits.

The desktop adapter must provide fresh, title/layout-checked observations.
Budgets and uncertain overlap are incomplete, never successful checkpoints.
"""
import time
from difflib import SequenceMatcher


def equivalent(a, b):
    # History-only, one-character OCR jitter; all variants remain explicit evidence.
    return a == b or (min(len(a),len(b)) >= 18 and abs(len(a)-len(b)) <= 1
                     and SequenceMatcher(None,a,b,autojunk=False).ratio() >= .95)


def overlap_size(older, newer, allow_variants=False):
    equal = equivalent if allow_variants else lambda a,b:a==b
    def matches(a,b):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    for size in range(min(len(older), len(newer)), 1, -1):
        if matches(older[-size:], newer[:size]):
            # A repeated matching block cannot identify the shared frontier.
            block = older[-size:]
            hits = sum(matches(newer[i:i + size],block) for i in range(len(newer) - size + 1))
            if hits == 1:
                return size
    return 0


def stitch(screens, allow_variants=False):
    """Screens and each screen's lines are newest first. Never global-dedup."""
    result = list(reversed(screens[-1]))
    for screen in reversed(screens[:-1]):
        newer = list(reversed(screen))
        size = overlap_size(result, newer, allow_variants)
        if not size:
            return None
        result.extend(newer[size:])
    return result


def anchor_positions(chrono, anchor):
    if len(anchor) != 3:
        return []
    return [i for i in range(len(chrono) - 2) if chrono[i:i + 3] == anchor]


def same_layout(a, b):
    fields = ('window_id', 'x', 'y', 'width', 'height')
    return all(a.get(k) == b.get(k) for k in fields)


class AdaptiveStep:
    def __init__(self, height):
        self.minimum = max(40, int(height * .12))
        self.maximum = max(self.minimum, int(height * .68))
        self.value = max(self.minimum, int(height * .42))

    def accepted(self, shared_lines):
        if shared_lines >= 6:
            self.value = min(self.maximum, int(self.value * 1.2))

    def shrink(self):
        self.value = max(self.minimum, self.value // 2)


def collect(initial, anchor, adapter, deadline, stop_when=None, clock=time.monotonic, resume_screens=None):
    """Freeze newest endpoint; locally verify every motion and ordered overlap.

    adapter: texts(window), move(window, pixels), check(window, previous),
    archive(window, purpose), preflight(window). A timed-out move is never retried.
    stop_when enables a read-only historical boundary instead of an anchor.
    """
    started = clock()
    current = initial
    screens = resume_screens or [adapter.texts(current)]
    policy = AdaptiveStep(initial.get('height', 949) * .75)
    stats = dict(scrolls=0, recovery_scrolls=0, retries=0, accepted_screens=1,
                 initial_step=policy.value, overlap_lines=[], ocr_variants=[])
    stalled = 0
    allow_variants = stop_when is not None
    adapter.archive(current, 'initial')

    def finish(status, lines=None):
        return dict(status=status, lines=lines, screens=screens, window=current,
                    elapsed_seconds=round(clock() - started, 3), **stats)

    if resume_screens and adapter.texts(current) != resume_screens[-1]:
        return finish('resume_frontier_changed')
    stats['resumed_screens'] = len(resume_screens or [])
    if stop_when is None and len(anchor) != 3:
        return finish('baseline_required')
    while True:
        chrono = stitch(screens, allow_variants)
        if chrono is None:
            return finish('hole')
        hits = anchor_positions(chrono, anchor)
        if len(hits) > 1:
            return finish('ambiguous')
        if stop_when is not None:
            if stop_when(current):
                return finish('history_boundary', chrono)
        elif len(hits) == 1:
            return finish('connected', chrono[hits[0] + 3:])
        if clock() >= deadline:
            return finish('budget_exhausted')
        try:
            # Fresh zero-motion observation; input is never grounded in a stale frame.
            current = adapter.preflight(current)
            old_texts = screens[-1]
            candidate = adapter.move(current, policy.value)
            stats['scrolls'] += 1
            adapter.check(candidate, current)
            adapter.archive(candidate, 'older')
            new_texts = adapter.texts(candidate)
            if new_texts == old_texts:
                stalled += 1
                current = candidate
                if stalled >= 3:
                    return finish('boundary_without_anchor')
                adapter.wait_for_history(stalled)
                continue
            stalled = 0
            shared = overlap_size(list(reversed(new_texts)), list(reversed(old_texts)), allow_variants)
            if not shared:
                # One bounded half-step toward the known viewport, then reobserve.
                recovered = False
                for _ in range(2):
                    if clock() >= deadline:
                        break
                    candidate = adapter.preflight(candidate)
                    candidate = adapter.move(candidate, -max(policy.minimum, policy.value // 2))
                    stats['scrolls'] += 1
                    stats['recovery_scrolls'] += 1
                    adapter.check(candidate, current)
                    adapter.archive(candidate, 'overlap_recovery')
                    new_texts = adapter.texts(candidate)
                    shared = overlap_size(list(reversed(new_texts)), list(reversed(old_texts)), allow_variants)
                    if shared and new_texts != old_texts:
                        recovered = True
                        break
                    if new_texts == old_texts:
                        # Restored exactly: retry a smaller planned step next iteration.
                        policy.shrink()
                        current = candidate
                        stats['retries'] += 1
                        recovered = True
                        break
                if not recovered:
                    current = candidate
                    return finish('hole')
                if new_texts == old_texts:
                    if stats['retries'] >= 3:
                        return finish('hole')
                    continue
                policy.shrink()
            screens.append(new_texts)
            if allow_variants:
                older=list(reversed(new_texts))[-shared:]
                newer=list(reversed(old_texts))[:shared]
                stats['ocr_variants'].extend({'older':a,'newer':b} for a,b in zip(older,newer) if a!=b)
            stats['accepted_screens'] += 1
            stats['overlap_lines'].append(shared)
            policy.accepted(shared)
            current = candidate
            if hasattr(adapter, 'save_progress'):
                adapter.save_progress(screens, current)
        except Exception as exc:
            result = finish('needs_review')
            result['error'] = str(exc)
            return result
