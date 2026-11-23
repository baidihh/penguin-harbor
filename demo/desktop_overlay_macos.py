"""A click-through conversation and lyric overlay with a movable caption."""

from time import monotonic

from AppKit import (
    NSAppearance, NSAppearanceNameDarkAqua, NSBackingStoreBuffered, NSButton, NSColor,
    NSCursor, NSEvent, NSFloatingWindowLevel, NSFont, NSForegroundColorAttributeName, NSFontAttributeName,
    NSImage, NSLineBreakByTruncatingTail, NSMakeRect, NSPanel, NSScreen, NSShadow,
    NSShadowAttributeName, NSStrokeColorAttributeName, NSStrokeWidthAttributeName,
    NSTextField, NSView, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowStyleMaskBorderless,
)
from Foundation import NSAttributedString, NSMouseInRect


def caption_origin(pet, screen, width, height, position=None):
    left, bottom = screen.origin
    right, top = left + screen.size.width, bottom + screen.size.height
    above = pet.origin.y + pet.size.height + 4
    x, y = (position[0], position[1] - height) if position is not None else (
        pet.origin.x + pet.size.width - width,
        above if above + height <= top - 4 else pet.origin.y - height - 4,
    )
    return max(left + 8, min(x, right - width - 8)), max(bottom + 4, min(y, top - height - 4))


class DraggableCaption(NSTextField):
    def acceptsFirstMouse_(self, event):
        return True

    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), NSCursor.openHandCursor())

    def mouseDown_(self, event):
        self.overlay.begin_drag(NSEvent.mouseLocation())

    def mouseDragged_(self, event):
        self.overlay.drag(NSEvent.mouseLocation())

    def mouseUp_(self, event):
        self.overlay.end_drag()


class OverlayPanel(NSPanel):
    def canBecomeKeyWindow(self):
        return True

    def cancelOperation_(self, sender):
        self.overlay.dismiss()


class MacDesktopOverlay:
    def __init__(self, room):
        self.room = room
        self.desktop = room.desktop
        self.app = room.app
        self.panel = None
        self.scale = 1.0
        self.conversation_open = False
        self.lyrics_visible = True
        self.composing = False
        self.expanded = False
        self.last_caption = None
        self.last_turn = None
        self.show_reply_until = 0
        self.last_frame = None
        self.last_paused = None
        self.position = self.app.pet.snapshot().get('mac_caption_position')
        self.drag_start = None
        self.moved = False

    def _button(self, parent, symbol, label, action):
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, label)
        button = NSButton.buttonWithImage_target_action_(image, self.room.target, 'performRoomAction:')
        button.setBordered_(False)
        button.setContentTintColor_(NSColor.whiteColor())
        button.setToolTip_(label)
        button.setAccessibilityLabel_(label)
        button.setTag_(len(self.room.actions))
        self.room.actions.append(action)
        parent.addSubview_(button)
        return button

    def _build(self):
        self.panel = OverlayPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 400, 120), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False,
        )
        self.panel.overlay = self
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces |
                                          NSWindowCollectionBehaviorFullScreenAuxiliary)
        root = self.panel.contentView()
        self.caption = DraggableCaption.alloc().initWithFrame_(NSMakeRect(0, 70, 400, 28))
        self.caption.overlay = self
        self.caption.setEditable_(False)
        self.caption.setSelectable_(False)
        self.caption.setBezeled_(False)
        self.caption.setDrawsBackground_(False)
        self.caption.cell().setWraps_(True)
        self.caption.setMaximumNumberOfLines_(3)
        self.caption.setLineBreakMode_(NSLineBreakByTruncatingTail)
        root.addSubview_(self.caption)
        self.controls = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 68))
        self.controls.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self.controls.setWantsLayer_(True)
        self.controls.layer().setCornerRadius_(12)
        self.controls.layer().setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(.08, .12, .16, .89).CGColor())
        root.addSubview_(self.controls)
        self.entry = self.room._field(self.controls, '和企鹅说点什么…', 12, 36, 342, self.send)
        self.entry.setBezeled_(False)
        self.entry.setDrawsBackground_(False)
        self.entry.setTextColor_(NSColor.whiteColor())
        self.send_button = self._button(self.controls, 'arrow.up.circle.fill', '发送', self.send)
        self.previous = self._button(self.controls, 'backward.end.fill', '上一首', lambda: self.room.step(-1))
        self.pause = self._button(self.controls, 'pause.fill', '暂停或继续', lambda: self.room.submit(self.app.music.toggle_pause))
        self.next = self._button(self.controls, 'forward.end.fill', '下一首', lambda: self.room.step(1))
        self.buttons = [
            self.previous, self.pause, self.next,
            self._button(self.controls, 'music.note.list', '播放队列', lambda: self.room.open('music')),
            self._button(self.controls, 'text.bubble', '会话记录', lambda: self.room.open('history')),
            self._button(self.controls, 'minus', '缩小字幕', lambda: self.resize(-.1)),
            self._button(self.controls, 'plus', '放大字幕', lambda: self.resize(.1)),
            self._button(self.controls, 'xmark', '收起字幕', self.dismiss),
        ]
        self.controls.setHidden_(True)

    def open(self):
        if self.panel is None:
            self._build()
        self.conversation_open = True
        self.lyrics_visible = True
        self.composing = True
        self.update(self.app.pet.snapshot(), self.app.music.snapshot())
        self.room.activate()
        self.panel.makeKeyAndOrderFront_(None)
        self.panel.makeFirstResponder_(self.entry)

    def dismiss(self):
        self.conversation_open = False
        self.lyrics_visible = False
        self.hide()

    def hide(self):
        self.composing = False
        self.drag_start = None
        self.moved = False
        if self.panel is not None:
            self.panel.orderOut_(None)

    def resize(self, amount):
        self.scale = min(1.55, max(.8, round(self.scale + amount, 2)))

    def begin_drag(self, pointer):
        self.drag_start = pointer, self.panel.frame().origin
        self.moved = False

    def drag(self, pointer):
        if self.drag_start is None:
            return
        start, origin = self.drag_start
        dx, dy = pointer.x - start.x, pointer.y - start.y
        if self.moved or abs(dx) + abs(dy) >= 3:
            self.moved = True
            self.panel.setFrameOrigin_((origin.x + dx, origin.y + dy))

    def end_drag(self):
        self.drag_start = None
        if self.moved:
            self.moved = False
            frame = self.panel.frame()
            position = frame.origin.x, frame.origin.y + frame.size.height
            screen = self.panel.screen()
            if screen is not None:
                x, y = caption_origin(self.desktop.panel.frame(), screen.visibleFrame(),
                                      frame.size.width, frame.size.height, position)
                position = x, y + frame.size.height
            self._save_position(position)

    def reset_position(self):
        self._save_position(None)

    def _save_position(self, position):
        self.position = position
        self.last_frame = None
        try:
            self.app.pet.set_mac_caption_position(position)
        except OSError:
            self.room.set_chat_notice('字幕位置未能保存。')

    def send(self):
        if self.room.send_text(str(self.entry.stringValue()).strip()):
            self.entry.setStringValue_('')
            self.panel.makeFirstResponder_(None)
            self.composing = False
            self.panel.resignKeyWindow()

    def _caption(self, state, music):
        conversation = state.get('conversation') or []
        turn = conversation[-1] if conversation else None
        if turn != self.last_turn:
            self.last_turn = turn
            self.show_reply_until = monotonic() + 7
        if self.conversation_open and turn and (
                self.composing or turn['reply_state'] == 'pending' or monotonic() < self.show_reply_until):
            return turn['reply']
        if music['playing'] or music['paused']:
            lyric = music.get('lyric')
            if lyric:
                return lyric
        if self.conversation_open:
            return turn['reply'] if turn else '今天想聊什么？'
        return self.room._title(music['current'])

    def _layout(self, text, music):
        pet = self.desktop.panel
        frame = pet.frame()
        screen = pet.screen() or NSScreen.mainScreen()
        if self.position is not None:
            screen = next((item for item in NSScreen.screens()
                           if NSMouseInRect(self.position, item.frame(), False)), screen)
        screen = screen.visibleFrame()
        width = min(410 * self.scale, screen.size.width - 24)
        font = NSFont.systemFontOfSize_weight_(17 * self.scale, .32)
        signature = text, width, self.scale
        if signature != self.last_caption:
            shadow = NSShadow.alloc().init()
            shadow.setShadowColor_(NSColor.blackColor().colorWithAlphaComponent_(.78))
            shadow.setShadowBlurRadius_(4)
            shadow.setShadowOffset_((0, -1))
            self.caption.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
                text, {NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.whiteColor(),
                       NSStrokeColorAttributeName: NSColor.blackColor(), NSStrokeWidthAttributeName: -1.7,
                       NSShadowAttributeName: shadow},
            ))
            self.last_caption = signature
        size = self.caption.cell().cellSizeForBounds_(NSMakeRect(0, 0, width - 20, 140))
        text_height = min(max(28, size.height + 8), 71 * self.scale)
        height = 70 + text_height
        x, y = caption_origin(frame, screen, width, height, self.position)
        geometry = x, y, width, height
        if geometry != self.last_frame:
            self.last_frame = geometry
            self.panel.setFrame_display_(NSMakeRect(*geometry), True)
            self.caption.setFrame_(NSMakeRect(10, 70, width - 20, text_height))
            self.controls.setFrame_(NSMakeRect(4, 1, width - 8, 66))
            self.entry.setFrame_(NSMakeRect(12, 35, width - 64, 25))
            self.send_button.setFrame_(NSMakeRect(width - 49, 34, 32, 27))
            gap = (width - 24) / len(self.buttons)
            for index, button in enumerate(self.buttons):
                button.setFrame_(NSMakeRect(8 + gap * index, 4, gap, 26))
        for button in (self.previous, self.next):
            button.setEnabled_(bool(music['songs']))
        self.pause.setEnabled_(music['playing'] or music['paused'])
        if music['paused'] != self.last_paused:
            self.last_paused = music['paused']
            self.pause.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                'play.fill' if music['paused'] else 'pause.fill', '继续' if music['paused'] else '暂停'))

    def update(self, state, music):
        active_music = self.lyrics_visible and (music['playing'] or music['paused'])
        if not self.desktop.panel.isVisible() or not (self.conversation_open or active_music):
            self.hide()
            return
        if self.panel is None:
            self._build()
        if self.drag_start is not None:
            return
        text = self.room.overlay_notice or self._caption(state, music)
        self._layout(text, music)
        pointer = NSEvent.mouseLocation()
        frame = self.panel.frame()
        caption = self.caption.frame()
        hover_width = min(frame.size.width, max(90, self.caption.cell().cellSize().width + 20))
        caption_area = NSMakeRect(frame.origin.x, frame.origin.y + caption.origin.y - 4,
                                  hover_width, caption.size.height + 4)
        if self.composing and self.panel.isVisible() and not self.panel.isKeyWindow():
            self.composing = False
        hover_area = frame if self.expanded else caption_area
        editing = self.panel.isKeyWindow() and self.entry.currentEditor() is not None
        self.expanded = self.composing or editing or NSMouseInRect(pointer, hover_area, False)
        self.controls.setHidden_(not self.expanded)
        self.panel.setIgnoresMouseEvents_(not self.expanded)
        if not self.panel.isVisible():
            self.panel.orderFrontRegardless()
