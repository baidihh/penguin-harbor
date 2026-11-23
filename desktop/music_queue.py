"""Share the desktop queue and timed lyrics with the penguin's song picker."""

from bisect import bisect_right
import json
import re
import threading

from .netease import NoPlayableSource


TIMESTAMP = re.compile(r'\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]')


def parse_lyrics(lrc):
    match = re.search(r'\[offset:\s*([+-]?\d+)\]', lrc, re.IGNORECASE)
    offset = int(match[1]) if match else 0
    cues = []
    for line in lrc.splitlines():
        tags = list(TIMESTAMP.finditer(line))
        text = line[tags[-1].end():].strip() if tags else ''
        if not text:
            continue
        for tag in tags:
            millis = (int(tag[1]) * 60 + int(tag[2])) * 1000 + int((tag[3] or '').ljust(3, '0'))
            cues.append((max(0, millis - offset) / 1000, text))
    return sorted(cues, key=lambda cue: cue[0])


class MusicQueue:
    def __init__(self, player, path):
        self.player = player
        self.path = path
        self._commands = threading.RLock()
        self.closed = threading.Event()
        self.current = None
        self.armed = False
        self.error = ''
        self.cues = {}
        try:
            songs = json.loads(path.read_text(encoding='utf-8'))
            self.songs = tuple(song for song in songs[:100] if isinstance(song, dict) and 'id' in song and 'name' in song)
        except (OSError, ValueError, TypeError):
            self.songs = ()
        self.worker = None

    @property
    def available(self):
        return self.player.available

    def search(self, keyword, limit=8):
        return self.player.search(keyword, limit=limit)

    def _save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.songs, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)

    def enqueue(self, *songs):
        with self._commands:
            known = {song['id'] for song in self.songs}
            added = []
            for song in songs:
                if song['id'] not in known and len(self.songs) + len(added) < 100:
                    added.append(dict(song))
                    known.add(song['id'])
            if added:
                self.songs += tuple(added)
                self._save()

    def play(self, song):
        with self._commands:
            if self.closed.is_set():
                raise RuntimeError('播放器已退出。')
            played = self.player.play(song)
            self.current = dict(song)
            self.armed = True
            self.error = ''
            self.enqueue(song)
            identity = song['id']
            if identity not in self.cues:
                self.cues[identity] = None
                threading.Thread(target=self._load_lyrics, args=(identity,), daemon=True).start()
            return played

    def _load_lyrics(self, identity):
        try:
            self.cues[identity] = parse_lyrics(self.player.lyrics(identity))
        except (OSError, ValueError):
            self.cues.pop(identity, None)

    def play_collection(self, songs):
        with self._commands:
            last_error = None
            for index, song in enumerate(songs):
                try:
                    self.play(song)
                except NoPlayableSource as error:
                    last_error = error
                    continue
                ordered = {track['id']: track for track in songs[index:]}
                for queued in self.songs:
                    ordered.setdefault(queued['id'], queued)
                self.songs = tuple(ordered.values())[:100]
                self._save()
                return song
        if last_error:
            raise NoPlayableSource(str(last_error) if len(songs) == 1 else '网易云没有提供这组歌曲的完整音源。')
        raise ValueError('这次读取的歌单是空的。')

    def select(self, identity):
        with self._commands:
            song = next((song for song in self.songs if song['id'] == identity), None)
            if song is None:
                raise ValueError('这首歌已离开队列。')
            return self.play(song)

    def remove(self, identity):
        with self._commands:
            if (self.current or {}).get('id') == identity and self.player.state()['status'] in ('playing', 'paused'):
                raise ValueError('请先停止或切歌，再移出正在播放的歌曲。')
            self.songs = tuple(song for song in self.songs if song['id'] != identity)
            self._save()

    def step(self, direction):
        with self._commands:
            identity = (self.current or {}).get('id')
            index = next((i for i, song in enumerate(self.songs) if song['id'] == identity), -1)
            target = index + direction if index >= 0 else 0
            stride = -1 if direction < 0 else 1
            while 0 <= target < len(self.songs):
                try:
                    self.play(self.songs[target])
                except NoPlayableSource:
                    target += stride
                else:
                    return True
            return False

    def stop(self):
        with self._commands:
            self.armed = False
            self.player.stop()

    def set_paused(self, paused):
        with self._commands:
            state = self.player.state()['status']
            if state not in ('playing', 'paused'):
                raise ValueError('当前没有正在播放的歌曲。')
            if paused and state == 'playing':
                self.player.pause()
            elif not paused and state == 'paused':
                self.player.resume()

    def toggle_pause(self):
        with self._commands:
            state = self.player.state()['status']
            if state == 'playing':
                self.player.pause()
            elif state == 'paused':
                self.player.resume()

    def close(self):
        self.closed.set()
        self.player.close()

    def start(self):
        if self.worker is None:
            self.worker = threading.Thread(target=self._advance, daemon=True, name='music-queue')
            self.worker.start()

    def _advance(self):
        while not self.closed.wait(0.4):
            if not self.armed or self.player.state()['status'] in ('playing', 'paused'):
                continue
            with self._commands:
                if not self.armed or self.player.state()['status'] in ('playing', 'paused'):
                    continue
                self.armed = False
                try:
                    process = self.player.process
                    if process and process.poll() != 0:
                        raise RuntimeError('歌曲播放中断了，可以从队列重试。')
                    self.step(1)
                except (OSError, RuntimeError, ValueError) as error:
                    self.error = str(error)

    def snapshot(self):
        current = self.current or {}
        identity = current.get('id')
        status = self.player.state()['status']
        playing = status == 'playing'
        paused = status == 'paused'
        active = playing or paused
        timed_lyrics = self.cues.get(identity)
        cues = timed_lyrics or []
        position = self.player.position() if active else 0
        index = bisect_right(cues, position, key=lambda cue: cue[0]) - 1
        lyric = cues[index][1] if active and index >= 0 else ''
        return {'current': current, 'playing': playing, 'paused': paused, 'songs': self.songs, 'lyric': lyric,
                'position': int(position), 'has_lyrics': bool(cues), 'lyrics_loading': identity in self.cues and timed_lyrics is None,
                'error': self.error}
