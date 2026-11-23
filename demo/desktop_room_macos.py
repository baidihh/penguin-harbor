"""AppKit conversation and music controls for the macOS penguin."""

from datetime import datetime
from collections import deque
import queue
import shlex
import subprocess
import sys
import threading
from time import monotonic

from AppKit import (
    NSAlert, NSAlertFirstButtonReturn, NSAppearance, NSAppearanceNameDarkAqua,
    NSBackingStoreBuffered, NSButton, NSColor, NSFloatingWindowLevel, NSFont, NSImage,
    NSLineBreakByTruncatingTail, NSMakeRect, NSPanel, NSPopUpButton, NSScrollView,
    NSScreen, NSSecureTextField, NSTextField, NSTextView, NSView, NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow, NSVisualEffectMaterialPopover, NSVisualEffectStateActive,
    NSVisualEffectView, NSWindowStyleMaskBorderless,
)
from Foundation import NSObject
from .desktop_overlay_macos import MacDesktopOverlay


class RoomActions(NSObject):
    def performRoomAction_(self, sender):
        self.room.actions[sender.tag()]()


class RoomPanel(NSPanel):
    def canBecomeKeyWindow(self):
        return True

    def cancelOperation_(self, sender):
        self.orderOut_(None)


class MacDesktopRoom:
    WIDTH = 518
    HEIGHT = 366

    def __init__(self, desktop):
        self.desktop = desktop
        self.app = desktop.app
        self.window = None
        self.actions = []
        self.target = RoomActions.alloc().init()
        self.target.room = self
        self.results = queue.Queue()
        self.search_results = []
        self.songs = ()
        self.last_conversation = None
        self.music_jobs = deque()
        self.music_busy = False
        self.configuring = False
        self.overlay_notice = ''
        self.notice_until = 0
        self.overlay = MacDesktopOverlay(self)

    def _button(self, view, title, x, y, width, action, *, symbol=None):
        if symbol:
            icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
            button = NSButton.buttonWithImage_target_action_(icon, self.target, 'performRoomAction:')
            button.setAccessibilityLabel_(title)
            button.setToolTip_(title)
            button.setBordered_(False)
        else:
            button = NSButton.buttonWithTitle_target_action_(title, self.target, 'performRoomAction:')
        button.setFrame_(NSMakeRect(x, y, width, 31))
        button.setTag_(len(self.actions))
        self.actions.append(action)
        view.addSubview_(button)
        return button

    @staticmethod
    def _label(view, text, x, y, width):
        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(x, y, width, 22))
        label.setLineBreakMode_(NSLineBreakByTruncatingTail)
        view.addSubview_(label)
        return label

    def _field(self, view, placeholder, x, y, width, action):
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, 26))
        field.setPlaceholderString_(placeholder)
        field.setTarget_(self.target)
        field.setAction_('performRoomAction:')
        field.setTag_(len(self.actions))
        self.actions.append(action)
        view.addSubview_(field)
        return field

    @staticmethod
    def _popup(view, x, y, width):
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(x, y, width, 29), False)
        view.addSubview_(popup)
        return popup

    def _build(self):
        self.window = RoomPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self.WIDTH, self.HEIGHT),
            NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False,
        )
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setReleasedWhenClosed_(False)
        self.window.setHidesOnDeactivate_(True)
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setMovableByWindowBackground_(True)
        view = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, self.WIDTH, self.HEIGHT))
        view.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        view.setMaterial_(NSVisualEffectMaterialPopover)
        view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        view.setState_(NSVisualEffectStateActive)
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(16)
        view.layer().setMasksToBounds_(True)
        self.window.setContentView_(view)
        self.history_tab = self._button(view, '会话记录', 12, 327, 80, lambda: self.open('history'))
        self.music_tab = self._button(view, '音乐', 94, 327, 58, lambda: self.open('music'))
        self._button(view, '网易云账号', 158, 327, 102, self.open_account)
        self.voice_button = self._button(view, '开启声音', 264, 327, 87, self.toggle_voice)
        self._button(view, '配置 GLM', 355, 327, 101, self.configure)
        self._button(view, '关闭面板', 473, 327, 32, lambda: self.window.orderOut_(None), symbol='xmark')
        self.chat = self._build_chat(view)
        self.music = self._build_music(view)

    def _build_chat(self, view):
        pane = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.WIDTH, 320))
        view.addSubview_(pane)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16, 75, 486, 240))
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        self.transcript = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        self.transcript.setEditable_(False)
        self.transcript.setDrawsBackground_(False)
        self.transcript.setTextColor_(NSColor.labelColor())
        self.transcript.setFont_(NSFont.systemFontOfSize_(14))
        self.transcript.setTextContainerInset_((8, 8))
        self.transcript.setVerticallyResizable_(True)
        self.transcript.setAutoresizingMask_(NSViewWidthSizable)
        self.transcript.textContainer().setWidthTracksTextView_(True)
        scroll.setDocumentView_(self.transcript)
        pane.addSubview_(scroll)
        self.entry = self._field(pane, '和企鹅说点什么…', 16, 41, 400, self.send)
        self._button(pane, '发送', 426, 37, 80, self.send)
        self.chat_status = self._label(pane, '', 16, 11, 486)
        return pane

    def _build_music(self, view):
        pane = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.WIDTH, 320))
        view.addSubview_(pane)
        self.keyword = self._field(pane, '输入歌名或歌手', 16, 278, 400, self.search)
        self._button(pane, '搜索', 426, 274, 80, self.search)
        self._label(pane, '网易云搜索结果', 16, 244, 486)
        self.matches = self._popup(pane, 12, 213, 315)
        self._button(pane, '加入队列', 329, 212, 92, self.enqueue)
        self._button(pane, '播放', 426, 212, 80, self.play)
        self._label(pane, '播放队列', 16, 174, 486)
        self.playlist = self._popup(pane, 12, 143, 315)
        self._button(pane, '移除', 329, 142, 92, self.remove)
        self._button(pane, '播放', 426, 142, 80, self.select)
        self.track = self._label(pane, '', 16, 103, 486)
        self._button(pane, '上一首', 12, 64, 90, lambda: self.step(-1))
        self.pause = self._button(pane, '暂停', 105, 64, 90, lambda: self.submit(self.app.music.toggle_pause))
        self._button(pane, '下一首', 198, 64, 90, lambda: self.step(1))
        self._button(pane, '停止', 291, 64, 90, lambda: self.submit(self.app.music.stop))
        self._button(pane, '日推', 382, 64, 62, lambda: self.play_account('daily'))
        self._button(pane, '红心', 445, 64, 62, lambda: self.play_account('liked'))
        self.music_status = self._label(pane, '', 16, 32, 486)
        self.lyric = self._label(pane, '', 16, 9, 486)
        self._fill_popup(self.matches, [], '搜索歌曲后选择')
        self._fill_popup(self.playlist, [], '队列为空')
        return pane

    def open(self, tab):
        if tab == 'chat':
            self.overlay.open()
            return
        if self.window is None:
            self._build()
        self.tab = tab
        self.chat.setHidden_(tab != 'history')
        self.music.setHidden_(tab != 'music')
        self.window.setTitle_('会话记录' if tab == 'history' else '企鹅的音乐')
        self.history_tab.setEnabled_(tab != 'history')
        self.music_tab.setEnabled_(tab != 'music')
        if not self.window.isVisible():
            frame = self.desktop.panel.frame()
            screen = (self.desktop.panel.screen() or NSScreen.mainScreen()).visibleFrame()
            x = max(screen.origin.x, min(frame.origin.x + frame.size.width - self.WIDTH,
                                         screen.origin.x + screen.size.width - self.WIDTH))
            above = frame.origin.y + frame.size.height + 8
            y = min(above, screen.origin.y + screen.size.height - self.HEIGHT - 32)
            self.window.setFrameOrigin_((x, max(screen.origin.y, y)))
        self.update(self.app.pet.snapshot())
        self.activate()
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.entry if tab == 'history' else self.keyword)

    def activate(self):
        if self.desktop.application.respondsToSelector_('activate'):
            self.desktop.application.activate()
        else:
            self.desktop.application.activateIgnoringOtherApps_(True)

    def _run(self, kind, job):
        def worker():
            try:
                self.results.put((kind, job(), None))
            except (OSError, ValueError, RuntimeError) as error:
                self.results.put((kind, None, str(error) or '操作没有完成。'))

        threading.Thread(target=worker, daemon=True).start()

    def send(self):
        if self.send_text(str(self.entry.stringValue()).strip()):
            self.entry.setStringValue_('')

    def send_text(self, text):
        if not text:
            return False
        if len(text) > 600:
            self.set_chat_notice('每条消息最多 600 字。')
            return False
        self.set_chat_notice('')
        self.app.interact('chat', text=text)
        return True

    def set_chat_notice(self, text):
        self.overlay_notice = text
        self.notice_until = monotonic() + 8
        if self.window is not None:
            self.chat_status.setStringValue_(text)

    def open_account(self):
        from . import netease_login

        script = self.app.runtime / 'netease-account.command'
        args = [sys.executable, netease_login.__file__]
        try:
            script.write_text('#!/bin/sh\n' + shlex.join(args) + '\n', encoding='utf-8')
            script.chmod(0o700)
            subprocess.Popen(['/usr/bin/open', '-a', 'Terminal', str(script.resolve())])
        except OSError:
            self.music_status.setStringValue_('无法打开网易云账号设置。')

    def play_account(self, source):
        def play():
            outcome = self.app.chat_tool(f'play_{source}_music', {})
            if not outcome['ok']:
                raise RuntimeError(outcome['error'])

        self.submit(play)

    def toggle_voice(self):
        self.app.pet.act('voice', enabled=not self.app.pet.snapshot()['voice'])

    def configure(self):
        if self.configuring:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_('配置智谱 GLM')
        alert.setInformativeText_('输入你的智谱 API 密钥。验证后保存到本机，密钥不会写入 Git。')
        alert.addButtonWithTitle_('验证并保存')
        alert.addButtonWithTitle_('取消')
        field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 26))
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)
        if alert.runModal() == NSAlertFirstButtonReturn:
            key = str(field.stringValue()).strip()
            if key:
                self.configuring = True
                self.set_chat_notice('正在验证密钥…')
                self._run('config', lambda: self.app.configure_glm(key))

    def submit(self, job, kind='music'):
        self.music_jobs.append((kind, job))
        self._next_music_job()

    def _next_music_job(self):
        if self.music_jobs and not self.music_busy:
            self.music_busy = True
            self._run(*self.music_jobs.popleft())

    def search(self):
        keyword = str(self.keyword.stringValue()).strip()
        if keyword and not self.music_busy:
            self.music_status.setStringValue_('正在搜索歌曲…')
            self.submit(lambda: self.app.music.search(keyword), 'search')

    def _selected(self, popup, songs, action):
        index = popup.indexOfSelectedItem()
        if 0 <= index < len(songs):
            song = songs[index]
            self.submit(lambda: action(song))

    def enqueue(self):
        self._selected(self.matches, self.search_results, self.app.music.enqueue)

    def play(self):
        self._selected(self.matches, self.search_results, self.app.music.play)

    def remove(self):
        self._selected(self.playlist, self.songs, lambda song: self.app.music.remove(song['id']))

    def select(self):
        self._selected(self.playlist, self.songs, lambda song: self.app.music.select(song['id']))

    def step(self, direction):
        def step_song():
            if not self.app.music.step(direction):
                raise ValueError('队列里没有可以切换的歌曲。')

        self.submit(step_song)

    @staticmethod
    def _title(song):
        return ' · '.join(filter(None, (song.get('name'), song.get('artist'))))

    def _fill_popup(self, popup, songs, empty, selected=None):
        popup.removeAllItems()
        popup.setEnabled_(bool(songs))
        if songs:
            for index, song in enumerate(songs):
                popup.addItemWithTitle_(f'{index + 1}. {self._title(song)}')
                if song['id'] == selected:
                    popup.selectItemAtIndex_(index)
        else:
            popup.addItemWithTitle_(empty)

    def update(self, state):
        while True:
            try:
                kind, value, error = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == 'config':
                self.configuring = False
                message = error or 'GLM 已连接，可以开始聊天。'
                self.set_chat_notice(message)
                continue
            self.music_busy = False
            if kind == 'search' and error is None:
                self.search_results = value
                self._fill_popup(self.matches, value, '没有找到歌曲')
            if self.window is not None:
                self.music_status.setStringValue_(error or ('没有找到歌曲。' if kind == 'search' and not value else ''))
            if error:
                self.set_chat_notice(error)
            self._next_music_job()
        if self.overlay_notice and monotonic() >= self.notice_until:
            self.overlay_notice = ''
        music = self.app.music.snapshot()
        self.overlay.update(state, music)
        if self.window is None or (not self.window.isVisible() and self.last_conversation is not None):
            return
        self.voice_button.setTitle_('关闭声音' if state['voice'] else '开启声音')
        conversation = state['timeline']
        if conversation != self.last_conversation:
            lines = [f"{row['label']}  ·  {datetime.fromtimestamp(row['time']):%m-%d %H:%M}\n{row['text']}"
                     for row in conversation]
            self.transcript.setString_('\n\n'.join(lines) or '今天想聊什么？')
            self.transcript.scrollRangeToVisible_((self.transcript.textStorage().length(), 0))
            self.last_conversation = conversation
        if not self.app.glm.ready and not self.chat_status.stringValue():
            self.chat_status.setStringValue_('先配置 GLM 密钥，即可和企鹅聊天。')
        if music['songs'] != self.songs:
            index = self.playlist.indexOfSelectedItem()
            selected = self.songs[index]['id'] if 0 <= index < len(self.songs) else None
            self.songs = music['songs']
            self._fill_popup(self.playlist, self.songs, '队列为空', selected)
        status = '已暂停' if music['paused'] else '正在播放' if music['playing'] else '已停止'
        title = self._title(music['current'])
        self.track.setStringValue_(f'{status} · {title}' if title else '还没有播放歌曲')
        self.pause.setTitle_('继续' if music['paused'] else '暂停')
        self.pause.setEnabled_(music['playing'] or music['paused'])
        self.lyric.setStringValue_(music['lyric'])
        if music['error']:
            self.music_status.setStringValue_(music['error'])
