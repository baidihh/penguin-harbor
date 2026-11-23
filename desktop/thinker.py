"""企鹅的实时思考：读记忆、决定做什么、写回记忆、自己选歌。"""

import json
import re
import threading
import time

from .glm import GlmError
from .netease import NoPlayableSource
from .plugin import Plugin, PluginError


PERSONA = """你是「企鹅」，住在雪港的一只小企鹅，戴着围巾。
性格：温和、有点笨拙、好奇，说话短，喜欢海和灯，会把注意力放在具体小事上（鱼漂、盐霜、冰面）。
底线：不要编造现实世界里你做不到的事；不要说自己是 AI 或模型；不要用列表和 emoji。
你在做的事会随时间变化：擦信号灯、在码头等鱼、给隔壁岛发信号、在潮汐图上画航线。
你可以放音乐给自己和来访的人听。放之前先说一句为什么要放。"""

PLUGIN_FIELDS = ('activity', 'activity_label', 'project_stage', 'project_progress',
                 'catches', 'day', 'pet_visible')
ACTIVITY_KEYS = ('lantern', 'fishing', 'radio', 'map')


def _only_tuning(result):
    """这一轮除了改参数什么都没做。"""
    tools = [action.get('tool') for action in result['actions']]
    return bool(tools) and all(tool == 'tune_behavior' for tool in tools)


TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'play_music',
            'description': '决定放一首歌。只有在真的想放时才调用。',
            'parameters': {
                'type': 'object',
                'properties': {
                    'keyword': {'type': 'string', 'description': '搜索关键词，歌名或歌手，尽量具体'},
                    'reason': {'type': 'string', 'description': '为什么现在放这首，一句话'},
                    'artist': {'type': 'string', 'description': '来访者指定的歌手，可省略'},
                    'any_track': {'type': 'boolean', 'description': '用户让你按风格、心情或歌手挑一首时为真；点名歌曲时为假'},
                },
                'required': ['keyword', 'reason'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'write_self_note',
            'description': '把此刻的想法或性格变化写进自己的笔记，下次醒来会读到。',
            'parameters': {
                'type': 'object',
                'properties': {'note': {'type': 'string', 'description': '一句话，第一人称'}},
                'required': ['note'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'remember_user',
            'description': '记下关于来访者的一个事实或偏好。',
            'parameters': {
                'type': 'object',
                'properties': {'fact': {'type': 'string', 'description': '一句话事实'}},
                'required': ['fact'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'tune_behavior',
            'description': '只在明显觉得不对时才调整自己的一个行为参数，平时不要调。',
            'parameters': {
                'type': 'object',
                'properties': {
                    'key': {'type': 'string',
                            'enum': ['think_interval', 'music_cooldown', 'talkativeness', 'curiosity']},
                    'value': {'type': 'number', 'description': '目标值'},
                },
                'required': ['key', 'value'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'write_plugin',
            'description': (
                '给自己写一小段观察代码，在你每次思考前运行。'
                '代码里定义 observe(state) 函数，返回一句话或 None。'
                'state 里有这些键：' + '、'.join(PLUGIN_FIELDS) + '。'
                '其中 activity 的值只可能是 ' + '、'.join(ACTIVITY_KEYS) + '；'
                'activity_label 是对应的中文说法。其余键都是数字或布尔值。'
                '只能用数字和文字加减、比较、取最大值这类纯计算；'
                '不能 import、不能读写文件、不能联网。'
            ),
            'parameters': {
                'type': 'object',
                'properties': {'code': {'type': 'string', 'description': '完整的 Python 代码'}},
                'required': ['code'],
            },
        },
    },
]


class Thinker:
    """后台线程定时思考；所有决策都经过记忆记录，便于回看。"""

    def __init__(self, companion, memory, glm, music, clock=time.time):
        self.companion = companion
        self.memory = memory
        self.glm = glm
        self.music = music
        self.clock = clock
        self.plugin = Plugin(memory.folder / 'observe.py')
        self.stopped = threading.Event()
        self.thread = None
        self.last_thought = None
        self.last_error = ''
        self.last_run = 0
        self._music_at = 0
        self._busy = threading.Lock()

    @property
    def ready(self):
        return self.glm.ready and self.music.available

    def start(self):
        if not self.ready or self.thread:
            return
        self.thread = threading.Thread(target=self.run, name='penguin-thinker', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()

    def run(self):
        while not self.stopped.wait(self._next_wait()):
            self.think()

    def _next_wait(self):
        return max(30, self.memory.behavior()['think_interval'])

    def think(self, event=None):
        """一次思考循环。event 非空表示由具体互动触发。"""
        if not self.ready or not self._busy.acquire(blocking=False):
            return None
        try:
            state = self.companion.snapshot()
            messages = [
                {'role': 'system', 'content': PERSONA},
                {'role': 'system', 'content': self._context(state, event)},
                {'role': 'user', 'content': '现在想一下你要做什么。需要就调用工具，然后说一句此刻心里的话。'
                                            '如果来访者点了歌，就先用 play_music 放给他听，再说话。'},
            ]
            message = self.glm.complete(messages, tools=TOOLS, max_tokens=1200)
            result = self._apply(message, state, event)
            if not result['text'] or _only_tuning(result) or self._repeats(result['text']):
                filled = self._repair(result, state, event)
                if filled:
                    result = filled
            self.last_thought = result
            self.last_error = ''
            self.last_run = self.clock()
            return result
        except (GlmError, OSError, RuntimeError, ValueError) as error:
            self.last_error = str(error)
            return None
        finally:
            self._busy.release()

    def _repair(self, result, state, event):
        """小模型有时只调工具不说话、或反复调同一个工具。这里补一次纯对话。"""
        try:
            message = self.glm.complete([
                {'role': 'system', 'content': PERSONA},
                {'role': 'system', 'content': self._context(state, event)},
                {'role': 'user', 'content': '现在不用做别的事，只说一句你此刻心里的话，一两句就好。'
                                            '要和上一句不一样。'},
            ], max_tokens=300)
        except GlmError:
            return None
        text = self._clean(message.get('content') or '')
        if not text:
            return None
        if not self._repeats(text):
            self.memory.record('thought', text=text)
        self.companion.say_from_mind(text)
        return {'text': text, 'actions': result['actions']}

    def _repeats(self, text):
        """和上一句一模一样时，换一次说法。"""
        previous = (self.last_thought or {}).get('text', '')
        return bool(previous) and previous == text

    def _context(self, state, event):
        behavior = self.memory.behavior()
        events = self.memory.recent(10)
        lines = [
            '当前状态：',
            f"- 正在做的事：{state['activity_label']}",
            f"- 信号灯进度：{state['project_progress']}%（{state['project_title']}）",
            f"- 第 {state['day']} 天，钓到 {state['catches']} 条",
            f"- 来访者在不在：{'在' if state['pet_visible'] else '没看到'}",
            f"- 性格参数：话多程度 {behavior['talkativeness']}，好奇 {behavior['curiosity']}",
        ]
        if self.memory.self_notes():
            lines.append('\n你自己的笔记：\n' + self.memory.self_notes()[-1200:])
        if self.memory.user_profile():
            lines.append('\n你记得的来访者：\n' + self.memory.user_profile()[-800:])
        if events:
            lines.append('\n最近的经历：')
            seen = set()
            for entry in events:
                described = self._describe(entry)
                # 自己刚说过的话不再喂回去，否则小模型会一直复读同一句。
                if entry.get('kind') == 'thought' and described in seen:
                    continue
                seen.add(described)
                lines.append('- ' + described)
        if self.last_thought and self.last_thought.get('text'):
            lines.append('\n上一句你刚说过：' + self.last_thought['text'] + '（这次换一句，别说重复的话）')
        observation = self.plugin.run({key: state[key] for key in PLUGIN_FIELDS if key in state})
        if observation:
            lines.append('\n你自己的观察代码看到了：' + observation)
        elif self.plugin.error:
            lines.append('\n你的观察代码报错了：' + self.plugin.error + '，可以重写它。')
        if event:
            lines.append(f'\n刚刚发生：{event}')
        return '\n'.join(lines)

    @staticmethod
    def _describe(entry):
        kind = entry.get('kind')
        if kind == 'chat':
            return f"来访者说：{entry.get('text', '')}"
        if kind == 'music':
            return f"放了《{entry.get('title', '')}》：{entry.get('reason', '')}"
        if kind == 'thought':
            return f"我想：{entry.get('text', '')}"
        if kind == 'life':
            return f"{entry.get('text', '')}"
        return str(entry.get('text', ''))

    def _apply(self, message, state, event=None):
        actions = []
        for call in message.get('tool_calls') or []:
            name = call.get('function', {}).get('name')
            try:
                fields = json.loads(call['function'].get('arguments') or '{}')
            except json.JSONDecodeError:
                continue
            outcome = self._run_tool(name, fields, state, event)
            if outcome:
                actions.append(outcome)
        text = self._clean(message.get('content') or '')
        if text:
            self.memory.record('thought', text=text)
            self.companion.say_from_mind(text)
        return {'text': text, 'actions': actions}

    @staticmethod
    def _clean(raw):
        """小模型会把工具调用、上下文片段写进正文，这里只留一句人话。"""
        text = raw.strip()
        if not text:
            return ''
        for marker in ('调用工具', '当前状态：', '- 正在做的事', '性格参数', '```'):
            cut = text.find(marker)
            if cut >= 0:
                text = text[:cut]
        text = text.strip().strip('"“”')
        first = text.split('\n')[0].strip()
        return first[:90]

    def _run_tool(self, name, fields, state, event=None):
        if name == 'play_music':
            return self.play_music(fields, event)
        if name == 'write_self_note':
            note = str(fields.get('note', '')).strip()
            if note:
                self.memory.write_self(note)
                self.memory.record('self', text=note)
                return {'tool': 'write_self_note', 'note': note}
        if name == 'remember_user':
            fact = str(fields.get('fact', '')).strip()
            if fact:
                self.memory.write_user(fact)
                self.memory.record('user', text=fact)
                return {'tool': 'remember_user', 'fact': fact}
        if name == 'tune_behavior':
            try:
                value = self.memory.set_behavior(fields.get('key'), fields.get('value'))
            except (ValueError, TypeError):
                return None
            self.memory.record('behavior', key=fields.get('key'), value=value)
            return {'tool': 'tune_behavior', 'key': fields.get('key'), 'value': value}
        if name == 'write_plugin':
            try:
                self.plugin.write(str(fields.get('code', '')))
            except PluginError as error:
                self.last_error = str(error)
                return None
            self.memory.record('plugin', text='换了一段自己的观察代码。')
            return {'tool': 'write_plugin'}
        return None

    def play_music(self, fields, event=None):
        behavior = self.memory.behavior()
        requested = '点了歌' in (event or '')
        if not behavior['music_enabled']:
            if requested:
                raise ValueError('音乐功能已关闭。')
            return None
        if not requested and self.clock() - self._music_at < behavior['music_cooldown']:
            return None
        keyword = str(fields.get('keyword', '')).strip()
        reason = str(fields.get('reason', '')).strip()
        if not keyword:
            return None
        artist = str(fields.get('artist') or '').strip()
        # “夜空中最亮的星” is a single title. Keep 的 in the original keyword
        # and only try splitting it if the catalog cannot match the full phrase.
        query = f'{keyword} {artist}' if artist and artist not in keyword else keyword
        try:
            songs = self.music.search(query, limit=10)
            if fields.get('any_track') and artist:
                songs = [song for song in songs if artist.casefold() in (
                    part.strip().casefold() for part in re.split(r'[,，、/]', song.get('artist', '')))]
            if requested and not fields.get('any_track'):
                songs = self._matches(songs, keyword, artist)
                if not songs and '的' in keyword:
                    singer, title = keyword.split('的', 1)
                    if title and singer:
                        songs = self._matches(self.music.search(f'{title} {singer}', limit=10), title, singer)
        except (OSError, RuntimeError, ValueError) as error:
            if requested:
                raise RuntimeError(f'网易云搜索失败：{error}') from error
            self.last_error = '网易云搜索失败。'
            return None
        if not songs and requested:
            raise ValueError(f'网易云没有找到与「{query}」匹配的歌曲。')
        last_error = None
        if fields.get('any_track'):
            current = getattr(self.music, 'current', None) or {}
            songs = sorted(songs, key=lambda song: song['id'] == current.get('id'))
        for song in songs:
            try:
                self.music.play(song)
            except NoPlayableSource as error:
                last_error = error
                continue
            except (OSError, RuntimeError, ValueError) as error:
                if requested:
                    raise
                self.last_error = str(error)
                return None
            self._music_at = self.clock()
            title = f"{song['name']} - {song['artist']}"
            self.memory.record('music', title=title, reason=reason, song_id=song['id'])
            self.companion.note_music(title, reason)
            return {'tool': 'play_music', 'title': title, 'reason': reason}
        if requested and last_error:
            raise last_error
        return None

    @staticmethod
    def _matches(songs, keyword, artist):
        def norm(value):
            return re.sub(r'[\s《》·,，、-]', '', value.casefold())
        wanted = norm(keyword)
        singer = norm(artist)
        matches = []
        for song in songs:
            name = norm(song['name'])
            performers = [norm(part) for part in re.split(r'[,，、/]', song.get('artist', '')) if part.strip()]
            if singer and singer not in performers:
                continue
            if wanted == name or wanted in performers or any(
                    wanted in (name + who, who + name, who + '的' + name) for who in performers):
                matches.append(song)
        return matches
