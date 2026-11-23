"""Isolated Qt WebEngine host for transparent desktop panels."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import threading

from PySide6.QtCore import Qt, QUrl, QObject, Signal, QTimer, QPoint
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView


PAGE = Path(__file__).with_name('web') / 'windows-ui-preview.html'
URL = QUrl.fromLocalFile(str(PAGE.resolve()))
URL.setFragment('desktop')


def emit(action, value=None):
    print(json.dumps({'action': action, 'value': value}), flush=True)


class Commands(QObject):
    received = Signal(dict)


class Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, main):
        return url == URL

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith('PENGUIN ') and self.url() == URL:
            request = json.loads(message[8:])
            self.parent().request(request)


class Panel(QWebEngineView):
    def __init__(self, host, caption=False):
        super().__init__()
        self.host = host
        self.caption = caption
        self.mode = ''
        self.ready = False
        self.manual = False
        self.setWindowTitle('企鹅字幕' if caption else '企鹅桌面面板')
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
        if caption:
            flags |= Qt.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, caption)
        self.setStyleSheet('background:transparent')
        self.setContextMenuPolicy(Qt.NoContextMenu)
        page = Page(self)
        self.setPage(page)
        page.setBackgroundColor(QColor(0, 0, 0, 0))
        page.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
        self.loadFinished.connect(self.loaded)
        self.load(URL)

    def loaded(self, ok):
        if not ok:
            emit('error', '桌面面板载入失败')
            return
        self.page().runJavaScript('Boolean(window.panelReady)', self.check_ready)

    def check_ready(self, ready):
        if not ready:
            QTimer.singleShot(80, lambda: self.loaded(True))
            return
        self.ready = True
        if self.mode:
            self.present(self.mode)

    def request(self, event):
        action = event.get('action')
        if action == 'dragCaption' and self.caption:
            self.manual = True
            self.windowHandle().startSystemMove()
        elif action == 'contentHeight':
            if self.mode:
                size = max(27, min(int(event['value']) + 6, 530))
                self.resize(self.width(), size)
                self.place()
        else:
            emit(action, event.get('value'))

    def present(self, mode):
        self.mode = mode
        if not self.ready:
            return
        width, height = {'caption': (320, 54), 'dock': (318, 47), 'menu': (204, 275),
                         'options': (210, 335), 'voices': (244, 440), 'music': (356, 320), 'history': (362, 390)}[mode]
        self.resize(width, height)
        self.page().runJavaScript('window.panelShow('+json.dumps(mode)+','+json.dumps(self.host.data)+')')
        self.place()
        self.show()
        if not self.caption:
            self.raise_()
            self.activateWindow()
            self.setFocus()

    def place(self):
        if self.manual and self.caption:
            return
        p = self.host.pet_rect()
        middle = QPoint((p.left+p.right)//2, (p.top+p.bottom)//2)
        screen = self.host.screen
        ratio = screen.devicePixelRatio()
        avail = screen.availableGeometry()
        cx, top, bottom = round(middle.x()/ratio), round(p.top/ratio), round(p.bottom/ratio)
        if self.mode in ('menu', 'options', 'voices'):
            x, y = self.host.menu_point.x(), self.host.menu_point.y()
        elif self.mode in ('music', 'history'):
            x, y = cx - self.width() - 30, bottom-self.height()
        else:
            x = cx-self.width()//2
            y = top-self.height()-3 if self.caption else bottom+2
        self.move(max(avail.left(), min(x, avail.right()-self.width()+1)),
                  max(avail.top(), min(y, avail.bottom()-self.height()+1)))

    def update_data(self):
        if self.mode and self.ready:
            self.page().runJavaScript('window.panelUpdate('+json.dumps(self.host.data)+')')
            self.place()

    def hide_panel(self):
        self.mode = ''
        self.hide()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if self.mode in ('menu', 'voices', 'options'):
            QTimer.singleShot(200, self.dismiss_inactive)

    def dismiss_inactive(self):
        if self.mode in ('menu', 'voices', 'options') and not self.isActiveWindow():
            emit('dismiss')


class Host:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.screen = QGuiApplication.primaryScreen()
        self.menu_point = QCursor.pos()
        self.data = {}
        self.main = Panel(self)
        self.caption = Panel(self, True)

    def pet_rect(self):
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(wintypes.HWND(self.hwnd), ctypes.byref(rect))
        return rect

    def command(self, msg):
        op = msg.get('op')
        if op == 'quit':
            QApplication.quit()
        elif op == 'hide':
            self.main.hide_panel()
        elif op == 'show':
            if msg['mode'] in ('menu', 'options', 'voices'):
                self.menu_point = QCursor.pos()
                self.screen = QGuiApplication.screenAt(self.menu_point) or self.screen
            self.main.present(msg['mode'])
        elif op == 'draft':
            self.main.page().runJavaScript("document.querySelector('.dock input').value += " + json.dumps(msg['text']))
        elif op == 'data':
            self.data = msg['data']
            self.main.update_data()
            if self.data.get('caption'):
                if self.caption.mode:
                    self.caption.update_data()
                else:
                    self.caption.present('caption')
            else:
                self.caption.hide_panel()


def main():
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    host = Host(int(sys.argv[1]))
    commands = Commands()
    commands.received.connect(host.command, Qt.QueuedConnection)

    def listen():
        for line in sys.stdin:
            commands.received.emit(json.loads(line))
        commands.received.emit({'op':'quit'})
    threading.Thread(target=listen, daemon=True).start()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
