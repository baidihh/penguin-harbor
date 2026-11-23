"""The penguin's desktop conversation, activity journal, and music controls."""

from datetime import datetime
from tkinter import font as tkfont
import os
import queue
import threading
import tkinter as tk

from .desktop_voice import DesktopVoice, open_system_dictation
from .desktop_overlay import DesktopOverlay, MENU_COLORS


PAPER = '#394f5d'
SURFACE = '#344954'
HARBOR = '#9ce8df'
INK = '#f3f8fa'
MUTED = '#c5d8e1'
LINE = '#607986'
WATER = '#486773'
ACCENT = '#52737e'
FONT = 'Microsoft YaHei UI'
TRANSPARENT = '#ff00fe'


def rounded_panel(canvas, width, height, radius, fill):
    w, h, r = width - 1, height - 1, radius
    canvas.delete('outline')
    canvas.create_polygon(
        0, r, 0, 0, r, 0, w-r, 0, w, 0, w, r,
        w, h-r, w, h, w-r, h, r, h, 0, h, 0, h-r,
        smooth=True, splinesteps=24, fill=fill, outline=LINE, tags='outline')
    canvas.tag_lower('outline')


class DesktopRoom:
    def __init__(self, desktop):
        self.desktop = desktop
        self.root = desktop.root
        self.app = desktop.app
        self.results = queue.Queue()
        self.voice = DesktopVoice(self.app.runtime, self.results)
        self.wake_enabled = tk.BooleanVar(master=self.root)
        self.lyrics_visible = tk.BooleanVar(master=self.root, value=True)
        self.overlay = DesktopOverlay(self)
        self.notice = tk.StringVar(master=self.root)
        self.window = None
        self.tab = ''
        self.last_content = None
        self.last_music = None
        self.search_results = []
        self.queue_songs = ()
        self.show_all = False
        self.bubble_height = 80
        self.last_geometry = None
        self.waiting = False
        self.pause_label = tk.StringVar(master=self.root, value='暂停')

    def button(self, parent, text, action, *, primary=False, **options):
        background = options.pop('background', parent.cget('bg'))
        padx, pady = options.pop('padx', 8), options.pop('pady', 3)
        return tk.Button(parent, text=text, command=action, bd=0, relief='flat', padx=padx, pady=pady,
                         bg=HARBOR if primary else background, fg=PAPER if primary else INK,
                         activebackground=ACCENT, activeforeground=INK, cursor='hand2',
                         highlightthickness=1, highlightbackground=background, highlightcolor=HARBOR,
                         font=(FONT, 9), **options)

    def open(self, tab='对话', dictate=False, history=False):
        if self.window is None:
            self._build()
        if tab == '对话' and not history:
            self.window.withdraw()
            self.overlay.open()
            if dictate:
                self.start_voice()
            return
        self.overlay.composing = False
        self.show_all = history
        self.last_content = None
        self.select_tab(tab)
        self._refresh_room(self.app.pet.snapshot(), self.app.music.snapshot())
        self._fit()
        self.window.deiconify()
        self.window.lift()
        if tab == '对话':
            self.entry.focus_force()
        if dictate:
            self.start_voice()

    def _build(self):
        key = TRANSPARENT if self.root.tk.call('tk', 'windowingsystem') == 'win32' else PAPER
        self.window = window = tk.Toplevel(self.root, bg=key)
        window.withdraw()
        window.title('和企鹅说话')
        window.protocol('WM_DELETE_WINDOW', self._close)
        window.bind('<Escape>', lambda _: self._close())
        if window.tk.call('tk', 'windowingsystem') == 'win32':
            window.overrideredirect(True)
            window.attributes('-transparentcolor', key)
            window.attributes('-alpha', .86)
        window.attributes('-topmost', True)
        window.bind('<FocusOut>', lambda _: window.after(500, self._dismiss_if_idle))
        self.header = header = tk.Frame(window, bg=PAPER, padx=10, pady=4)
        self.button(header, '×', self._close).pack(side='right')
        header.bind('<ButtonPress-1>', lambda event: setattr(self, '_panel_drag', (event.x_root, event.y_root, window.winfo_x(), window.winfo_y())))
        header.bind('<B1-Motion>', self._drag_panel)
        self.tabs = {}
        for title in ('对话', '音乐'):
            button = self.button(header, '会话记录' if title == '对话' else title, lambda name=title: self.open(name, history=name == '对话'))
            button.pack(side='left')
            self.tabs[title] = button
        self.notice_label = tk.Label(window, textvariable=self.notice, bg=PAPER, fg=MUTED,
                                     anchor='w', justify='left', wraplength=320, font=(FONT, 8), padx=10, pady=4)
        self.notice.trace_add('write', lambda *_: self._notice_changed())
        self.body = tk.Frame(window, bg=key)
        self.body.pack(fill='both', expand=True)
        self.panels = {title: tk.Frame(self.body, bg=key if title == '对话' else PAPER)
                       for title in self.tabs}
        self._build_chat()
        self._build_music()
        self.more = tk.Menu(window, tearoff=0, font=(FONT, 9), **MENU_COLORS)
        self.more.add_command(label='会话记录', command=lambda: self.open(history=True))
        self.more.add_command(label='歌曲与播放队列', command=lambda: self.open('音乐'))
        self.more.add_checkbutton(label='桌面歌词', variable=self.lyrics_visible)
        self.more.add_command(label='去港湾', command=self.app.open_room)
        self.more.add_command(label='收起', command=self._close)

    def _close(self):
        self.voice.dictate.clear()
        self.window.withdraw()

    def _dismiss_if_idle(self):
        if (self.window.winfo_viewable() and self.tab == '对话' and not self.show_all
                and not self.window.focus_displayof() and not self.entry.get('1.0', 'end-1c').strip()
                and not self.voice.dictate.is_set() and not self.waiting):
            x, y = self.window.winfo_pointerxy()
            for widget in (self.window, self.root):
                if (widget.winfo_rootx() <= x < widget.winfo_rootx() + widget.winfo_width()
                        and widget.winfo_rooty() <= y < widget.winfo_rooty() + widget.winfo_height()):
                    return
            self._close()

    def _notice_changed(self):
        if self.notice.get():
            self.notice_label.pack(fill='x', side='bottom', before=self.body)
        else:
            self.notice_label.pack_forget()
        self._fit()

    def _fit(self):
        if not self.window or not self.tab:
            return
        compact = self.tab == '对话' and not self.show_all
        width = min(352 if compact else 500, self.root.winfo_screenwidth() - 16)
        if compact:
            height = self.bubble_height + self.compose_edge.winfo_reqheight() + 6
        else:
            height = 420 if self.tab != '音乐' else 380
        if self.notice.get():
            height += self.notice_label.winfo_reqheight()
        height = min(height, self.root.winfo_screenheight() - 70)
        self.chat_edge.configure(height=self.bubble_height if compact else 1)
        anchor = (self.root.winfo_x(), self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height())
        geometry = self._near_penguin(width, height)
        if (geometry, anchor) != self.last_geometry:
            self.window.geometry(geometry)
            self.last_geometry = (geometry, anchor)

    def _text(self, panel):
        box = tk.Text(panel, wrap='word', bg=PAPER, fg=INK, font=(FONT, 10), bd=0,
                      highlightthickness=0, padx=8, pady=6, state='disabled', width=1, height=1)
        box.pack(fill='both', expand=True)
        box.tag_configure('time', foreground=MUTED, font=(FONT, 8), spacing1=6)
        box.tag_configure('you_time', foreground=MUTED, justify='right', font=(FONT, 8), spacing1=6)
        box.tag_configure('you', foreground=HARBOR, justify='right', lmargin1=72, lmargin2=72,
                          spacing1=2, spacing2=2, spacing3=4)
        box.tag_configure('penguin', foreground=INK, rmargin=32, spacing1=2, spacing2=2, spacing3=4)
        box.tag_configure('chat_status', foreground=MUTED, font=(FONT, 9), spacing1=9, spacing3=8)
        box.tag_configure('welcome', foreground=HARBOR, font=(FONT, 11), rmargin=20, spacing3=3)
        box.tag_configure('activity', foreground=INK, spacing1=5, spacing2=3, spacing3=11)
        return box

    def _build_chat(self):
        panel = self.panels['对话']
        self.compose_edge = edge = tk.Canvas(panel, bg=panel.cget('bg'), highlightthickness=0, height=42)
        edge.pack(fill='x', side='bottom', pady=(5, 0))
        compose = tk.Frame(edge, bg=SURFACE)
        content = edge.create_window(10, 7, anchor='nw', window=compose)

        def layout(event):
            rounded_panel(edge, event.width, event.height, 17, SURFACE)
            edge.itemconfigure(content, width=max(1, event.width - 20))

        edge.bind('<Configure>', layout)
        compose.bind('<Configure>', lambda event: edge.configure(height=event.height + 14))
        self.button(compose, '↑', self.send, background=WATER, padx=8, pady=1).pack(side='right', padx=(5, 0))
        self.button(compose, '⋯', self._open_more, padx=5, pady=1).pack(side='right')
        self.button(compose, '语音', self.start_voice, padx=5, pady=1).pack(side='right')
        self.entry = tk.Text(compose, bg=SURFACE, fg=INK, insertbackground=INK, font=(FONT, 10),
                             relief='flat', bd=0, highlightthickness=0, width=1, height=1,
                             wrap='word', padx=2, pady=2)
        self.entry.pack(side='left', fill='both', expand=True)
        hint = tk.Label(self.entry, text='和企鹅说句话…', bg=SURFACE, fg=MUTED, font=(FONT, 10), padx=0, pady=0)
        hint.place(x=8, y=2)
        hint.bind('<Button-1>', lambda _: self.entry.focus_set())

        def update_hint(_):
            if not self.entry.edit_modified():
                return
            if self.entry.get('1.0', 'end-1c'):
                hint.place_forget()
            else:
                hint.place(x=8, y=2)
            self.entry.configure(height=min(3, max(1, self.entry.get('1.0', 'end-1c').count('\n') + 1)))
            self.entry.edit_modified(False)
            edge.after_idle(self._fit)

        self.entry.bind('<<Modified>>', update_hint)
        self.entry.bind('<Return>', self.send)
        self.entry.bind('<Shift-Return>', self._newline)
        self.entry.bind('<Tab>', self._focus_next)
        self.entry.bind('<Shift-Tab>', self._focus_previous)
        self.chat_edge = card = tk.Canvas(panel, bg=panel.cget('bg'), highlightthickness=0, height=80)
        card.pack(fill='both', expand=True)
        interior = tk.Frame(card, bg=PAPER)
        content = card.create_window(7, 5, anchor='nw', window=interior)

        def layout_chat(event):
            rounded_panel(card, event.width, event.height, 13, PAPER)
            card.itemconfigure(content, width=max(1, event.width - 14), height=max(1, event.height - 10))

        card.bind('<Configure>', layout_chat)
        self.chat = self._text(interior)
        self.chat_font = tkfont.Font(font=self.chat.cget('font'))

    def _open_more(self):
        self.more.tk_popup(self.window.winfo_rootx() + self.window.winfo_width() - 110,
                           self.window.winfo_rooty() + self.window.winfo_height() - 8)

    def _newline(self, _):
        self.entry.insert('insert', '\n')
        return 'break'

    @staticmethod
    def _focus_next(event):
        event.widget.tk_focusNext().focus_set()
        return 'break'

    @staticmethod
    def _focus_previous(event):
        event.widget.tk_focusPrev().focus_set()
        return 'break'

    def _build_music(self):
        panel = self.panels['音乐']
        player = tk.Frame(panel, bg=PAPER, padx=12, pady=8)
        player.pack(fill='x')
        self.track = tk.StringVar(master=panel, value='还没有播放歌曲')
        tk.Label(player, textvariable=self.track, bg=PAPER, fg=HARBOR, anchor='w',
                 font=(FONT, 11), wraplength=460, justify='left').pack(fill='x')
        controls = tk.Frame(player, bg=PAPER, pady=5)
        controls.pack(fill='x')
        self.button(controls, '‹ 上一首', lambda: self.step(-1)).pack(side='left')
        self.button(controls, '', self.toggle_pause, textvariable=self.pause_label, primary=True).pack(side='left')
        self.button(controls, '下一首 ›', lambda: self.step(1)).pack(side='left')
        self.button(controls, '停止', lambda: self.run_job(self.app.music.stop)).pack(side='left')
        tk.Checkbutton(controls, text='歌词', variable=self.lyrics_visible, bg=PAPER, fg=INK,
                       activebackground=PAPER, selectcolor=SURFACE, bd=0, cursor='hand2',
                       font=(FONT, 9)).pack(side='right')
        self.lyrics_hint = tk.StringVar(master=panel)
        tk.Label(player, textvariable=self.lyrics_hint, bg=PAPER, fg=MUTED,
                 font=(FONT, 8), anchor='w').pack(fill='x')
        search = tk.Frame(panel, bg=PAPER, padx=12, pady=4)
        search.pack(fill='x')
        self.keyword = tk.Entry(search, bg=SURFACE, fg=INK, insertbackground=INK, font=(FONT, 10), bd=0,
                                highlightthickness=1, highlightbackground=LINE, highlightcolor=HARBOR)
        self.keyword.pack(side='left', fill='x', expand=True, ipady=5)
        self.keyword.bind('<Return>', self.search)
        self.search_button = self.button(search, '搜歌', self.search, primary=True)
        self.search_button.pack(side='left', padx=(8, 0))
        columns = tk.Frame(panel, bg=PAPER, padx=12, pady=8)
        columns.pack(fill='both', expand=True)
        matches = tk.Frame(columns, bg=PAPER)
        matches.pack(side='left', fill='both', expand=True, padx=(0, 6))
        queue = tk.Frame(columns, bg=PAPER)
        queue.pack(side='left', fill='both', expand=True, padx=(6, 0))
        for column, actions in ((matches, [('立即播放', lambda: self.add_song(play=True)),
                                           ('加入队列', self.add_song)]),
                                (queue, [('播放选中', self.select_song), ('移出队列', self.remove_song)])):
            footer = tk.Frame(column, bg=PAPER, pady=4)
            footer.pack(side='bottom', fill='x')
            for title, action in actions:
                self.button(footer, title, action).pack(side='left')
        self.search_box = self._song_list(matches, '搜索结果', 4)
        self.search_box.bind('<Double-Button-1>', lambda _: self.add_song(play=True))
        self.queue_box = self._song_list(queue, '播放队列', 4)
        self.queue_box.bind('<Double-Button-1>', lambda _: self.select_song())

    def _song_list(self, panel, title, height):
        tk.Label(panel, text=title, bg=PAPER, fg=MUTED, anchor='w', font=(FONT, 9, 'bold')).pack(fill='x', pady=(2, 4))
        box = tk.Listbox(panel, width=1, height=height, bg=SURFACE, fg=INK, font=(FONT, 10), exportselection=False,
                         highlightthickness=1, highlightbackground=LINE, highlightcolor=HARBOR,
                         bd=0, selectbackground=ACCENT,
                         selectforeground=INK)
        box.pack(fill='both', expand=True)
        return box

    def select_tab(self, tab):
        if self.tab:
            self.panels[self.tab].pack_forget()
        self.tab = tab
        self.panels[tab].pack(fill='both', expand=True)
        for title, button in self.tabs.items():
            color = WATER if title == tab else PAPER
            button.configure(bg=color, highlightbackground=color, fg=HARBOR if title == tab else MUTED)
        self.last_content = None
        if tab == '对话' and not self.show_all:
            self.header.pack_forget()
        else:
            self.header.pack(fill='x', before=self.body)
        self._fit()

    def run_job(self, action, *, search=False):
        def worker():
            try:
                result = action()
                if search:
                    self.results.put(('search', result))
            except (OSError, ValueError, RuntimeError) as exc:
                self.results.put(('search_error' if search else 'error', str(exc) or '这次操作没有完成。'))
        threading.Thread(target=worker, daemon=True).start()

    def send(self, event=None):
        words = self.entry.get('1.0', 'end-1c').strip()
        if words:
            self.entry.delete('1.0', 'end')
            self.notice.set('')
            self.run_job(lambda: self.app.interact('chat', text=words))
        return 'break'

    def active_entry(self):
        if self.overlay.window and self.overlay.window.winfo_viewable():
            if not (self.window and self.window.winfo_viewable()):
                return self.overlay.entry
        return self.entry

    def _drag_panel(self, event):
        sx, sy, x, y = self._panel_drag
        self.window.geometry(f'+{max(0, x + event.x_root - sx)}+{max(0, y + event.y_root - sy)}')

    def start_voice(self):
        self.active_entry().focus_force()
        if self.voice.dictate.is_set():
            self.voice.finish.set()
        elif self.voice.start():
            self.notice.set('正在打开麦克风…')
        elif os.name == 'nt':
            self.notice.set('使用 Windows 语音输入，确认文字后发送。')
            self.window.after(120, open_system_dictation)
        else:
            self.notice.set('请先安装中文语音识别模型。')

    def toggle_wake(self):
        if not self.wake_enabled.get():
            self.voice.wake.clear()
            return
        self.open()
        if self.voice.start(wake=True):
            self.notice.set('语音唤醒已开启。说「你好企鹅」可以打开聊天。')
        else:
            self.wake_enabled.set(False)
            self.notice.set('语音唤醒需要离线中文语音模型。')

    def search(self, event=None):
        keyword = self.keyword.get().strip()
        if keyword and str(self.search_button['state']) != 'disabled':
            self.search_button.config(state='disabled')
            self.notice.set('正在搜索歌曲…')
            self.run_job(lambda: self.app.music.search(keyword), search=True)
        return 'break'

    def select_song(self):
        picked = self.queue_box.curselection()
        if picked:
            identity = self.queue_songs[picked[0]]['id']
            self.run_job(lambda: self.app.music.select(identity))

    def remove_song(self):
        picked = self.queue_box.curselection()
        if picked:
            identity = self.queue_songs[picked[0]]['id']
            self.run_job(lambda: self.app.music.remove(identity))

    def add_song(self, play=False):
        picked = self.search_box.curselection()
        if picked:
            song = self.search_results[picked[0]]
            self.run_job(lambda: self.app.music.play(song) if play else self.app.music.enqueue(song))

    def step(self, direction):
        def run():
            if not self.app.music.step(direction):
                self.results.put(('error', '队列里已经没有这一方向的歌曲。'))
        self.run_job(run)

    def toggle_pause(self):
        self.run_job(self.app.music.toggle_pause)

    @staticmethod
    def _time(when):
        return datetime.fromtimestamp(when).strftime('%m-%d %H:%M')

    def _write(self, box, lines):
        if self.last_content == lines:
            return
        follow = self.last_content is None or box.yview()[1] > 0.97
        scroll = box.yview()[0]
        self.last_content = lines
        box.config(state='normal')
        box.delete('1.0', 'end')
        for line, tag in lines:
            box.insert('end', line + '\n', tag)
        box.config(state='disabled')
        if box is self.chat:
            font = self.chat_font
            width = 310
            rows = sum(max(1, (font.measure(line) + width - 1) // width)
                       for text, _ in lines for line in text.split('\n'))
            self.bubble_height = min(220, max(48, rows * font.metrics('linespace') + len(lines) * 7 + 38))
            self._fit()
        if follow:
            box.see('end')
        else:
            box.yview_moveto(scroll)

    def _refresh_room(self, state, music):
        if self.tab == '对话':
            lines = []
            conversation = state.get('conversation', [])
            self.waiting = bool(conversation and conversation[-1].get('reply_state') == 'pending')
            if self.show_all:
                for row in state['timeline']:
                    user = row['label'] == '你'
                    lines.extend([(row['label'] + '  ·  ' + self._time(row['time']), 'you_time' if user else 'time'),
                                  (row['text'], 'you' if user else 'penguin')])
                self._write(self.chat, lines or [('今天想聊什么？', 'welcome')])
                return
            for turn in conversation[-1:]:
                minute = self._time(turn['time']) if self.show_all else datetime.fromtimestamp(turn['time']).strftime('%H:%M')
                lines.extend([('你  ·  ' + minute, 'you_time'), (turn['user'], 'you')])
                if turn.get('reply_state') in ('pending', 'failed', 'saved'):
                    lines.append((turn['reply'], 'chat_status'))
                else:
                    when = turn.get('reply_time', turn['time'])
                    reply_minute = self._time(when) if self.show_all else datetime.fromtimestamp(when).strftime('%H:%M')
                    lines.extend([('企鹅  ·  ' + reply_minute, 'time'), (turn['reply'], 'penguin')])
            if not lines:
                lines = [('今天想聊什么？', 'welcome')]
            self._write(self.chat, lines)
        elif self.tab == '音乐':
            self._refresh_music(music)

    def _refresh_music(self, music):
        current = music['current']
        name = current.get('name')
        paused = music.get('paused', False)
        self.pause_label.set('继续' if paused else '暂停')
        prefix = '已暂停' if paused else '正在播放' if music['playing'] else '已停止'
        self.track.set(f'{prefix}  ·  {self._song_title(current)}' if name else '还没有播放歌曲')
        hint = ''
        if self.lyrics_visible.get() and music['playing']:
            if music['lyrics_loading']:
                hint = '正在获取歌词…'
            elif not music['has_lyrics']:
                hint = '这首歌暂无同步歌词。'
        self.lyrics_hint.set(hint)
        signature = (music['songs'], current.get('id'), music['playing'], paused)
        if signature != self.last_music:
            self.last_music = signature
            picked = self.queue_box.curselection()
            identity = self.queue_songs[picked[0]]['id'] if picked else None
            self.queue_songs = music['songs']
            self.queue_box.delete(0, 'end')
            for index, song in enumerate(self.queue_songs):
                marker = '▶ ' if music['playing'] and song['id'] == current.get('id') else '    '
                self.queue_box.insert('end', marker + self._song_title(song))
                if song['id'] == identity:
                    self.queue_box.selection_set(index)
        if music['error'] and music['error'] != self.notice.get():
            self.notice.set(music['error'])

    @staticmethod
    def _song_title(song):
        artist = song.get('artist')
        return song['name'] + (' · ' + artist if artist else '')

    def _near_penguin(self, width, height, *, above=False):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        if above:
            x += (self.root.winfo_width() - width) // 2
            y -= height + 8
        else:
            x -= width + 12
            y += self.root.winfo_height() - height
        x = min(max(0, x), max(0, self.root.winfo_screenwidth() - width))
        y = min(max(0, y), max(0, self.root.winfo_screenheight() - height - 35))
        return f'{width}x{height}+{x}+{y}'

    def update(self, state):
        while True:
            try:
                kind, value = self.results.get_nowait()
            except queue.Empty:
                break
            panels = getattr(self.desktop, 'panels', None)
            if panels and kind=='wake':
                panels.show('dock')
                if value:
                    panels.write('draft', text=value)
                else:
                    panels.voice()
                continue
            if panels and kind=='transcript' and panels.pending_voice:
                panels.pending_voice = False
                panels.show('dock')
                panels.write('draft', text=value or '')
                panels.notice = '确认文字后按回车发送。' if value else '没听清，请再试一次。'
                continue
            if panels and kind in ('error','microphone_error') and not (self.window and self.window.winfo_viewable()):
                panels.notice = str(value)
                continue
            if kind == 'wake':
                self.open(dictate=not value)
                if value:
                    self.active_entry().insert('end', value)
                    if self.overlay.window:
                        self.overlay._entry_changed()
            elif kind == 'transcript' and self.window:
                if value:
                    self.active_entry().insert('end', value)
                    if self.overlay.window:
                        self.overlay._entry_changed()
                    self.active_entry().focus_set()
                self.notice.set('确认文字后按回车发送。' if value else '没听清，点「语音」再说一次。')
            elif kind in ('search', 'search_error'):
                self.search_button.config(state='normal')
                if kind == 'search':
                    self.search_results = value
                    self.search_box.delete(0, 'end')
                    for song in value:
                        self.search_box.insert('end', self._song_title(song))
                self.notice.set(value if kind == 'search_error' else '' if value else '没有找到匹配的歌曲。')
            elif self.window:
                if kind == 'microphone_error':
                    self.wake_enabled.set(False)
                self.notice.set(value)
        room_visible = bool(self.window and self.window.winfo_viewable())
        panels = getattr(self.desktop, 'panels', None)
        if panels or room_visible or self.lyrics_visible.get() or self.overlay.window is not None:
            music = self.app.music.snapshot()
            if room_visible:
                self._refresh_room(state, music)
                self._fit()
            if panels:
                panels.update(state,music)
            else:
                self.overlay.update(state, music)
