"""长期记忆：事件流、自我笔记、用户画像、可调行为参数。"""

from copy import deepcopy
import json
import threading
import time


DEFAULT_BEHAVIOR = {
    'think_interval': 180,
    'music_enabled': True,
    'music_cooldown': 900,
    'talkativeness': 1.0,
    'curiosity': 0.7,
}
BEHAVIOR_LIMITS = {
    'think_interval': (60, 1800),
    'music_cooldown': (120, 7200),
    'talkativeness': (0.2, 2.0),
    'curiosity': (0.0, 1.0),
}
# 免费模型上下文够用但不宽裕，笔记只留最近这么多字，超出的旧内容自动淘汰。
NOTE_LIMIT = 1200
EVENT_LIMIT = 250


class Memory:
    """所有记忆都落在磁盘上，进程重启后性格和记忆延续。"""

    def __init__(self, folder, clock=time.time):
        self.folder = folder
        self.clock = clock
        self.lock = threading.RLock()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.events_path = self.folder / 'events.jsonl'
        self.self_path = self.folder / 'self.md'
        self.user_path = self.folder / 'user.md'
        self.behavior_path = self.folder / 'behavior.json'

    def _read_text(self, path):
        return path.read_text(encoding='utf-8') if path.exists() else ''

    def _append_text(self, path, text):
        text = text.strip()
        if not text:
            return
        with self.lock:
            existing = self._read_text(path).rstrip()
            body = (existing + '\n' if existing else '') + text + '\n'
            if len(body) > NOTE_LIMIT:
                body = body[-NOTE_LIMIT:].lstrip('\n')
                cut = body.find('\n')
                body = body[cut + 1:] if cut >= 0 else body
            path.write_text(body, encoding='utf-8')

    def record(self, kind, **fields):
        entry = {'kind': kind, 'time': self.clock(), **fields}
        with self.lock:
            with self.events_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
            self._trim_events()
        return entry

    def _trim_events(self):
        """事件太多时只保留最近的一段，避免文件无限增长。"""
        try:
            lines = self.events_path.read_text(encoding='utf-8').splitlines()
        except OSError:
            return
        if len(lines) <= EVENT_LIMIT * 2:
            return
        temporary = self.events_path.with_suffix('.tmp')
        temporary.write_text('\n'.join(lines[-EVENT_LIMIT:]) + '\n', encoding='utf-8')
        temporary.replace(self.events_path)

    def recent(self, limit=12, kinds=None):
        if not self.events_path.exists():
            return []
        with self.lock:
            lines = self.events_path.read_text(encoding='utf-8').splitlines()[-400:]
        entries = []
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kinds and entry.get('kind') not in kinds:
                continue
            entries.append(entry)
        return entries[-limit:]

    def self_notes(self):
        return self._read_text(self.self_path)

    def write_self(self, note):
        self._append_text(self.self_path, note)

    def user_profile(self):
        return self._read_text(self.user_path)

    def write_user(self, fact):
        self._append_text(self.user_path, fact)

    def behavior(self):
        stored = {}
        if self.behavior_path.exists():
            try:
                stored = json.loads(self.behavior_path.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                stored = {}
        return {**DEFAULT_BEHAVIOR, **stored}

    def set_behavior(self, key, value):
        if key not in DEFAULT_BEHAVIOR:
            raise ValueError('没有这个行为参数。')
        if key in BEHAVIOR_LIMITS:
            low, high = BEHAVIOR_LIMITS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f'{key} 只能设在 {low} 到 {high} 之间。')
            value = round(float(value), 2)
        else:
            value = bool(value)
        with self.lock:
            current = self.behavior()
            current[key] = value
            temporary = self.behavior_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.behavior_path)
        return deepcopy(value)
