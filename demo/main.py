"""Run the local penguin harbor, desktop pet, and optional OPPO event bridge."""

import argparse
from collections import deque
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from demo.companion import Companion
from demo.earbuds import EarbudBridge
from demo.glm import Glm, GlmError, save_key
from demo.memory import Memory
from demo.music_queue import MusicQueue
from demo.netease import NetEase
from demo.netease_account import ACCOUNT_TOOLS, NetEaseAccount, requested_collection, requested_song
from demo.penguin_sprite import png_bytes, read_frames, with_earbuds
from demo.speech import Speech, chat_reply
from demo.thinker import Thinker, TOOLS
from demo.voice import MacVoice, WindowsVoice, synthesize


CONTROL_REQUESTS = {
    '暂停': 'pause', '暂停播放': 'pause', '暂停音乐': 'pause',
    '继续播放': 'resume', '继续放歌': 'resume', '恢复播放': 'resume',
    '下一首': 'next', '切歌': 'next', '换一首': 'next', '播放下一首': 'next',
    '上一首': 'previous', '停止播放': 'stop', '停止音乐': 'stop',
}

CHAT_MUSIC_TOOLS = [
    {'type': 'function', 'function': {
        'name': 'control_music', 'description': '暂停、恢复、停止或切换当前音乐队列。',
        'parameters': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['pause', 'resume', 'stop', 'previous', 'next']}},
            'required': ['action']}}},
    {'type': 'function', 'function': {
        'name': 'search_music', 'description': '只在网易云搜歌，返回歌名和歌手供讨论或选择。',
        'parameters': {'type': 'object', 'properties': {'keyword': {'type': 'string'}},
                       'required': ['keyword']}}},
]


def requested_control(text):
    return CONTROL_REQUESTS.get(text.strip().rstrip('。！!').removesuffix('吧'))


class App:
    def __init__(self, options):
        self.options = options
        self.runtime = Path(options.state_dir or ROOT / '.runtime')
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.pet = Companion(self.runtime / 'penguin-state.json')
        self.frames = read_frames(self.runtime / 'qq-penguin' / 'spritesheet.webp')
        self.thumbnail = png_bytes(self.frames['idle'][0]) if self.frames else None
        self.earbud_thumbnails = {
            f'/assets/penguin-{side}.png': png_bytes(with_earbuds(self.frames['idle'][0], side))
            for side in ('left', 'right', 'both')
        } if self.frames else {}
        self.commands = queue.Queue()
        self.chat_lock = threading.Lock()
        self.pending_chat = deque()
        self.chat_running = False
        self.stopped = threading.Event()
        self.bridge = EarbudBridge(self.pet, self.runtime)
        self.url = f'http://127.0.0.1:{options.port}'
        self.voice = None
        self.server = None
        self.memory = Memory(self.runtime / 'penguin-memory')
        self.glm = Glm()
        queue_path = self.runtime / 'music-queue.json'
        self.music_account = NetEaseAccount()
        self.music = MusicQueue(NetEase(folder=self.runtime / 'penguin-music', account=self.music_account), queue_path)
        if not queue_path.exists():
            for song in self.memory.recent(12, kinds=('music',)):
                if song.get('song_id') is not None and song.get('title'):
                    name, _, artist = song['title'].partition(' - ')
                    self.music.enqueue({'id': song['song_id'], 'name': name, 'artist': artist})
        self.pet.speech = Speech(self.glm, self.memory) if self.glm.ready else None
        self.thinker = Thinker(self.pet, self.memory, self.glm, self.music)

    def start_mind(self):
        if self.options.headless:
            return
        self.music.start()
        self.thinker.start()

    def configure_glm(self, key):
        candidate = Glm(key, model=self.glm.model, timeout=12)
        try:
            candidate.complete([{'role': 'user', 'content': '回复 OK'}], max_tokens=12)
        except GlmError as error:
            raise ValueError('GLM 验证失败。请检查密钥、模型权限和网络，再重试。') from error
        save_key(key)
        self.glm.key = candidate.key
        self.pet.speech = Speech(self.glm, self.memory)
        self.thinker.start()

    def interact(self, action, **fields):
        if action == 'chat':
            with self.chat_lock:
                draft = self.pet.act(action, **{**fields, 'await_reply': True})
                text = draft['turn']['user']
                self.memory.record('chat', text=text)
                if self.glm.ready or requested_control(text) or requested_collection(text) or requested_song(text) is not None:
                    self.pending_chat.append(draft)
                    if not self.chat_running:
                        self.chat_running = True
                        threading.Thread(target=self._drain_chat, daemon=True, name='penguin-chat').start()
                else:
                    self.pet.finish_chat(draft, '还没有配置大模型，配置后可以再发一次。', failed=True)
            return
        else:
            self.pet.act(action, **fields)
            if action != 'knock':
                return
            trigger = '来访者刚敲了敲你'
        threading.Thread(target=self.thinker.think, args=(trigger,), daemon=True).start()

    def _drain_chat(self):
        while True:
            with self.chat_lock:
                if not self.pending_chat or self.stopped.is_set():
                    self.chat_running = False
                    return
                draft = self.pending_chat.popleft()
            try:
                self.reply_to_chat(draft)
            except Exception:
                # Resolve this turn and keep subsequent messages moving.
                self.pet.finish_chat(draft, '这次操作中断了，请再试一次。', failed=True)

    def reply_to_chat(self, draft):
        text = draft['turn']['user']
        control = requested_control(text)
        if control:
            outcome = self.chat_tool('control_music', {'action': control})
            self.pet.finish_chat(draft, outcome.get('message') or outcome.get('error'), failed=not outcome['ok'])
            return
        collection = requested_collection(text)
        song = requested_song(text) if not collection else None
        if collection or song is not None:
            outcome = self.chat_tool(f'play_{collection}_music', {}) if collection else self.chat_tool(
                'play_music', {'keyword': song or '华语流行', 'any_track': not song, 'reason': '你刚点了一首歌'})
            reply = f"正在放《{outcome['title']}》。" if outcome['ok'] else outcome['error']
            self.pet.finish_chat(draft, reply, failed=not outcome['ok'])
            return
        activity, previous = self.pet.chat_context(draft)
        try:
            music_tools = [tool for tool in TOOLS if tool['function']['name'] == 'play_music'] + ACCOUNT_TOOLS + CHAT_MUSIC_TOOLS
            response = chat_reply(self.glm, activity, previous, text,
                                  tools=music_tools, run_tool=self.chat_tool)
        except GlmError:
            self.pet.finish_chat(draft, '这次没收到大模型的回复，可以再发一次。', failed=True)
        else:
            self.pet.finish_chat(draft, response)

    def chat_tool(self, name, fields):
        try:
            if name == 'search_music':
                keyword = str(fields.get('keyword') or '').strip()[:120]
                if not keyword:
                    raise ValueError('请告诉我歌名或歌手。')
                return {'ok': True, 'songs': [{'name': s['name'], 'artist': s['artist']}
                                              for s in self.music.search(keyword)]}
            if name == 'control_music':
                action = fields.get('action')
                if action in ('pause', 'resume'):
                    self.music.set_paused(action == 'pause')
                    message = '已暂停。' if action == 'pause' else '继续播放了。'
                elif action == 'stop':
                    self.music.stop()
                    message = '音乐已停止。'
                elif action in ('next', 'previous'):
                    if not self.music.step(1 if action == 'next' else -1):
                        raise ValueError('队列里这个方向没有可播放的歌曲。')
                    message = f"正在放《{self.music.current['name']}》。"
                else:
                    raise ValueError('未知的播放操作。')
                return {'ok': True, 'message': message}
            if name == 'get_daily_music':
                return self.collection_summary(self.music_account.daily())
            if name == 'get_liked_music':
                return self.collection_summary(self.music_account.liked())
            if name in ('play_daily_music', 'play_liked_music'):
                load = self.music_account.daily if name == 'play_daily_music' else self.music_account.liked
                return self.play_account_music(load, fields.get('song_id'))
            if name != 'play_music':
                raise ValueError('没有这个音乐工具。')
            outcome = self.thinker.play_music(fields, event='来访者刚点了歌')
        except (OSError, ValueError, RuntimeError) as error:
            return {'ok': False, 'error': str(error)}
        if outcome:
            return {'ok': True, 'status': 'playing', **outcome}
        return {'ok': False, 'error': '没有开始播放，请提供歌名、歌手或想听的风格。'}

    @staticmethod
    def collection_summary(collection):
        return {**collection, 'ok': True, 'songs': [
            {key: song[key] for key in ('id', 'name', 'artist') if key in song} for song in collection['songs']]}

    def play_account_music(self, load, song_id=None):
        if not self.memory.behavior()['music_enabled']:
            return {'ok': False, 'error': '音乐功能已关闭。'}
        collection = load()
        songs = collection['songs']
        if song_id is not None:
            songs = [song for song in songs if str(song['id']) == str(song_id)]
            if not songs:
                return {'ok': False, 'error': '本次读取的歌单里没有这首歌。'}
        song = self.music.play_collection(songs)
        title = ' - '.join(filter(None, (song['name'], song['artist'])))
        self.memory.record('music', title=title, song_id=song['id'])
        self.pet.note_music(title, '')
        return {'ok': True, 'status': 'playing', 'title': title}

    def mind_status(self):
        return {'ready': self.thinker.ready, 'glm': self.glm.ready, 'music': self.music.available,
                'thinking': bool(self.thinker.last_run), 'last_error': self.thinker.last_error,
                'last_thought': (self.thinker.last_thought or {}).get('text', ''),
                'behavior': self.memory.behavior()}

    def start_native_voice(self):
        if self.options.headless or self.voice is not None:
            return
        if sys.platform == 'darwin':
            self.voice = MacVoice()
        elif sys.platform == 'win32':
            self.voice = WindowsVoice(self.runtime)
            threading.Thread(target=self.follow_voice, name='penguin-desktop-replies', daemon=True).start()

    def follow_voice(self):
        last_message = None
        enabled = False
        while not self.stopped.wait(0.12):
            state = self.pet.snapshot()
            if enabled and not state['voice']:
                self.voice.cancel()
            enabled = state['voice']
            message = state['message']
            if message['id'] != last_message:
                last_message = message['id']
                if message.get('audible', True) and (enabled or message['preview']):
                    self.voice.say(state['voice_id'], message['speech'])
                else:
                    self.voice.cancel()

    def open_room(self):
        edge = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
        if edge.exists():
            subprocess.Popen([str(edge), '--app=' + self.url, '--window-size=1380,940'])
        else:
            webbrowser.open(self.url)

    def stop(self):
        self.stopped.set()
        self.thinker.stop()
        self.music.close()
        self.bridge.stop.set()
        self.bridge.retry.set()
        if self.voice:
            self.voice.close()
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()


SONG_WORDS = ('放一首', '放个', '放首', '来一首', '来首', '唱一首', '唱首',
              '听一首', '听首', '播放', '点歌', '想听')


def _song_trigger(text, fallback):
    """来访者在留言里点歌时，给思考循环一个明确的放歌信号。"""
    line = (text or '').strip()
    if line and any(word in line for word in SONG_WORDS):
        return f'来访者刚点了歌：「{line}」'
    return fallback


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def __init__(self, *args, app, **kwargs):
        self.app = app
        super().__init__(*args, directory=str(ROOT / 'web'), **kwargs)

    def log_message(self, format, *args):
        if args and str(args[0]).startswith('POST'):
            print(format % args, flush=True)

    def send_json(self, data, status=200):
        encoded = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_asset(self, data, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/state':
            self.send_json({'app': 'lili-attic-v1', **self.app.pet.snapshot(), 'native': not self.app.options.headless,
                            'native_voice': self.app.voice is not None, 'penguin_sprite': bool(self.app.thumbnail),
                            'voice_error': self.app.voice.error if self.app.voice else '',
                            'mind': self.app.mind_status()})
        elif path == '/api/earbuds/events':
            self.stream_earbuds()
        elif path == '/assets/penguin.png' and self.app.thumbnail:
            self.send_asset(self.app.thumbnail, 'image/png')
        elif path in self.app.earbud_thumbnails:
            self.send_asset(self.app.earbud_thumbnails[path], 'image/png')
        elif path.startswith('/api/'):
            self.send_json({'error': '没有这个接口。'}, 404)
        else:
            super().do_GET()

    def stream_earbuds(self):
        origin = self.headers.get('Origin')
        if origin and origin not in (self.app.url, self.app.url.replace('127.0.0.1', 'localhost')):
            self.send_error(403)
            return
        self.connection.settimeout(5)
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.end_headers()
            previous = None
            while not self.app.stopped.is_set():
                update = self.app.pet.wait_for_earbuds(previous, timeout=4)
                if update is None:
                    self.wfile.write(b': heartbeat\n\n')
                else:
                    previous = update['earbud_revision']
                    self.wfile.write(b'data: ' + json.dumps(update, ensure_ascii=False).encode('utf-8') + b'\n\n')
                self.wfile.flush()
        except OSError:
            return

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ('/api/action', '/api/voice'):
            self.send_json({'error': '没有这个接口。'}, 404)
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length < 1 or length > 8192:
                raise ValueError('内容长度不合适。')
            origin = self.headers.get('Origin')
            if origin and origin not in (self.app.url, self.app.url.replace('127.0.0.1', 'localhost')):
                self.send_json({'error': '请从企鹅的本地房间操作。'}, 403)
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('互动数据格式不合适。')
            if path == '/api/voice':
                self.play_voice(data)
                return
            action = data.pop('action', None)
            if action in ('show_pet', 'hide_pet'):
                self.app.commands.put(action)
            elif action == 'connect':
                if self.app.options.no_bluetooth:
                    raise ValueError('本次启动关闭了蓝牙，可在正常启动时连接。')
                self.app.bridge.retry.set()
            elif action == 'simulate':
                self.app.pet.simulate_case(data.get('value'))
            elif action == 'quit':
                self.send_json({'ok': True})
                self.app.stop()
                return
            else:
                self.app.interact(action, **data)
            self.send_json(self.app.pet.snapshot())
        except (ValueError, TypeError) as error:
            self.send_json({'error': str(error)}, 400)
        except OSError:
            self.send_json({'error': '这次记录未保存成功，请检查项目文件夹是否可写。'}, 500)


    def play_voice(self, data):
        state = self.app.pet.snapshot()
        message = state['message']
        if self.app.voice or data.get('id') != message['id'] or not message.get('audible', True) or not (state['voice'] or message['preview']):
            self.send_json({'error': '这句台词已结束。'}, 409)
            return
        try:
            audio = synthesize(state['voice_id'], message['speech'])
            if not audio:
                raise ValueError('No speech audio')
        except Exception:
            self.send_json({'error': '神经语音暂时连不上，请检查网络后重试。'}, 503)
            return
        self.send_asset(audio, 'audio/wav' if audio.startswith(b'RIFF') else 'audio/mpeg')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18765)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--no-bluetooth', action='store_true')
    parser.add_argument('--state-dir')
    options = parser.parse_args()
    app = App(options)
    try:
        app.server = ThreadingHTTPServer(('127.0.0.1', options.port), partial(Handler, app=app))
    except OSError:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(app.url + '/api/state', timeout=2) as response:
            existing = json.load(response)
        if existing.get('app') != 'lili-attic-v1':
            raise RuntimeError('端口正由其他应用使用，请加 --port 指定另一个端口。')
        request = urllib.request.Request(app.url + '/api/action', data=b'{"action":"show_pet"}', headers={'Content-Type': 'application/json'})
        opener.open(request, timeout=2).close()
        app.open_room()
        return
    app.start_native_voice()
    app.start_mind()
    threading.Thread(target=app.server.serve_forever, name='lili-http', daemon=True).start()
    print('Penguin Harbor is ready at ' + app.url, flush=True)
    if not options.no_bluetooth:
        app.bridge.start()
    if not options.no_open:
        app.open_room()
    try:
        if options.headless:
            app.stopped.wait()
        else:
            if sys.platform == 'darwin':
                from demo.desktop_macos import DesktopPet
            else:
                from demo.desktop import DesktopPet
            DesktopPet(app).run()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        app.server.server_close()


if __name__ == '__main__':
    main()
