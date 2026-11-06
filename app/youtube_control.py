"""Cross-process YouTube pacing, extraction exclusion and persistent cooldown."""
import fcntl
import hashlib
import json
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path


class CooldownError(Exception):
    def __init__(self, seconds):
        self.retry_after = max(1, math.ceil(seconds))
        super().__init__(f'YouTube 暂时限制访问，请在 {self.retry_after} 秒后重试')


class YouTubeControl:
    def __init__(self, root=None, interval=None):
        self.root = Path(root or os.environ.get('SITE_DATA_ROOT', '/data')) / '.youtube-control'
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.interval = float(interval if interval is not None else os.environ.get('SITE_YOUTUBE_INTERVAL', '5'))

    @contextmanager
    def lock(self, name='request', timeout=60):
        # Stable lock files are never removed; striped video locks bound disk usage.
        with (self.root / (name + '.lock')).open('a') as handle:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('YouTube request queue timed out')
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self):
        try:
            return json.loads((self.root / 'state.json').read_text())
        except (OSError, ValueError):
            return {}

    def write(self, state):
        temporary = self.root / 'state.tmp'
        temporary.write_text(json.dumps(state))
        temporary.replace(self.root / 'state.json')

    def remaining(self):
        return max(0, math.ceil(self.read().get('until', 0) - time.time()))

    @contextmanager
    def extraction(self):
        with self.lock():
            state = self.read()
            if state.get('until', 0) > time.time():
                raise CooldownError(state['until'] - time.time())
            delay = state.get('last_request', 0) + self.interval - time.time()
            if delay > 0:
                time.sleep(delay)
            state['last_request'] = time.time()
            self.write(state)
            yield

    def outcome(self, success, immediate=False):
        with self.lock():
            state = self.read()
            if success:
                state.update(failures=0, until=0)
            else:
                failures = min(6, max(2 if immediate else 1, state.get('failures', 0) + 1))
                state['failures'] = failures
                if failures >= 2:
                    state['until'] = time.time() + min(900, 120 * 2 ** (failures - 2))
            self.write(state)
            return max(0, math.ceil(state.get('until', 0) - time.time()))

    def video_lock(self, url):
        stripe = int(hashlib.sha256(url.encode()).hexdigest()[:4], 16) % 64
        return self.lock(f'video-{stripe}')
