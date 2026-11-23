"""Persistent, local story simulation for the penguin and the harbor."""

from copy import deepcopy
import json
import math
from pathlib import Path
import threading
import time
from .case_life import CaseLife, TOUCH_LABELS


from .voice import DEFAULT_VOICE, PREVIEW_LINE, VOICES, VOICE_PREVIEWS


PROJECT_STAGES = [
    ('捡回熄掉的灯', '昨晚风太大，旧信号灯灭掉啦。海上还有船，我得想办法让大家看清回来的路。'),
    ('看潮位，找灯位', '我沿着岸边走了一圈。码头靠船近，风崖照得远。你来帮我挑一个嘛？'),
    ('换一块透明灯罩', '灯罩修好啦！上面有一道小划痕，照出来像一颗歪歪的小星星。'),
    ('等第一艘船经过', '现在就差一次试灯。雾好大哦，你看到海上闪了一下吗？'),
    ('今晚的灯亮了', '有船朝我们闪了两下！我数得一清二楚，它看见这边的灯啦。'),
]

ACTIVITIES = [
    ('lantern', '在擦信号灯', '用围巾角擦掉灯罩上的盐霜，回过神时围巾已经湿了一截。'),
    ('fishing', '蹲在码头等鱼', '鱼漂动了两次。第一次是鱼，第二次是自己把线踩住了。'),
    ('radio', '给隔壁岛发信号', '把岸边的浪声发给隔壁岛。对面回了三下短哨。'),
    ('map', '在潮汐图上画航线', '标出一片冬天也很平静的水面，打算下次带朋友去滑冰。'),
]

SIGNAL_SITES = {
    'pier': ('码头', '灯沿着水面照过去。进港的小船，一拐弯就能看见。'),
    'cliff': ('风崖', '灯从风崖上照得好远。还没到港的船，也知道方向啦。'),
}
CATCHES = [
    ('银鱼', '有了！一条银鱼。哇，它一甩尾巴，水都溅到我脸上啦。'),
    ('小鳕鱼', '拉到了！是条小鳕鱼。太小啦，我们放它回去，好不好？'),
    ('旧信号旗', '咦，钓起来一面旧信号旗。晒干挂在码头，风一吹就有声音了。'),
]

class Companion:
    def __init__(self, state_path, clock=time.time):
        self.path = Path(state_path)
        self.clock = clock
        self.lock = threading.RLock()
        self.earbud_changed = threading.Condition(self.lock)
        self.earbud_revision = time.monotonic_ns() // 1000
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        else:
            self.data = {
                'version': 2, 'created': clock(), 'offset': 0, 'project_stage': 0,
                'room_choice': None, 'memories': [], 'journal': [], 'voice': False,
                'harbor_collection': [],
            }
        self.data.setdefault('pet_scale', 1.0)
        self.data.setdefault('voice_id', DEFAULT_VOICE)
        self.data.setdefault('catches', 0)
        self.data.setdefault('last_fishing_tide', -1)
        self.data.setdefault('conversation', [])
        for turn in self.data['conversation']:
            if turn.get('reply_state') == 'pending':
                turn.update(reply='上次回复中断了，可以再发一次。', reply_state='failed', reply_time=self.clock())
        self.case_life = CaseLife(self.data.setdefault('case_home', {}), clock)
        self.case_demo = None
        self.device = {'status': 'disconnected', 'detail': '等待连接 OPPO Enco Air5 Pro', 'worn': None,
                       'estimated_lid': 'unknown', 'battery': {}, 'last_touch': None}
        self.message_id = 0
        self.message = None
        self.pet_visible = False
        self.speech = None
        self._last_activity = None
        self._sync()
        greeting = '欸，你来啦。昨晚风把信号灯吹灭了。要陪我在天黑前把它点起来吗？'
        if self.data['project_stage'] == 4:
            greeting = '你回来了呀。外面风好冷，来这边坐。' + self._project_line()
        self._say(greeting, '见面')
        self._save()

    def _save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def _journal(self, text, kind='life'):
        next_id = self.data['journal'][-1]['id'] + 1 if self.data['journal'] else 1
        self.data['journal'].append({'text': text, 'kind': kind, 'time': self.clock(), 'id': next_id})
        self.data['journal'] = self.data['journal'][-100:]

    def _sync(self):
        elapsed = max(0, self.clock() - self.data['created'] + self.data['offset'])
        stage = min(4, int(elapsed // 180))
        changed = False
        for index in range(self.data['project_stage'] + 1, stage + 1):
            self._journal(PROJECT_STAGES[index][0] + '。' + PROJECT_STAGES[index][1], 'project')
            changed = True
        self.data['project_stage'] = max(stage, self.data['project_stage'])
        activity_index = int(elapsed // 90)
        if self._last_activity is not None and activity_index != self._last_activity:
            _, activity, note = ACTIVITIES[activity_index % len(ACTIVITIES)]
            self._journal(activity, 'activity')
            changed = True
            if not any(item['text'] == note for item in self.data['journal']):
                self._journal(note)
        self._last_activity = activity_index
        if changed:
            self._save()
        return elapsed

    def _say(self, text, topic='聊一会儿', choices=None, *, speech=None, preview=False, scene=None, detail='', audible=True):
        if self.speech is not None and speech is None and not preview:
            text = self.speech.line(scene or topic, text, detail)
        self.message_id += 1
        self.message = {'id': self.message_id, 'text': text, 'topic': topic,
                        'choices': choices or [], 'time': self.clock(),
                        'speech': text if speech is None else speech, 'preview': preview,
                        'audible': audible and (preview or (self.case_demo or self.case_life).phase != 'sleeping')}
        return deepcopy(self.message)

    def say_from_mind(self, text, topic='自言自语'):
        """思考循环产出的台词，直接替换当前消息。"""
        with self.lock:
            at_home = (self.case_demo or self.case_life).phase in ('home', 'sleeping')
            self._journal(text, 'thought')
            self._save()
            return self._say(text, topic, speech=text, audible=not at_home)

    def note_music(self, title, reason):
        """记录企鹅自己放了歌，让页面能显示。"""
        with self.lock:
            self.data.setdefault('music', [])
            self.data['music'] = (self.data['music'] + [{'title': title, 'reason': reason,
                                                         'time': self.clock()}])[-20:]
            self._journal('给自己放了《' + title + '》。' + reason)
            self._save()

    def set_mac_caption_position(self, position):
        with self.lock:
            if position is None:
                self.data.pop('mac_caption_position', None)
            else:
                self.data['mac_caption_position'] = list(position)
            self._save()

    def _timeline(self):
        rows = []
        turns = self.data['conversation']
        legacy_messages = {}
        for turn in turns:
            legacy_messages.setdefault(turn['user'], []).append(turn['time'])
        for entry in self.data['journal']:
            text = entry['text']
            if entry['kind'] == 'together' and text.startswith('你留下了一句话，'):
                text = text.removeprefix('你留下了一句话，')
                if any(abs(entry['time'] - t) < 1 for t in legacy_messages.get(text, ())):
                    continue
                label = '你'
            else:
                label = '企鹅心声' if entry['kind'] == 'thought' else '企鹅动态'
            rows.append({'time': entry['time'], 'label': label, 'text': text})
        for turn in turns:
            rows.append({'time': turn['time'], 'label': '你', 'text': turn['user']})
            if turn.get('reply'):
                rows.append({'time': turn.get('reply_time', turn['time']), 'label': '企鹅',
                             'text': turn['reply']})
        life = self.case_life.snapshot()
        if life['source'] == 'device':
            rows.extend({'time': e['time'], 'label': '耳机互动', 'text': e['label']}
                        for e in life['events']
                        if e['kind'] not in ('lid_opened', 'lid_closed', 'taken_out', 'returned'))
        return sorted(rows, key=lambda row: row['time'])

    def snapshot(self):
        with self.lock:
            elapsed = self._sync()
            activity = ACTIVITIES[int(elapsed // 90) % len(ACTIVITIES)]
            result = deepcopy(self.data)
            result.update({'timeline': self._timeline(), 'activity': activity[0], 'activity_label': activity[1],
                           'project_title': PROJECT_STAGES[self.data['project_stage']][0],
                           'voice_options': {key: voice[0] for key, voice in VOICES.items()},
                           'project_progress': min(100, round(elapsed / 720 * 100)),
                           'day': int(elapsed // 86400) + 1, 'message': deepcopy(self.message),
                           'pet_visible': self.pet_visible, **self._earbud_snapshot()})
            return result

    def _earbud_snapshot(self):
        return {'earbud_revision': self.earbud_revision, 'device': deepcopy(self.device),
                'case_companion': (self.case_demo or self.case_life).snapshot()}

    def _publish_earbuds(self):
        self.earbud_revision = max(self.earbud_revision + 1, time.monotonic_ns() // 1000)
        self.earbud_changed.notify_all()

    def wait_for_earbuds(self, previous, timeout):
        with self.earbud_changed:
            if self.earbud_changed.wait_for(lambda: self.earbud_revision != previous, timeout):
                return self._earbud_snapshot()
            return None

    def act(self, action, **fields):
        with self.lock:
            if action == 'chat' and (not isinstance(fields.get('text'), str) or not fields['text'].strip()):
                raise ValueError('先写一句想告诉企鹅的话。')
            if action == 'choose' and fields.get('value') not in SIGNAL_SITES:
                raise ValueError('请选择码头或风崖。')
            if action == 'voice_select' and (not isinstance(fields.get('value'), str) or fields['value'] not in VOICES):
                raise ValueError('请选择一种可用音色。')
            if action == 'scale':
                value = fields.get('value')
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.5 <= value <= 2:
                    raise ValueError('精灵大小可以设为 50% 到 200%。')
            handlers = {
                'knock': self._knock, 'project': self._project, 'harbor_sound': self._harbor_sound,
                'fish': self._fish, 'chat': self._chat, 'recall': self._recall,
                'choose': self._choose, 'advance': self._advance, 'voice': self._voice,
                'scale': self._scale, 'voice_select': self._voice_select, 'voice_preview': self._voice_preview,
            }
            if action not in handlers:
                raise ValueError('暂时还没有这个互动。')
            self._sync()
            result = handlers[action](fields)
            self._save()
            return result

    def _signal_choices(self):
        return [{'label': '选' + label, 'value': value} for value, (label, _) in SIGNAL_SITES.items()]

    def _project_line(self):
        stage = self.data['project_stage']
        line = PROJECT_STAGES[stage][1]
        if stage == 4:
            _, outcome = SIGNAL_SITES[self.data['room_choice'] or 'pier']
            line += outcome
        return line

    def _knock(self, fields):
        activity = self.snapshot()['activity']
        if activity == 'lantern':
            text = '欸，你来啦！这层盐霜我擦好久了，差一点拿围巾当抹布。'
            return self._say(text, '企鹅转过头', self._signal_choices(), speech=text if fields.get('instant') else None)
        lines = {
            'fishing': '你来得刚好。帮我看一下鱼漂喔，我的脚都蹲麻了。选「钓一竿」试试看？',
            'radio': '嘘，隔壁岛在打招呼！你听，两短一长。很像我饿的时候敲饭盒。',
            'map': '欸，你看这儿，冰面滑到一半会绕回来。我给你画条路，下次一起去。',
        }
        return self._say(lines[activity], '企鹅转过头', speech=lines[activity] if fields.get('instant') else None)

    def _project(self, fields):
        stage = self.data['project_stage']
        text = self._project_line()
        return self._say(text, PROJECT_STAGES[stage][0], self._signal_choices(), speech=text if fields.get('instant') else None)

    def _harbor_sound(self, fields):
        collection = self.data['harbor_collection']
        if not collection:
            collection.append('退潮时的港湾')
            self._journal('和你一起听了退潮，浪花把小石头推来推去。', 'together')
        return self._say('这段是我趴在码头录的。听见小石头滚过去了吗？我最爱退潮时那个声音。', '港湾录音')

    def _fish(self, fields):
        tide = int(self._sync() // 90)
        if self.data['last_fishing_tide'] == tide:
            return self._say('浮漂还没动哦。我们等下一阵潮，再来钓一竿。', '等一阵潮')
        name, line = CATCHES[self.data['catches'] % len(CATCHES)]
        self.data['last_fishing_tide'] = tide
        self.data['catches'] += 1
        self._journal('和你钓到' + name + '。', 'together')
        return self._say(line, '今天的渔获')

    def _chat(self, fields):
        text = fields['text'].strip()[:600]
        self.data['memories'].append({'text': text, 'time': self.clock()})
        self.data['memories'] = self.data['memories'][-100:]
        pending = fields.get('await_reply', False)
        response = '正在想怎么回你…' if pending else '留言已保存。'
        message = self._say(response, '对话', speech='', audible=False)
        conversation = self.data['conversation']
        turn = {'user': text, 'reply': response, 'time': self.clock(),
                'reply_state': 'pending' if pending else 'saved'}
        conversation.append(turn)
        self.data['conversation'] = conversation[-100:]
        return {**message, 'turn': turn}

    def finish_chat(self, draft, response, *, failed=False):
        with self.lock:
            turn = draft['turn']
            if not any(saved is turn for saved in self.data['conversation']):
                return
            turn['reply'] = response
            turn['reply_state'] = 'failed' if failed else 'answered'
            turn['reply_time'] = self.clock()
            if self.message_id == draft['id']:
                self._say(response, draft['topic'], speech='' if failed else response, audible=not failed)
            self._save()

    def chat_context(self, draft):
        with self.lock:
            previous = []
            for turn in self.data['conversation']:
                if turn is draft['turn']:
                    break
                if turn.get('reply_state') in (None, 'answered'):
                    previous.append(turn)
            else:
                previous.clear()
            activity = ACTIVITIES[int(self._sync() // 90) % len(ACTIVITIES)][1]
            return activity, deepcopy(previous[-4:])

    def _recall(self, fields):
        memories = self.data['memories']
        if memories:
            return self._say('我记得你上次说，“' + memories[-1]['text'] + '”。后来怎么样了？',
                             '翻开航海日记', speech='我还记得你上次说的事。我把那一页打开了，后来怎么样了？')
        return self._say('我们第一次见面，你就在港口帮我看灯。我要把这一页留给你。', '日记第一页')

    def _choose(self, fields):
        value = fields['value']
        label, outcome = SIGNAL_SITES[value]
        if self.data['room_choice'] != value:
            self.data['room_choice'] = value
            self._journal('一起决定把信号灯放在' + label + '。', 'together')
        return self._say('好哇，信号灯放在' + label + '。' + outcome, '选好了灯位')

    def _advance(self, fields):
        self.data['offset'] += 180
        self._sync()
        return self._say(self._project_line(), PROJECT_STAGES[self.data['project_stage']][0], self._signal_choices())

    def _voice(self, fields):
        self.data['voice'] = bool(fields.get('enabled'))
        return self._say('欸，听得到我吗？我们去听海呀。' if self.data['voice'] else '我用文字陪你喔。', '企鹅的声音')

    def _voice_select(self, fields):
        self.data['voice_id'] = fields['value']
        return deepcopy(self.message)

    def _voice_preview(self, fields):
        return self._say(VOICE_PREVIEWS.get(self.data['voice_id'], PREVIEW_LINE), '试听音色', preview=True)

    def _scale(self, fields):
        self.data['pet_scale'] = round(fields['value'], 2)
        return deepcopy(self.message)

    def earbud_position(self, position):
        with self.lock:
            if not any(key in position for key in ('left', 'right')):
                return
            merged = deepcopy(self.device.get('position', {}))
            merged.update(deepcopy(position))
            if merged == self.device.get('position'):
                return
            previous_ears = dict(self.case_life.ears)
            previous_lid = self.case_life.lid
            event = self.case_life.position(position)
            ears = self.case_life.ears.values()
            worn = True if 'worn' in ears else None if 'unknown' in ears else False
            self.device.update({'worn': worn, 'position': merged, 'estimated_lid': self.case_life.lid})
            if self.device['status'] == 'syncing':
                self.device.update({'status': 'connected', 'detail': 'OPPO Enco Air5 Pro · 实时连接'})
            if previous_ears != self.case_life.ears or previous_lid != self.case_life.lid:
                self.case_demo = None
            if event:
                if self.case_demo is None:
                    self._case_feedback(event)
            self._publish_earbuds()
            if event:
                self._save()

    def earbud_touch(self, touch):
        ear, gesture = touch.get('ear'), touch.get('gesture')
        if ear not in ('left', 'right'):
            return
        with self.lock:
            if gesture not in TOUCH_LABELS:
                self.device['unclassified_touch'] = deepcopy(touch)
                self._publish_earbuds()
                return
            self.device['last_touch'] = ('左耳' if ear == 'left' else '右耳') + TOUCH_LABELS[gesture]
            self.case_demo = None
            event = self.case_life.touch(touch)
            if event:
                self._case_feedback(event)
                self._publish_earbuds()
                self._save()

    def _case_feedback(self, event):
        if event is None:
            return
        if event['kind'] in ('knock', 'project'):
            handler = self._knock if event['kind'] == 'knock' else self._project
            handler({'instant': True})
        elif event['kind'] == 'worn' and self.data['memories']:
            self._recall({})
        else:
            self._say(event['text'], event['label'], speech=event['text'], audible=event['audible'])
        if self.case_demo is None and event['kind'] in ('lid_opened', 'lid_closed', 'taken_out', 'returned'):
            self._journal(event['label'] + '。' + event['text'], 'together')

    def simulate_case(self, value):
        positions = {'lid_open', 'lid_close', 'take_left', 'take_right', 'return_left', 'return_right',
                     'return_both', 'wear', 'remove'}
        touches = {
            'single_left': ('left', 'single_tap'), 'single_right': ('right', 'single_tap'),
            'left': ('left', 'double_tap'), 'right': ('right', 'double_tap'),
            'swipe_up': ('left', 'swipe_up'), 'swipe_down': ('left', 'swipe_down'),
        }
        if value not in positions and value not in touches and value != 'live':
            raise ValueError('请选择一个耳机盒或触摸动作。')
        with self.lock:
            if value == 'live':
                if self.case_demo is not None:
                    self.case_demo = None
                    self._publish_earbuds()
                return
            if self.case_demo is None:
                self.case_demo = CaseLife(deepcopy(self.case_life.saved), self.clock, simulated=True)
                self.case_demo.position({'left': {'out_of_case': False}, 'right': {'out_of_case': False},
                                         'estimated_lid': 'unknown'})
            if value in touches:
                ear, gesture = touches[value]
                event = self.case_demo.touch({'ear': ear, 'gesture': gesture})
            else:
                ears = dict(self.case_demo.ears)
                lid = self.case_demo.lid
                if value in ('lid_open', 'lid_close'):
                    lid = 'open' if value == 'lid_open' else 'closed'
                elif value.startswith('take_'):
                    ears[value.removeprefix('take_')] = 'out'
                    lid = 'open'
                elif value == 'return_both':
                    ears = {'left': 'docked', 'right': 'docked'}
                    lid = 'open'
                elif value.startswith('return_'):
                    ears[value.removeprefix('return_')] = 'docked'
                    lid = 'open'
                elif value == 'wear':
                    ears['left'] = 'worn'
                    lid = 'open'
                elif value == 'remove':
                    ears = {ear: 'out' if state == 'worn' else state for ear, state in ears.items()}
                event = self.case_demo.position({
                    **{ear: {'out_of_case': state != 'docked', 'in_ear': state == 'worn'} for ear, state in ears.items()},
                    'estimated_lid': lid,
                })
            self._case_feedback(event)
            self._publish_earbuds()

    def device_status(self, status, detail):
        with self.lock:
            self.device.update({'status': status, 'detail': detail})
            if status != 'connected':
                self.device.update({'worn': None, 'estimated_lid': 'unknown'})
                self.device.pop('position', None)
                self.case_life.disconnect()
            self._publish_earbuds()

    def earbud_battery(self, battery):
        with self.lock:
            values = {key: value for key, value in battery.items() if key in ('left', 'right', 'case')}
            if any(self.device['battery'].get(key) != value for key, value in values.items()):
                self.device['battery'].update(values)
                self._publish_earbuds()
