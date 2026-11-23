"""A draggable, transparent, always-on-top desktop companion."""

from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk
from .desktop_room import DesktopRoom
from .desktop_overlay import MENU_COLORS
from .penguin_sprite import with_earbuds, worn_earbuds
from .voice import VOICES


BASE_SIZE = 170
EDGE = 10
PILL_PAD_X = 9
PILL_PAD_Y = 4
PILL_RADIUS = 10
PILL_GAP = 6
FONT_FAMILY = 'Microsoft YaHei UI'
PILL_FILL = '#ffffff'
PILL_LINE = '#cfe0ee'
PILL_INK = '#0f3248'


def hard_alpha(frame, size):
    """Flatten the sprite's soft alpha so the colour-key window cannot tint its edges."""
    width = max(1, round(size * frame.width / frame.height))
    scaled = frame.convert('RGBA').convert('RGBa').resize((width, size), Image.Resampling.LANCZOS).convert('RGBA')
    scaled.putalpha(scaled.getchannel('A').point(lambda value: 255 if value >= 128 else 0))
    return scaled


def draw_pill(canvas, left, top, right, bottom, radius, tags):
    """A crisp rounded pill built from canvas primitives, so no alpha edge is needed."""
    body = {'fill': PILL_FILL, 'outline': '', 'tags': tags}
    canvas.create_rectangle(left + radius, top, right - radius, bottom, **body)
    canvas.create_rectangle(left, top + radius, left + radius, bottom - radius, **body)
    canvas.create_rectangle(right - radius, top + radius, right, bottom - radius, **body)
    for x, y, start in ((left, top, 90), (right - 2 * radius, top, 0),
                        (left, bottom - 2 * radius, 180), (right - 2 * radius, bottom - 2 * radius, 270)):
        box = (x, y, x + 2 * radius, y + 2 * radius)
        canvas.create_arc(*box, start=start, extent=90, style='pieslice', **body)
        canvas.create_arc(*box, start=start, extent=90, style='arc', outline=PILL_LINE, tags=tags)
    line = {'fill': PILL_LINE, 'width': 1, 'tags': tags}
    canvas.create_line(left + radius, top, right - radius, top, **line)
    canvas.create_line(left + radius, bottom, right - radius, bottom, **line)
    canvas.create_line(left, top + radius, left, bottom - radius, **line)
    canvas.create_line(right, top + radius, right, bottom - radius, **line)


class DesktopPet:
    def __init__(self, app):
        self.app = app
        self.root = tk.Tk()
        self.root.title('企鹅 · 桌面精灵')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        windowing_system = self.root.tk.call('tk', 'windowingsystem')
        background = '#ff00fe'
        if windowing_system == 'aqua':
            background = 'systemTransparent'
            self.root.wm_attributes('-transparent', True)
        elif windowing_system == 'win32':
            self.root.wm_attributes('-transparentcolor', background)
        else:
            background = '#fffbee'
        self.root.configure(bg=background)
        self.canvas = tk.Canvas(self.root, width=BASE_SIZE + EDGE * 2, height=BASE_SIZE + EDGE * 2,
                                bg=background, highlightthickness=0)
        self.canvas.pack()
        self.label_font = tkfont.Font(root=self.root, family=FONT_FAMILY, size=9, weight='bold')
        self.pill_height = self.label_font.metrics('linespace') + PILL_PAD_Y * 2
        self.sources = app.frames.get('idle', ())
        self.animation_sources = app.frames
        self.earbud_frames = {}
        self.last_case_event = None
        self.sleeping = False
        self.frames = []
        self.scale = None
        self.opened = False
        self.window_width = BASE_SIZE + EDGE * 2
        self.window_height = BASE_SIZE + EDGE * 2
        self.scale_value = tk.DoubleVar(value=self.app.pet.snapshot()['pet_scale'])
        self.voice_value = tk.StringVar(value=self.app.pet.snapshot()['voice_id'])
        if self.sources:
            self.sprite = self.canvas.create_image(0, 0, anchor='s', tags='pet')
        else:
            self.sprite = self.canvas.create_text(0, 0, anchor='s', text='🐧', tags='pet')
        self.pill = self.canvas.create_text(0, 0, anchor='center', text='', fill=PILL_INK,
                                            font=self.label_font, tags='status')
        self.last_label = ''
        self.drag = None
        self.room = DesktopRoom(self)
        self.panels = None
        panel_python = Path(__file__).with_name('.runtime') / 'panels-env' / 'Scripts' / 'python.exe'
        if windowing_system == 'win32' and (panel_python.parents[1] / 'Lib/site-packages/PySide6/QtWebEngineWidgets.pyd').exists():
            from .desktop_panels import DesktopPanels
            self.panels = DesktopPanels(self, panel_python)
        self.menu = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, 10), **MENU_COLORS)
        self.menu.add_command(label='和企鹅说话', command=self.room.open)
        self.menu.add_command(label='语音聊天', command=lambda: self.room.open(dictate=True))
        self.menu.add_checkbutton(label='语音唤醒「你好企鹅」', variable=self.room.wake_enabled,
                                  command=self.room.toggle_wake)
        self.voice_menu = tk.Menu(self.menu, tearoff=0, font=(FONT_FAMILY, 9), **MENU_COLORS)
        for identity, (label, speaker, pitch) in VOICES.items():
            self.voice_menu.add_radiobutton(label=label, variable=self.voice_value, value=identity,
                                            command=self.select_voice)
        self.voice_menu.add_separator()
        self.voice_menu.add_command(label='试听当前声音', command=lambda: self.app.pet.act('voice_preview'))
        self.menu.add_cascade(label='说话音色', menu=self.voice_menu)
        self.menu.add_separator()
        self.menu.add_command(label='暂停播放', command=self.room.toggle_pause)
        self.pause_menu_index = self.menu.index('end')
        self.menu.add_command(label='上一首', command=lambda: self.room.step(-1))
        self.menu.add_command(label='下一首', command=lambda: self.room.step(1))
        self.menu.add_command(label='播放队列', command=lambda: self.room.open('音乐'))
        self.menu.add_checkbutton(label='桌面歌词', variable=self.room.lyrics_visible)
        self.menu.add_separator()
        self.menu.add_command(label='去企鹅的港湾', command=self.app.open_room)
        self.menu.add_separator()
        self.menu.add_command(label='放大一点', command=lambda: self.zoom(0.1))
        self.menu.add_command(label='缩小一点', command=lambda: self.zoom(-0.1))
        sizes = tk.Menu(self.menu, tearoff=0, **MENU_COLORS)
        for value in (0.5, 0.75, 1, 1.5, 2):
            sizes.add_radiobutton(label=f'{round(value * 100)}%', variable=self.scale_value, value=value,
                                  command=lambda: self.app.pet.act('scale', value=self.scale_value.get()))
        self.menu.add_cascade(label='精灵大小', menu=sizes)
        self.menu.add_separator()
        self.menu.add_command(label='收起桌面精灵', command=self.hide)
        self.menu.add_command(label='退出 Demo', command=self.app.stop)
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.move)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        menu_events = ('<Button-2>', '<Control-Button-1>') if windowing_system == 'aqua' else ('<Button-3>',)
        for event_pattern in menu_events:
            self.canvas.bind(event_pattern, self.open_menu)
        self.canvas.bind('<MouseWheel>', lambda event: self.zoom(0.1 if event.delta > 0 else -0.1))
        self.root.protocol('WM_DELETE_WINDOW', self.app.stop)
        self.app.pet.pet_visible = True
        self.last_label = self.app.pet.snapshot()['activity_label']
        self.canvas.itemconfigure(self.pill, text=self.last_label)
        self.apply_scale(self.app.pet.snapshot()['pet_scale'])
        self.root.after(100, self.tick)

    def zoom(self, delta):
        value = self.app.pet.snapshot()['pet_scale']
        self.app.pet.act('scale', value=round(max(0.5, min(2, value + delta)), 2))

    def select_voice(self):
        self.app.pet.act('voice_select', value=self.voice_value.get())
        self.app.pet.act('voice_preview')

    def open_menu(self, event):
        if self.panels:
            self.panels.show('menu')
            return 'break'
        paused = self.app.music.snapshot().get('paused', False)
        self.menu.entryconfigure(self.pause_menu_index, label='继续播放' if paused else '暂停播放')
        self.menu.tk_popup(event.x_root, event.y_root)
        return 'break'

    def apply_scale(self, scale):
        size = round(BASE_SIZE * scale)
        self.earbud_frames = {'': self.load_frames(size, '')}
        self.frames = self.earbud_frames[''].get('idle', [])
        self.layout(scale)
        if self.frames:
            self.canvas.itemconfigure(self.sprite, image=self.frames[0])
        else:
            self.canvas.itemconfigure(self.sprite, font=('Segoe UI Emoji', round(size * 0.55)))

    def load_frames(self, size, worn):
        return {
            name: [ImageTk.PhotoImage(hard_alpha(with_earbuds(frame, worn), size)) for frame in frames]
            for name, frames in self.animation_sources.items()
        }

    def layout(self, scale):
        size = round(BASE_SIZE * scale)
        if self.opened:
            right = self.root.winfo_x() + self.window_width
            bottom = self.root.winfo_y() + self.window_height
        else:
            right = self.root.winfo_screenwidth() - 24
            bottom = self.root.winfo_screenheight() - 60
        width = max(size, self.pill_width()) + EDGE * 2
        height = size + PILL_GAP + self.pill_height + EDGE * 2
        x = max(0, min(self.root.winfo_screenwidth() - width, right - width))
        y = max(0, min(self.root.winfo_screenheight() - height - 40, bottom - height))
        self.root.geometry(f'{width}x{height}+{x}+{y}')
        self.canvas.configure(width=width, height=height)
        self.canvas.coords(self.sprite, width / 2, EDGE + size)
        self.canvas.coords(self.pill, width / 2, EDGE + size + PILL_GAP + self.pill_height / 2)
        self.window_width, self.window_height = width, height
        self.opened = True
        self.scale = scale
        self.scale_value.set(scale)
        self.draw_status()

    def pill_width(self):
        return round(self.label_font.measure(self.canvas.itemcget(self.pill, 'text'))) + PILL_PAD_X * 2

    def draw_status(self):
        self.canvas.delete('status-box')
        text = self.canvas.itemcget(self.pill, 'text')
        if not text:
            return
        width = self.pill_width()
        center_x = self.window_width / 2
        top = EDGE + round(BASE_SIZE * self.scale) + PILL_GAP
        draw_pill(self.canvas, center_x - width / 2, top, center_x + width / 2, top + self.pill_height,
                  PILL_RADIUS, 'status-box')
        self.canvas.tag_lower('status-box', 'status')

    def press(self, event):
        self.drag = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def move(self, event):
        if self.drag:
            x, y, origin_x, origin_y = self.drag
            self.root.geometry(f'+{max(0, origin_x + event.x_root - x)}+{max(0, origin_y + event.y_root - y)}')

    def release(self, event):
        if self.drag and abs(event.x_root - self.drag[0]) + abs(event.y_root - self.drag[1]) < 5:
            if self.panels:
                self.panels.show('dock')
            else:
                self.room.open()
        self.drag = None

    def hide(self):
        self.root.withdraw()
        self.app.pet.pet_visible = False

    def tick(self):
        if self.app.stopped.is_set():
            if self.panels:
                self.panels.close()
            self.room.voice.close()
            self.root.destroy()
            return
        try:
            while True:
                command = self.app.commands.get_nowait()
                if command == 'show_pet':
                    self.root.deiconify()
                    self.app.pet.pet_visible = True
                elif command == 'hide_pet':
                    self.hide()
        except queue.Empty:
            pass
        state = self.app.pet.snapshot()
        life = state['case_companion']
        event_key = (life['source'], life['event_id'])
        if event_key != self.last_case_event:
            self.last_case_event = event_key
            if life['last_event'] and life['last_event']['kind'] == 'taken_out' and life['animation'] != 'idle':
                self.root.deiconify()
                self.app.pet.pet_visible = True
        sleeping = life['phase'] == 'sleeping'
        if sleeping != self.sleeping:
            self.sleeping = sleeping
            self.root.attributes('-alpha', 0.65 if sleeping else 1.0)
        if self.scale != state['pet_scale']:
            self.apply_scale(state['pet_scale'])
        if self.voice_value.get() != state['voice_id']:
            self.voice_value.set(state['voice_id'])
        label = life['label'] if life['phase'] in ('peeking', 'home', 'sleeping') else state['activity_label']
        if self.room.wake_enabled.get():
            label += ' · 聆听中'
        if label != self.last_label:
            self.last_label = label
            self.canvas.itemconfigure(self.pill, text=label)
            self.layout(state['pet_scale'])
        worn = worn_earbuds(life['ears'])
        if worn not in self.earbud_frames:
            self.earbud_frames[worn] = self.load_frames(round(BASE_SIZE * self.scale), worn)
        frames = self.earbud_frames[worn].get(life['animation'], self.frames)
        if frames:
            frame = int(time.monotonic() * (4 if life['animation'] == 'idle' else 9)) % len(frames)
            self.canvas.itemconfigure(self.sprite, image=frames[frame])
        if self.panels and self.panels.proc.poll() is not None:
            self.panels.close()
            self.panels = None
        self.room.update(state)
        self.root.after(120, self.tick)

    def run(self):
        self.root.mainloop()
