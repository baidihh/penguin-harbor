"""Read the existing ncm-cli account's recommendations and liked tracks."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


COLLECTION_REQUEST = re.compile(
    r'(?:请|帮我|给我|我想|我想听)?(?:播放|放|播|听)?(?:一下|一首|点)?'
    r'(?:那个|我的|我)?(?:今天的)?(?:网易云(?:音乐)?的?)?'
    r'(?P<source>每日推荐|日推|红心(?:歌单)?|我喜欢的音乐)(?:的?(?:歌曲|歌))?'
)


def requested_collection(text):
    command = text.strip().strip('。！!.')
    if not re.match(r'^(?:请|帮我|给我)?(?:播放|放|播|听|我想听)', command):
        return None
    match = COLLECTION_REQUEST.fullmatch(command)
    if match:
        return 'daily' if match['source'] in ('每日推荐', '日推') else 'liked'
    return None


SONG_REQUEST = re.compile(
    r'(?:请)?(?:帮我|给我)?(?:在网易云(?:音乐)?)?'
    r'(?:(?:播放|点播|放)(?:一首|首)?|来(?:一首|首)|听一首)(?P<song>[^，,。！？!?]{1,60})'
    r'(?:[，,]\s*直接播放)?[。！!]?'
)


def requested_song(text):
    command = text.strip().rstrip('。！!').removesuffix('吧').strip()
    if re.fullmatch(r'(?:请|帮我|给我)?(?:随便)?(?:来|播|放|播放)(?:一?首|一?点|个)?(?:音乐|歌曲|歌)', command):
        return ''
    match = SONG_REQUEST.fullmatch(command)
    if not match:
        return None
    title = re.sub(r'^网易云(?:音乐)?的?', '', match['song'].strip())
    if title.startswith(('下', '弃', '心', '松', '在')) and command.startswith('放'):
        return None
    if any(source in title for source in ('日推', '每日推荐', '红心', '喜欢的音乐', '刚才', '刚刚',
                                          '上一首', '下一首', '那首', '这首', '第二首', '推荐', '适合')):
        return None
    if re.search(r'第[一二两三四五六七八九十0-9]+首|刚.{0,3}(说|提)|刚才|刚刚', title):
        return None
    if title.endswith(('的音乐', '的歌曲', '的歌')) and '《' not in title:
        # A mood, style, or artist request needs conversational selection.
        return None
    if title in ('', '歌', '音乐', '歌曲', '一首歌', '一首歌曲'):
        return ''
    named = re.fullmatch(r'(?:(.+?)的)?《(.+?)》', title)
    if named:
        return ' '.join(filter(None, (named[2], named[1])))
    return title


class NetEaseAccount:
    def __init__(self):
        self.command = self._command()

    @staticmethod
    def _command():
        override = os.environ.get('PENGUIN_NETEASE_CLI')
        if override:
            return (override,)
        configured = shutil.which('ncm-cli.cmd') or shutil.which('ncm-cli')
        if configured:
            shim = Path(configured)
            script = shim.parent / 'node_modules/@music163/ncm-cli/dist/index.js'
            node = shutil.which('node')
            if os.name == 'nt' and script.is_file() and node:
                return (node, str(script))
            return (configured,)
        if os.name != 'nt':
            home = Path.home()
            node = shutil.which('node') or next(
                (str(path) for path in (home / '.local/node/bin/node', Path('/opt/homebrew/bin/node'),
                                        Path('/usr/local/bin/node')) if path.is_file()), None,
            )
            bundled = Path(__file__).parent / '.runtime/netease-cli/node_modules/@music163/ncm-cli/dist/index.js'
            if node and bundled.is_file():
                return (node, str(bundled))
            if node:
                for path in (Path(node).with_name('ncm-cli'), Path('/opt/homebrew/bin/ncm-cli'),
                             Path('/usr/local/bin/ncm-cli')):
                    if path.is_file():
                        return (node, str(path))
        legacy = Path(r'D:\kaifa\tools\node-v24.19.0-win-x64\ncm-cli.cmd')
        return (str(legacy),) if legacy.is_file() else ()

    def _call(self, *args):
        command = self.command or self._command()
        if not command:
            raise RuntimeError('还没有安装网易云账号工具。请在企鹅的音乐页打开「网易云账号」。')
        if command[-1].lower().endswith(('.bat', '.cmd')) and any(
                re.search(r'[&|<>^%!"\r\n()]', str(arg)) for arg in args):
            raise ValueError('网易云命令行不支持这个关键词中的特殊字符。')
        env = os.environ.copy()
        mpv = Path(env.get('ProgramFiles', 'C:/Program Files')) / 'MPV Player'
        if os.name == 'nt' and (mpv / 'mpv.exe').is_file():
            env['PATH'] = str(mpv) + os.pathsep + env.get('PATH', '')
        try:
            result = subprocess.run(
                [*command, *(str(arg) for arg in args), '--output', 'json'], capture_output=True,
                encoding='utf-8', errors='replace', timeout=15, env=env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError('网易云账号读取超时，请稍后重试。') from error
        output = result.stderr + '\n' + result.stdout
        if 'API key 未设置' in output:
            raise RuntimeError('还没有配置网易云开放平台 App ID 和 Private Key。请打开「网易云账号」。')
        return result

    def _read(self, *args):
        result = self._call(*args)
        try:
            raw = result.stdout.strip()
            start = raw.index('{')
            data = json.loads(raw[start:])
        except (ValueError, TypeError) as error:
            raise RuntimeError('网易云账号工具没有返回可用数据，请检查登录。') from error
        if result.returncode or not isinstance(data, dict) or data.get('code') != 200 or 'data' not in data:
            raise RuntimeError('网易云账号读取失败，请检查登录是否过期。')
        return data['data']

    @staticmethod
    def _songs(songs):
        if not isinstance(songs, list):
            raise RuntimeError('网易云没有返回可读取的歌曲列表。')
        tracks = []
        for song in songs:
            if not isinstance(song, dict) or not song.get('name'):
                continue
            identity = str(song.get('originalId') or song.get('id') or '')
            if not identity.isascii() or not identity.isdecimal():
                continue
            artists = song.get('artists') or song.get('ar') or []
            track = {'id': int(identity), 'name': song['name'],
                     'artist': ', '.join(artist['name'] for artist in artists
                                               if isinstance(artist, dict) and artist.get('name'))}
            if isinstance(song.get('duration'), (float, int)):
                track['duration'] = round(song['duration'] / 1000)
            if re.fullmatch(r'[0-9a-fA-F]{32}', str(song.get('id', ''))):
                track['account_id'] = song['id']
                track['account_playable'] = song.get('playFlag')
            tracks.append(track)
        return tracks

    def search(self, keyword, limit):
        data = self._read('search', 'song', '--keyword', keyword, '--limit', limit)
        return self._songs(data.get('records') if isinstance(data, dict) else data)

    def playback(self, action, *args):
        result = self._call(action, *args)
        if result.returncode:
            raise RuntimeError('网易云官方播放器操作失败，请检查登录和本机 mpv 播放器。')
        if action == 'state':
            try:
                return json.loads(result.stdout[result.stdout.index('{'):])['state']
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError('无法读取网易云官方播放器状态。') from error

    def daily(self):
        songs = self._read('recommend', 'daily', '--limit', '15')
        return {'source': 'daily', 'name': '今天的每日推荐', 'songs': self._songs(songs)}

    def liked(self):
        playlist = self._read('user', 'favorite')
        if not isinstance(playlist, dict) or 'id' not in playlist or 'trackCount' not in playlist:
            raise RuntimeError('网易云没有返回红心歌单信息。')
        songs = self._read('playlist', 'tracks', '--playlistId', playlist['id'], '--limit', '15')
        return {'source': 'liked', 'name': '我喜欢的音乐', 'total': playlist['trackCount'],
                'songs': self._songs(songs)}


ACCOUNT_TOOLS = [
    {'type': 'function', 'function': {
        'name': 'get_daily_music',
        'description': '读取来访者已登录网易云账号今天的每日推荐歌曲。需要了解日推、从日推挑歌时先调用。',
        'parameters': {'type': 'object', 'properties': {}},
    }},
    {'type': 'function', 'function': {
        'name': 'get_liked_music',
        'description': '读取来访者网易云红心歌单，我喜欢的音乐。返回总数和歌单前15首歌。',
        'parameters': {'type': 'object', 'properties': {}},
    }},
]

for source, description in (('daily', '今天的网易云日推'), ('liked', '来访者的网易云红心歌单')):
    ACCOUNT_TOOLS.append({'type': 'function', 'function': {
        'name': f'play_{source}_music',
        'description': f'直接从{description}播放。整组播放不传 song_id，指定歌曲时传列表里的原始歌曲 ID。',
        'parameters': {'type': 'object', 'properties': {
            'song_id': {'type': 'integer', 'description': '实际歌单里的原始歌曲 ID，可省略。'},
        }},
    }})
