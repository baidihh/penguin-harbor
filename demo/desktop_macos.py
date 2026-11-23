"""The macOS desktop companion, hosted in a transparent AppKit panel."""

import queue
import time

import objc
from Foundation import NSData
from .desktop_room_macos import MacDesktopRoom
from .penguin_sprite import png_bytes, with_earbuds, worn_earbuds
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
    NSBezierPath, NSColor, NSEvent, NSFloatingWindowLevel, NSFont, NSImage,
    NSImageScaleProportionallyUpOrDown, NSImageView, NSMakeRect, NSMenu,
    NSMenuItem, NSPanel, NSScreen, NSTextField, NSTimer, NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
)


class PetView(NSView):
    def mouseDown_(self, event):
        self.pet.drag_start = NSEvent.mouseLocation()
        self.pet.window_start = self.pet.panel.frame().origin
        self.pet.previous_pointer_x = self.pet.drag_start.x
        if event.clickCount() == 2:
            self.pet.cancel_knock()
            self.pet.play_animation('jumping')
            self.pet.app.open_room()

    def mouseDragged_(self, event):
        pointer = NSEvent.mouseLocation()
        start = self.pet.drag_start
        origin = self.pet.window_start
        self.pet.panel.setFrameOrigin_((origin.x + pointer.x - start.x, origin.y + pointer.y - start.y))
        delta = pointer.x - self.pet.previous_pointer_x
        if delta:
            self.pet.play_animation('running-right' if delta > 0 else 'running-left')
        self.pet.previous_pointer_x = pointer.x

    def mouseUp_(self, event):
        pointer = NSEvent.mouseLocation()
        start = self.pet.drag_start
        if self.pet.animation.startswith('running'):
            self.pet.play_animation('idle')
        if event.clickCount() == 1 and abs(pointer.x - start.x) + abs(pointer.y - start.y) < 5:
            self.pet.knock_at = time.monotonic() + NSEvent.doubleClickInterval()

    def scrollWheel_(self, event):
        delta = event.scrollingDeltaY()
        if delta:
            self.pet.zoom(0.1 if delta > 0 else -0.1)

    def rightMouseDown_(self, event):
        NSMenu.popUpContextMenu_withEvent_forView_(self.pet.menu, event, self)

    def openRoom_(self, sender):
        self.pet.app.open_room()

    def openChat_(self, sender):
        self.pet.room.open('chat')

    def openMusic_(self, sender):
        self.pet.room.open('music')

    def resetCaption_(self, sender):
        self.pet.room.overlay.reset_position()

    def zoomIn_(self, sender):
        self.pet.zoom(0.1)

    def zoomOut_(self, sender):
        self.pet.zoom(-0.1)

    def hidePet_(self, sender):
        self.pet.hide()

    def quitPet_(self, sender):
        self.pet.app.stop()

    def tick_(self, timer):
        self.pet.tick()


class SpriteView(NSImageView):
    def hitTest_(self, point):
        return None


class PillView(NSView):
    def drawRect_(self, rect):
        NSColor.whiteColor().setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 10, 10).fill()

    def hitTest_(self, point):
        return None


class DesktopPet:
    def __init__(self, app):
        self.app = app
        self.application = NSApplication.sharedApplication()
        self.application.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        screen = NSScreen.mainScreen().visibleFrame()
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(screen.origin.x + screen.size.width - 140, screen.origin.y + 14, 120, 120),
            style, NSBackingStoreBuffered, False,
        )
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        self.view = PetView.alloc().initWithFrame_(self.panel.contentView().bounds())
        self.view.pet = self
        self.panel.setContentView_(self.view)
        self.room = MacDesktopRoom(self)
        self.sprite = SpriteView.alloc().initWithFrame_(NSMakeRect(0, 0, 120, 120))
        self.sprite.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self.view.addSubview_(self.sprite)
        self.frames = self.load_frames('')
        self.earbud_frames = {'': self.frames}
        self.emoji = NSTextField.labelWithString_('🐧')
        self.view.addSubview_(self.emoji)
        self.emoji.setHidden_(bool(self.frames))
        self.animation = 'idle'
        self.animation_started = time.monotonic()
        self.update_animation(self.animation_started)

        self.bubble = PillView.alloc().initWithFrame_(NSMakeRect(0, 0, 96, 24))
        self.label = NSTextField.labelWithString_('')
        self.label.setFont_(NSFont.systemFontOfSize_weight_(10, 0.3))
        self.label.setTextColor_(NSColor.colorWithRed_green_blue_alpha_(0.06, 0.2, 0.28, 1))
        self.bubble.addSubview_(self.label)
        self.view.addSubview_(self.bubble)

        self.menu = NSMenu.alloc().init()
        for title, action in (
            ('和企鹅说话', 'openChat:'), ('音乐与播放队列', 'openMusic:'),
            ('字幕回到企鹅旁', 'resetCaption:'),
            ('去企鹅的港湾', 'openRoom:'), ('放大一点', 'zoomIn:'), ('缩小一点', 'zoomOut:'),
            ('收起桌宠', 'hidePet:'), ('退出企鹅', 'quitPet:'),
        ):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, '')
            item.setTarget_(self.view)
            self.menu.addItem_(item)

        self.scale = None
        self.pill_label = None
        self.last_message = 0
        self.last_voice = False
        self.last_case_event = None
        self.case_animation = False
        self.knock_at = None
        self.timer = None
        label = self.app.pet.snapshot()['activity_label']
        self.label.setStringValue_(label)
        self.layout(self.app.pet.snapshot()['pet_scale'], label)
        self.show()

    def load_frames(self, worn):
        result = {}
        for name, images in self.app.frames.items():
            result[name] = []
            for frame in images:
                png = png_bytes(with_earbuds(frame, worn))
                data = NSData.dataWithBytes_length_(png, len(png))
                result[name].append(NSImage.alloc().initWithData_(data))
        return result

    def play_animation(self, name):
        if name != self.animation:
            self.animation = name
            self.animation_started = time.monotonic()
            self.update_animation(self.animation_started)

    def update_animation(self, now):
        if not self.frames:
            return
        frames = self.frames[self.animation]
        index = int((now - self.animation_started) * (4 if self.animation == 'idle' else 9))
        if self.animation in ('waving', 'jumping') and index >= len(frames):
            self.play_animation('idle')
            return
        self.sprite.setImage_(frames[index % len(frames)])

    def cancel_knock(self):
        self.knock_at = None

    def finish_click(self):
        self.cancel_knock()
        self.play_animation('waving')
        self.room.open('chat')

    def zoom(self, delta):
        scale = self.app.pet.snapshot()['pet_scale']
        self.app.pet.act('scale', value=round(max(0.5, min(2, scale + delta)), 2))

    def show(self):
        self.panel.orderFrontRegardless()
        self.app.pet.pet_visible = True

    def hide(self):
        self.panel.orderOut_(None)
        self.room.overlay.hide()
        if self.room.window is not None:
            self.room.window.orderOut_(None)
        self.app.pet.pet_visible = False

    def layout(self, scale, label):
        size = round(120 * scale)
        self.label.sizeToFit()
        pill_width = max(56, round(self.label.frame().size.width) + 20)
        pill_height = round(self.label.frame().size.height) + 8
        width = max(size, pill_width)
        height = size + pill_height + 6
        frame = self.panel.frame()
        screen = (self.panel.screen() or NSScreen.mainScreen()).visibleFrame()
        x = frame.origin.x + frame.size.width - width
        x = max(screen.origin.x, min(x, screen.origin.x + screen.size.width - width))
        y = max(screen.origin.y, min(frame.origin.y, screen.origin.y + screen.size.height - height))
        self.panel.setFrame_display_(NSMakeRect(x, y, width, height), True)
        self.sprite.setFrame_(NSMakeRect(width - size, 0, size, size))
        if not self.frames:
            self.emoji.setFont_(NSFont.systemFontOfSize_(size * 0.7))
            self.emoji.setFrame_(NSMakeRect(width - size, 0, size, size))
        self.bubble.setFrame_(NSMakeRect((width - pill_width) / 2, height - pill_height, pill_width, pill_height))
        self.label.setFrame_(NSMakeRect(10, (pill_height - self.label.frame().size.height) / 2,
                                       pill_width - 20, self.label.frame().size.height))
        self.bubble.setHidden_(not label)
        self.scale = scale
        self.pill_label = label

    def tick(self):
        if self.app.stopped.is_set():
            self.timer.invalidate()
            self.hide()
            # Wake AppKit's event loop so stop_ returns without terminating Python.
            self.application.stop_(None)
            objc.lookUpClass('NSRunLoop').mainRunLoop().performBlock_(lambda: None)
            return

        while True:
            try:
                command = self.app.commands.get_nowait()
            except queue.Empty:
                break
            if command == 'show_pet':
                self.show()
            elif command == 'hide_pet':
                self.hide()

        now = time.monotonic()
        if self.knock_at is not None and now >= self.knock_at:
            self.finish_click()

        state = self.app.pet.snapshot()
        self.room.update(state)
        life = state['case_companion']
        worn = worn_earbuds(life['ears'])
        if worn not in self.earbud_frames:
            self.earbud_frames[worn] = self.load_frames(worn)
        self.frames = self.earbud_frames[worn]
        event_key = (life['source'], life['event_id'])
        if event_key != self.last_case_event:
            self.last_case_event = event_key
            self.play_animation(life['animation'])
            self.case_animation = life['animation'] != 'idle'
            if life['last_event'] and life['last_event']['kind'] == 'taken_out' and self.case_animation:
                self.show()
        elif self.case_animation and life['animation'] == 'idle':
            self.play_animation('idle')
            self.case_animation = False
        self.panel.setAlphaValue_(0.65 if life['phase'] == 'sleeping' else 1.0)
        voice = self.app.voice
        if voice and self.last_voice and not state['voice']:
            voice.cancel()
        self.last_voice = state['voice']
        message = state['message']
        if message['id'] != self.last_message:
            if voice and message.get('audible', True) and (state['voice'] or message['preview']):
                voice.say(state['voice_id'], message['speech'])
            elif voice:
                voice.cancel()
            self.last_message = message['id']
        self.update_animation(now)
        label = life['label'] if life['phase'] in ('peeking', 'home', 'sleeping') else state['activity_label']
        if state['pet_scale'] != self.scale or label != self.pill_label:
            self.label.setStringValue_(label)
            self.layout(state['pet_scale'], label)

    def run(self):
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.12, self.view, 'tick:', None, True,
        )
        self.application.run()
