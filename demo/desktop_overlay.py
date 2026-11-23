"""Transparent Windows captions and a quiet, hover-revealed conversation dock."""
import tkinter as tk
from tkinter import font as tkfont
from time import monotonic
from PIL import Image, ImageDraw, ImageFont, ImageTk

KEY = '#ff00fe'
GLASS = '#47616f'
INK = '#f2f8fa'
MUTED = '#d4e2e8'
MINT = '#9ce8df'
GLASS_RIM = '#73939e'
MENU_COLORS = dict(bg=GLASS, fg=INK, activebackground='#567681',
                   activeforeground=MINT, bd=1, relief='solid')
FONT = 'Microsoft YaHei UI'


def caption_origin(pet, screen, size, position=None):
    px, py, pw, ph = pet
    sw, sh = screen
    width, height = size
    x, y = position if position is not None else (px + pw - width, py - height - 8)
    if position is None and y < 8:
        y = py + ph + 8
    return max(8, min(x, sw - width - 8)), max(8, min(y, sh - height - 40))


class CaptionState:
    def __init__(self):
        self.last_turn = None
        self.reply_until = 0

    def text(self, state, music, conversation, composing, *, now=None):
        now = monotonic() if now is None else now
        turns = state.get('conversation') or []
        turn = turns[-1] if turns else None
        signature = dict(turn) if turn else None
        if signature != self.last_turn:
            self.last_turn = signature
            self.reply_until = now + 7
        if conversation and turn and (composing or turn.get('reply_state') == 'pending' or now < self.reply_until):
            return turn['reply']
        if music['playing'] or music['paused']:
            return music.get('lyric') or music['current'].get('name', '')
        return (turn['reply'] if turn else '今天想聊什么？') if conversation else ''


class DesktopOverlay:
    def __init__(self, room):
        self.room, self.root = room, room.root
        self.window = None
        self.conversation = False
        self.composing = False
        self.hover = False
        self.scale = 1.0
        self.position = None
        self.drag_start = None
        self.caption = CaptionState()
        self.signature = None
        self.geometry = None

    def _build(self):
        self.window = window = tk.Toplevel(self.root, bg=KEY)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes('-topmost', True)
        window.attributes('-transparentcolor', KEY)
        window.attributes('-alpha', .78)
        window.bind('<Escape>', lambda _: self.dismiss())
        window.protocol('WM_DELETE_WINDOW', self.dismiss)
        window.bind('<Enter>', lambda _: self._hover(True))
        window.bind('<Leave>', lambda _: window.after(150, self._leave))
        self.canvas = canvas = tk.Canvas(window, bg=KEY, highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        self.dock = tk.Frame(canvas, bg=GLASS, padx=5, pady=1)
        self.dock_item = canvas.create_window(12, 0, anchor='nw', window=self.dock, state='hidden')
        compose = tk.Frame(self.dock, bg=GLASS)
        compose.pack(fill='x')
        self.entry = tk.Text(compose, height=1, width=1, bg=GLASS, fg=INK, insertbackground=INK,
                             font=(FONT, 10), bd=0, highlightthickness=0, pady=3, wrap='none')
        self.entry.pack(side='left', fill='x', expand=True)
        self.hint = tk.Label(self.entry, text='和企鹅说点什么…', bg=GLASS, fg=MUTED, font=(FONT, 10))
        self.hint.place(x=0, y=2)
        self.hint.bind('<Button-1>', lambda _: self.entry.focus_force())
        self.entry.bind('<KeyRelease>', self._entry_changed)
        self.entry.bind('<Return>', self.send)
        self.entry.bind('<Shift-Return>', lambda _: 'break')
        self._button(compose, '⋯', self._show_menu).pack(side='right')
        self._button(compose, '↑', self.send, accent=True).pack(side='right')
        self.menu = tk.Menu(window, tearoff=False, font=(FONT, 9), **MENU_COLORS)
        self.menu.add_command(label='语音输入', command=self.room.start_voice)
        self.menu.add_command(label='会话记录', command=lambda: self.room.open(history=True))
        self.menu.add_command(label='播放队列', command=lambda: self.room.open('音乐'))
        self.menu.add_separator()
        self.menu.add_command(label='上一首', command=lambda: self.room.step(-1))
        self.menu.add_command(label='暂停', command=self.room.toggle_pause)
        self.pause_index = self.menu.index('end')
        self.menu.add_command(label='下一首', command=lambda: self.room.step(1))
        self.menu.add_separator()
        self.menu.add_command(label='缩小字幕', command=lambda: self.resize(-.1))
        self.menu.add_command(label='放大字幕', command=lambda: self.resize(.1))
        self.menu.add_command(label='收起', command=self.dismiss)
        self.status = tk.Label(self.dock, textvariable=self.room.notice, bg=GLASS, fg=MUTED,
                               font=(FONT, 8), anchor='w', wraplength=245)
        self.font = tkfont.Font(family=FONT, size=13, weight='bold')
        canvas.bind('<ButtonPress-1>', self._begin_drag)
        canvas.bind('<B1-Motion>', self._drag)
        canvas.bind('<ButtonRelease-1>', self._end_drag)
        canvas.configure(cursor='hand2')

    def _show_menu(self):
        self.menu.tk_popup(self.window.winfo_pointerx(), self.window.winfo_pointery())

    def _button(self, parent, text, action, *, accent=False):
        button = tk.Button(parent, text=text, command=action, bg=GLASS, fg=MINT if accent else INK,
                           activebackground='#567681', activeforeground=MINT, bd=0,
                           relief='flat', highlightthickness=0, font=(FONT, 9), padx=5, pady=2, cursor='hand2')
        button.bind('<Enter>', lambda _: button.configure(bg='#567681'))
        button.bind('<Leave>', lambda _: button.configure(bg=GLASS))
        return button

    def _entry_changed(self, _=None):
        if self.entry.get('1.0', 'end-1c'):
            self.hint.place_forget()
        else:
            self.hint.place(x=0, y=2)

    def open(self):
        if self.window is None:
            self._build()
        self.room.notice.set('')
        self.conversation = self.composing = True
        self.update(self.room.app.pet.snapshot(), self.room.app.music.snapshot())
        if not self.window.winfo_viewable():
            self.window.deiconify()
        self.entry.focus_force()

    def dismiss(self):
        self.conversation = self.composing = self.hover = False
        self.room.lyrics_visible.set(False)
        self.room.voice.dictate.clear()
        if self.window:
            self.window.withdraw()

    def send(self, _=None):
        words = self.entry.get('1.0', 'end-1c').strip()
        if words:
            if len(words) > 600:
                self.room.notice.set('每条消息最多 600 字。')
                return 'break'
            self.room.notice.set('')
            self.entry.delete('1.0', 'end')
            self._entry_changed()
            self.room.run_job(lambda: self.room.app.interact('chat', text=words))
            self.composing = False
            self.canvas.focus_set()
        return 'break'

    def resize(self, amount):
        self.scale = min(1.55, max(.8, round(self.scale + amount, 2)))

    def _hover(self, visible):
        self.hover = visible

    def _leave(self):
        x, y = self.window.winfo_pointerxy()
        if not (self.window.winfo_rootx() <= x < self.window.winfo_rootx() + self.window.winfo_width()
                and self.window.winfo_rooty() <= y < self.window.winfo_rooty() + self.window.winfo_height()):
            self.hover = False

    def _begin_drag(self, event):
        self.drag_start = event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y()

    def _drag(self, event):
        if self.drag_start:
            sx, sy, x, y = self.drag_start
            self.position = x + event.x_root - sx, y + event.y_root - sy

    def _end_drag(self, event):
        self.drag_start = None

    def update(self, state, music):
        active = self.conversation or (self.room.lyrics_visible.get() and (music['playing'] or music['paused']))
        if not active or not self.room.app.pet.pet_visible:
            if self.window:
                self.window.withdraw()
            return
        if self.window is None:
            self._build()
        text = self.caption.text(state, music, self.conversation, self.composing)
        width = min(round(320 * self.scale), self.root.winfo_screenwidth() - 24)
        signature = text, width, self.scale
        if signature != self.signature:
            self.signature = signature
            self.font.configure(size=round(11 * self.scale))
            # Keep desktop replies at two lines; the full turn stays in history.
            lines, line = [], ''
            for character in text:
                if character == '\n' or self.font.measure(line + character) > width - 24:
                    lines.append(line)
                    line = ''
                if len(lines) == 2:
                    lines[-1] = lines[-1][:-1] + '…'
                    break
                if character != '\n':
                    line += character
            else:
                lines.append(line)
            text = '\n'.join(lines)
            self.canvas.delete('caption')
            # Hard alpha avoids the magenta fringe produced by native text on a colour-key window.
            factor = 2
            font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', round(14 * self.scale * factor))
            bounds = ImageDraw.Draw(Image.new('RGBA', (1, 1))).multiline_textbbox(
                (0, 0), text, font=font, spacing=6, stroke_width=2)
            source = Image.new('RGBA', (width * factor, bounds[3] - bounds[1] + 24), (0, 0, 0, 0))
            ImageDraw.Draw(source).multiline_text(
                ((source.width - (bounds[2] - bounds[0])) / 2 - bounds[0], 10 - bounds[1]),
                text, font=font, fill=INK, stroke_width=2, stroke_fill='#30434e', spacing=6, align='center')
            source = source.convert('RGBa').resize((width, max(1, source.height // factor)),
                                                   Image.Resampling.LANCZOS).convert('RGBA')
            source.putalpha(source.getchannel('A').point(lambda value: 255 if value >= 128 else 0))
            self.caption_image = ImageTk.PhotoImage(source, master=self.window)
            self.caption_item = self.canvas.create_image(0, 0, anchor='nw', image=self.caption_image, tags='caption')
        bounds = self.canvas.bbox(self.caption_item)
        caption_height = max(23, bounds[3] + 1) if bounds else 23
        dock_width = min(270, width - 8)
        dock_left = (width - dock_width) // 2
        self.canvas.itemconfigure(self.dock_item, width=dock_width - 16)
        self.canvas.coords(self.dock_item, dock_left + 8, caption_height + 3)
        if self.room.notice.get():
            self.status.pack(fill='x', pady=(4, 0))
        else:
            self.status.pack_forget()
        focused = self.window.focus_displayof() is self.entry
        expanded = self.hover or self.composing or focused or self.room.voice.dictate.is_set()
        self.canvas.itemconfigure(self.dock_item, state='normal' if expanded else 'hidden')
        # Keep the hover target stable while the dock is hidden.
        height = caption_height + self.dock.winfo_reqheight() + 7
        rim = dock_left, dock_width, caption_height, height, expanded
        if rim != getattr(self, 'rim', None):
            self.rim = rim
            self.canvas.delete('glass')
            if expanded:
                left, top, right, bottom = dock_left, caption_height, dock_left + dock_width, height - 1
                radius = 10
                self.canvas.create_polygon(
                    left, top+radius, left, top, left+radius, top, right-radius, top,
                    right, top, right, top+radius, right, bottom-radius, right, bottom,
                    right-radius, bottom, left+radius, bottom, left, bottom, left, bottom-radius,
                    smooth=True, splinesteps=18, fill=GLASS, outline=GLASS_RIM, tags='glass')
                self.canvas.tag_lower('glass')
        paused = music['paused']
        if paused != getattr(self, 'last_paused', None):
            self.last_paused = paused
            self.menu.entryconfigure(self.pause_index, label='继续' if paused else '暂停')
        pet = self.root.winfo_x(), self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height()
        x, y = caption_origin(pet, (self.root.winfo_screenwidth(), self.root.winfo_screenheight()), (width, height), self.position)
        geometry = f'{width}x{height}+{x}+{y}'
        if geometry != self.geometry:
            self.geometry = geometry
            self.window.geometry(geometry)
        if not self.window.winfo_viewable():
            self.window.deiconify()
