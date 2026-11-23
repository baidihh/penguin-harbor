"""The earbud case is a home, and physical actions are visits to that home."""

from copy import deepcopy
import time


PHASE_LABELS = {
    'unknown': '等你打开耳机盒',
    'peeking': '从盒口探头',
    'ready': '跟你出门',
    'together': '在你耳边',
    'waiting': '在手边等你',
    'home': '回到盒内小屋',
    'sleeping': '回盒睡觉',
}
POSTCARDS = (
    ('蓝色贝壳', '睡前画了一枚蓝色贝壳，壳上的纹路像小小的海浪。'),
    ('歪星星灯罩', '把小灯罩擦得亮亮的，留了一颗歪歪的小星星给你。'),
    ('潮汐明信片', '在明信片上画了退潮的码头，还给你留了一个坐的位置。'),
    ('围巾上的雪', '把今天的小雪画在围巾边上，等你来一起看。'),
)
TOUCH_LABELS = {
    'single_tap': '单击', 'double_tap': '双击',
    'swipe_up': '上滑', 'swipe_down': '下滑',
}


class CaseLife:
    def __init__(self, saved, clock=time.time, simulated=False):
        self.saved = saved
        self.clock = clock
        self.simulated = simulated
        self.saved.setdefault('postcards', [])
        self.saved.setdefault('resting', False)
        self.saved.setdefault('pending_postcard', None)
        self.saved.setdefault('visits', 0)
        self.saved.setdefault('affection', 0)
        self.saved.setdefault('warmth', 60)
        self.ears = {'left': 'unknown', 'right': 'unknown'}
        self.lid = 'unknown'
        self.phase = 'unknown'
        self.event_id = 0
        self.last_event = None
        self.events = []
        self.selected = max(0, len(self.saved['postcards']) - 1)

    def _event(self, kind, label, text, animation='waving', audible=True, *, light_percent=None):
        self.event_id += 1
        event = {
            'id': self.event_id, 'kind': kind, 'label': label, 'text': text,
            'animation': animation, 'time': self.clock(), 'audible': audible,
        }
        if light_percent is not None:
            event['light_percent'] = light_percent
        self.last_event = event
        self.events = (self.events + [event])[-8:]
        return deepcopy(event)

    def _wake(self):
        pending = self.saved['pending_postcard']
        self.saved['resting'] = False
        if not pending:
            return None
        card = {**pending, 'time': self.clock()}
        self.saved['postcards'] = (self.saved['postcards'] + [card])[-12:]
        self.saved['visits'] += 1
        self.saved['pending_postcard'] = None
        self.selected = len(self.saved['postcards']) - 1
        return card

    def position(self, position):
        before = dict(self.ears)
        previous_lid = self.lid
        previous_phase = self.phase
        for ear in self.ears:
            value = position.get(ear, {})
            if value.get('in_ear') is True:
                self.ears[ear] = 'worn'
            elif 'out_of_case' in value:
                self.ears[ear] = 'out' if value['out_of_case'] else 'docked'
        if 'estimated_lid' in position:
            self.lid = position['estimated_lid'] if position['estimated_lid'] in ('open', 'closed') else 'unknown'

        worn = 'worn' in self.ears.values()
        docked = all(value == 'docked' for value in self.ears.values())
        outside = any(value in ('worn', 'out') for value in self.ears.values())
        if worn:
            self.phase = 'together'
        elif (docked and self.lid == 'closed') or (self.saved['resting'] and not outside and self.lid != 'open'):
            self.phase = 'sleeping'
        elif docked:
            self.phase = 'home' if previous_phase in ('home', 'ready', 'waiting', 'together') else 'peeking'
        elif outside:
            self.phase = 'waiting' if previous_phase in ('together', 'waiting') else 'ready'
        else:
            self.phase = 'unknown'

        taken = [ear for ear in self.ears if before[ear] == 'docked' and self.ears[ear] in ('out', 'worn')]
        if not taken and self.saved['resting'] and outside:
            taken = [ear for ear in self.ears if self.ears[ear] in ('out', 'worn')]
        returned = [ear for ear in self.ears if before[ear] in ('out', 'worn') and self.ears[ear] == 'docked']
        card = self._wake() if self.lid == 'open' or worn or taken else None
        if self.phase == 'sleeping' and previous_phase != 'sleeping':
            if not self.saved['resting']:
                title, text = POSTCARDS[self.saved['visits'] % len(POSTCARDS)]
                self.saved['pending_postcard'] = {'title': title, 'text': text}
                self.saved['resting'] = True
            return self._event('lid_closed', '合盖 · 小屋熄灯', '我把今天的小事收好了。晚安，下次给你看我的小收藏。', 'idle', False)
        if worn and 'worn' not in before.values():
            text = '喂喂，听得到吗？我跟着你出来啦。上次的事，我们可以接着聊。'
            if card:
                text += '还给你带了我的' + card['title'] + '。'
            return self._event('worn', '戴上 · 到你耳边', text)
        if taken:
            side = '左耳' if taken[0] == 'left' else '右耳'
            return self._event('taken_out', side + '取出 · 跟你出门', '等我把围巾围好！今天也带我出去看看吧。', 'jumping')
        if returned:
            if docked:
                return self._event('returned', '双耳回盒 · 收拾小屋', '到家啦。我先把小屋收拾一下，再给你准备一件小收藏。', 'running-left', False)
            return self._event('returned', '一只耳机回家了', '这边的小床先铺好啦，另一边的位置也给你留着。', 'running-left')
        if self.lid == 'open' and previous_lid != 'open':
            text = '有光进来啦！我正窝在小屋里，给你留了个位置。'
            if card:
                text = '你回来啦！我' + card['text']
            return self._event('lid_opened', '开盖 · 探头迎接你', text)
        if not worn and 'worn' in before.values():
            return self._event('removed', '摘下 · 在手边等你', '你先忙，我就在这里。再戴上时，我们接着刚才的话。', 'idle')
        return None

    def touch(self, touch):
        ear, gesture = touch.get('ear'), touch.get('gesture')
        if ear not in ('left', 'right') or gesture not in TOUCH_LABELS:
            return None
        if gesture == 'single_tap' and ear == 'left':
            self.saved['affection'] = min(100, self.saved['affection'] + 1)
            return self._event('pet', '左耳单击 · 摸摸头', '嘿嘿，摸到围巾啦。今天也很高兴见到你。', 'waving')
        if gesture == 'single_tap':
            cards = self.saved['postcards']
            if cards:
                self.selected = (self.selected + 1) % len(cards)
                text = '给你看我的' + cards[self.selected]['title'] + '。我' + cards[self.selected]['text']
            else:
                text = '收藏架还空着。下次回盒休息后，我给你留一件小东西。'
            return self._event('collection', '右耳单击 · 翻小收藏', text)
        if gesture == 'double_tap':
            kind = 'knock' if ear == 'left' else 'project'
            label = '左耳双击 · 叫叫企鹅' if ear == 'left' else '右耳双击 · 问问进展'
            return self._event(kind, label, '', 'jumping' if ear == 'left' else 'waving')
        brighter = gesture == 'swipe_up'
        self.saved['warmth'] = max(10, min(100, self.saved['warmth'] + (10 if brighter else -10)))
        label = '上滑 · 小屋亮一点' if brighter else '下滑 · 小屋暗一点'
        text = '灯亮一点，我就能画清楚贝壳的纹路啦。' if brighter else '这样暖暖暗暗的，正好窝着听海。'
        return self._event('light', label, text, light_percent=self.saved['warmth'])

    def disconnect(self):
        self.ears = {'left': 'unknown', 'right': 'unknown'}
        self.lid = 'unknown'
        self.phase = 'sleeping' if self.phase == 'sleeping' else 'unknown'

    def snapshot(self):
        event = self.last_event
        animation = event['animation'] if event and self.clock() - event['time'] < 2.5 else 'idle'
        cards = self.saved['postcards']
        return {
            'source': 'simulation' if self.simulated else 'device',
            'phase': self.phase, 'label': PHASE_LABELS[self.phase],
            'lid': self.lid, 'ears': dict(self.ears), 'animation': animation,
            'event_id': self.event_id, 'last_event': deepcopy(event), 'events': deepcopy(self.events),
            'postcards': deepcopy(cards), 'selected_postcard': deepcopy(cards[self.selected]) if cards else None,
            'affection': self.saved['affection'], 'warmth': self.saved['warmth'],
        }
