"""网易云音乐接入：搜索、取音频地址、用本机能力播放。

为什么不走 ncm-cli 播放：0.1.7 把播放器实现外置到远端 manifest，
本地没有 mpv 时会静默失败（daemon 起来了但不 spawn 播放器、也不报错），
而本机没有 mpv/vlc/ffplay。这里改为公开接口取真实音频地址，
Windows 交给系统 MCI 播放，其他平台退回 ffmpeg。整条链路可验证。
"""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid


SEARCH_API = 'https://music.163.com/api/search/get'
URL_API = 'https://music.163.com/api/song/enhance/player/url'
LYRIC_API = 'https://music.163.com/api/song/lyric'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://music.163.com/'}

def find_ffmpeg():
    """优先用 PATH 里的；否则退回 imageio-ffmpeg 自带的二进制。"""
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


PLAY_SCRIPT = Path(__file__).with_name('play_audio.ps1')


class NoPlayableSource(RuntimeError):
    pass


class NetEase:
    def __init__(self, ffmpeg=None, folder=None, timeout=10, account=None):
        self.ffmpeg = ffmpeg or find_ffmpeg()
        self.folder = Path(folder) if folder else Path(tempfile.gettempdir())
        self.folder.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.account = account
        self._official_stop = None
        self._official_state = {}
        self.process = None
        self.current = None
        self.last_error = ''
        self.progress_path = None
        self.started_at = 0.0
        self.control_path = None
        self._paused = False
        self._paused_at = 0.0
        self._paused_seconds = 0.0
        self._last_position = 0.0
        self._player_lock = threading.RLock()
        self._closed = False

    @property
    def available(self):
        if os.name == 'nt' and PLAY_SCRIPT.exists():
            return True
        return bool(shutil.which('afplay') or shutil.which('ffplay'))

    def _get(self, url):
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())

    def search(self, keyword, limit=5):
        if self.account and self.account.command:
            try:
                tracks = self.account.search(keyword, limit)
                if tracks:
                    return tracks
            except (OSError, RuntimeError, ValueError):
                pass
        query = urllib.parse.urlencode({'s': keyword, 'type': 1, 'limit': limit, 'offset': 0})
        data = self._get(f'{SEARCH_API}?{query}')
        if data.get('code', 200) != 200:
            raise RuntimeError('网易云搜索服务暂时不可用。')
        songs = (data.get('result') or {}).get('songs') or []
        found = [{'id': song['id'], 'name': song['name'],
                  'artist': ', '.join(artist['name'] for artist in song.get('artists', [])),
                  'duration': round(song.get('duration', 0) / 1000),
                  'fee': song.get('fee')}
                 for song in songs]
        return found

    def stream_url(self, song_id, bitrate=320000):
        """返回可直接播放的音频地址；没有版权或需付费时返回空。"""
        data = self._get(f'{URL_API}?ids=[{song_id}]&br={bitrate}')
        if data.get('code', 200) != 200:
            raise RuntimeError('网易云播放地址接口暂时不可用。')
        entry = (data.get('data') or [{}])[0]
        if entry.get('freeTrialInfo'):
            return ''
        return entry.get('url') or ''

    def playable(self, song_id):
        return bool(self.stream_url(song_id))

    def lyrics(self, song_id):
        query = urllib.parse.urlencode({'id': song_id, 'lv': -1, 'tv': -1})
        data = self._get(f'{LYRIC_API}?{query}')
        return (data.get('lrc') or {}).get('lyric') or ''

    def _download(self, url, name):
        suffix = '.mp3'
        target = self.folder / f'penguin-{name}{suffix}'
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=60) as response, target.open('wb') as handle:
            handle.write(response.read())
        return target

    def play(self, song):
        """播放一首歌。Windows 走 MCI（本机验证可出声），其他平台退回 ffmpeg。"""
        if not self.available:
            raise RuntimeError('没有可用的播放方式。')
        song_id = song['id'] if isinstance(song, dict) else song
        name = song.get('name', str(song_id)) if isinstance(song, dict) else str(song_id)
        if self.current and self.current.get('id') == song_id and self.state()['status'] in ('playing', 'paused'):
            if self.state()['status'] == 'paused':
                self.resume()
            return self.current
        # Keep the 320 kbps route when NetEase makes the full track available.
        track = song if isinstance(song, dict) else {}
        try:
            url = self.stream_url(song_id)
        except (OSError, RuntimeError, ValueError):
            if not (self.account and track.get('account_playable')):
                raise RuntimeError('连接网易云播放地址接口失败，请稍后重试。') from None
            url = ''
        if url:
            try:
                self._start_player(url, str(int(song_id)), song_id, name)
                return self.current
            except OSError:
                if not (self.account and track.get('account_playable')):
                    raise RuntimeError('已找到歌曲，下载网易云音频失败，请稍后重试。') from None
        # Queues created by older versions contain only the original numeric ID.
        if self.account and self.account.command and not track.get('account_id'):
            try:
                matches = self.account.search(name, 12)
                track = next((s for s in matches if str(s['id']) == str(song_id)), track)
            except (ValueError, RuntimeError, OSError) as error:
                raise NoPlayableSource('公开接口没有完整音源。' + str(error)) from error
        if self.account and track.get('account_playable') and track.get('account_id'):
            self._start_official(track)
            return self.current
        raise NoPlayableSource(f'我通过网易云接口没拿到《{name}》的完整音源。可以在网易云 App 里打开这首歌。')

    def _start_official(self, song):
        with self._player_lock:
            if self._closed:
                raise RuntimeError('播放器已退出。')
            self.stop()
            self.account.playback('play', '--song', '--encrypted-id', song['account_id'],
                                  '--original-id', str(song['id']))
            deadline = time.monotonic() + 8
            try:
                while time.monotonic() < deadline:
                    state = self.account.playback('state')
                    if (state.get('status') == 'playing' and state.get('position', 0) > 0
                            and song['name'].strip().casefold() in (state.get('title') or '').casefold()):
                        length = state.get('duration') or 0
                        if 0 < length < (song.get('duration') or 0) - 10:
                            raise NoPlayableSource('网易云官方播放器只提供这首歌的片段。')
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError('网易云接受了播放请求，但本机播放器没有开始播放。')
            except (OSError, RuntimeError, ValueError):
                self.account.playback('stop')
                raise
            self.current = {'id': song['id'], 'name': song['name']}
            self._official_state = state
            stop = self._official_stop = threading.Event()
            threading.Thread(target=self._watch_official, args=(stop,), daemon=True).start()

    def _watch_official(self, stop):
        while not stop.wait(1):
            with self._player_lock:
                if stop.is_set() or self._official_stop is not stop:
                    return
                try:
                    state = self._read_official_state(self._paused)
                except (OSError, RuntimeError, ValueError):
                    # A failed status query does not mean the song ended.
                    continue
                self._official_state = state
                if state.get('status') not in ('playing', 'paused'):
                    return

    def _read_official_state(self, paused):
        state = self.account.playback('state')
        # ncm-cli 0.1.7 reports a paused mpv as stopped, retaining the track
        # and its position. A true stop clears the current track.
        if (paused and state.get('status') == 'stopped' and state.get('position', 0) > 0
                and (self.current or {}).get('name', '').strip() in (state.get('title') or '')):
            return {**state, 'status': 'paused'}
        return state

    def _start_player(self, url, safe, song_id, name):
        progress = None
        control = None
        if os.name == 'nt' and PLAY_SCRIPT.exists():
            sample = self._download(url, safe)
            progress = self.folder / f'penguin-{safe}.position'
            control = self.folder / f'penguin-{uuid.uuid4().hex}.control'
            command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                       '-File', str(PLAY_SCRIPT), '-Path', str(sample), '-ProgressPath', str(progress),
                       '-ControlPath', str(control)]
        else:
            player = shutil.which('afplay') or shutil.which('ffplay')
            if not player:
                raise RuntimeError('没有找到可用的本地音频播放器。')
            sample = self._download(url, safe)
            command = [player, str(sample)] if Path(player).stem == 'afplay' else [player, '-nodisp', '-autoexit', str(sample)]
        with self._player_lock:
            if self._closed:
                raise RuntimeError('播放器已退出。')
            self.stop()
            if progress:
                progress.unlink(missing_ok=True)
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            self.progress_path = progress
            self.control_path = control
            self.started_at = time.monotonic()
            self.current = {'id': song_id, 'name': name}
            if progress:
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline and self.process.poll() is None:
                    if self.position() > 0:
                        return
                    time.sleep(0.05)
                self.stop()
                raise RuntimeError('音频已下载，但 Windows 播放器没有开始播放。')

    def position(self):
        if self._official_stop is not None:
            return float(self._official_state.get('position') or 0)
        progress_path = self.progress_path
        if progress_path:
            try:
                position = int(progress_path.read_text(encoding='ascii')) / 1000
                if progress_path is self.progress_path:
                    self._last_position = position
            except (OSError, ValueError):
                pass
            return self._last_position
        now = self._paused_at if self._paused else time.monotonic()
        return max(0.0, now - self.started_at - self._paused_seconds) if self.started_at else 0.0

    def _control_state(self):
        control_path = self.control_path
        if control_path:
            try:
                return json.loads(control_path.with_suffix('.control.state').read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                pass
        return {}

    def _set_paused(self, paused):
        with self._player_lock:
            if self._official_stop is not None:
                self.account.playback('pause' if paused else 'resume')
                state = self._read_official_state(paused)
                if state.get('status') != ('paused' if paused else 'playing'):
                    raise RuntimeError('网易云播放器没有确认这次暂停或继续操作。')
                self._paused = paused
                self._official_state = state
                return
            if not self.process or self.process.poll() is not None:
                return
            if self.control_path:
                identity = uuid.uuid4().hex
                request = {'id': identity, 'action': 'pause' if paused else 'resume'}
                temporary = self.control_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(request), encoding='utf-8')
                temporary.replace(self.control_path)
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    state = self._control_state()
                    if state.get('id') == identity:
                        if state.get('error'):
                            raise RuntimeError(state['error'])
                        self._paused = state.get('mode') == 'paused'
                        return
                    if self.process.poll() is not None:
                        raise RuntimeError('歌曲已经结束。')
                    time.sleep(0.025)
                raise RuntimeError('播放器没有及时响应，请重试。')
            self.process.send_signal(signal.SIGSTOP if paused else signal.SIGCONT)
            if paused and not self._paused:
                self._paused_at = time.monotonic()
            elif not paused and self._paused:
                self._paused_seconds += time.monotonic() - self._paused_at
            self._paused = paused

    def pause(self):
        self._set_paused(True)

    def resume(self):
        self._set_paused(False)

    def stop(self):
        with self._player_lock:
            if self._official_stop is not None:
                self._official_stop.set()
                self._official_stop = None
                if self._official_state.get('status') in ('playing', 'paused'):
                    self.account.playback('stop')
                self._official_state = {}
            if self.process and self.process.poll() is None:
                if self._paused and os.name != 'nt':
                    self.process.send_signal(signal.SIGCONT)
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None
            self.current = None
            self.started_at = 0
            self.progress_path = None
            if self.control_path:
                self.control_path.unlink(missing_ok=True)
                self.control_path.with_suffix('.control.state').unlink(missing_ok=True)
            self.control_path = None
            self._paused = False
            self._paused_at = 0.0
            self._paused_seconds = 0.0
            self._last_position = 0.0

    def close(self):
        with self._player_lock:
            self._closed = True
            self.stop()

    def state(self):
        if self._official_stop is not None:
            return {'status': self._official_state.get('status', 'stopped'),
                    'title': (self.current or {}).get('name', '')}
        process = self.process
        alive = bool(process and process.poll() is None)
        mode = self._control_state().get('mode')
        paused = mode == 'paused' if mode else self._paused
        return {'status': ('paused' if paused else 'playing') if alive else 'stopped',
                'title': (self.current or {}).get('name', '')}
