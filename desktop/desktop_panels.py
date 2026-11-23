"""Connect the desktop HTML views to the existing pet, voice, and player."""
import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from .desktop_overlay import CaptionState
from .voice import VOICES


class DesktopPanels:
    def __init__(self, desktop, python):
        self.desktop = desktop
        self.room = desktop.room
        self.app = desktop.app
        self.events = queue.Queue()
        self.mode = ''
        self.conversation = False
        self.caption = CaptionState()
        self.scale = 1.
        self.results = []
        self.notice = ''
        self.last = None
        self.next_update = 0
        self.closed = False
        self.search_id = 0
        self.pending_voice = False
        desktop.root.update_idletasks()
        hwnd = desktop.root.wm_frame()
        with (self.app.runtime / 'desktop-panels.log').open('a', encoding='utf-8') as log:
            self.proc = subprocess.Popen([str(python), str(Path(__file__).with_name('panel_host.py')), str(int(hwnd, 0))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8',
                creationflags=subprocess.CREATE_NO_WINDOW, env={**os.environ, 'PYTHONIOENCODING':'utf-8'})
        atexit.register(self.close)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.events.put(json.loads(line))
            except ValueError:
                continue

    def write(self, op, **fields):
        if self.proc.poll() is None and not self.closed:
            try:
                self.proc.stdin.write(json.dumps({'op':op, **fields})+'\n')
                self.proc.stdin.flush()
            except OSError:
                pass

    def close(self):
        if not self.closed:
            self.write('quit')
            self.closed = True
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def show(self, mode):
        self.mode = mode
        if mode == 'dock':
            self.conversation = True
        if self.room.window and self.room.window.winfo_viewable():
            self.room.window.withdraw()
        self.last = None
        self.write('show', mode=mode)

    def hide(self):
        self.mode = ''
        self.write('hide')

    def voice(self):
        self.show('dock')
        if self.room.voice.dictate.is_set():
            self.room.voice.finish.set()
        elif self.room.voice.start():
            self.pending_voice = True
            self.notice = '请说，识别的文字可以修改。'
        else:
            from .desktop_voice import open_system_dictation
            self.desktop.root.after(300, open_system_dictation)

    def dispatch(self, event):
        action, value = event.get('action'), event.get('value')
        if action in ('menu','options','voices','music','history'):
            self.show(action)
        elif action == 'chat':
            self.show('dock')
        elif action in ('send','dismiss','hideMusic'):
            if action != 'send' or self.mode != 'history':
                self.hide()
            if action=='send' and isinstance(value,str) and 0<len(value.strip())<=600:
                self.room.run_job(lambda:self.app.interact('chat', text=value.strip()))
        elif action == 'voice':
            self.voice()
        elif action == 'voiceTone':
            self.show('voices')
        elif action == 'selectVoice' and value in VOICES:
            self.desktop.voice_value.set(value)
            self.desktop.select_voice()
            self.hide()
        elif action == 'lyrics':
            self.room.lyrics_visible.set(not self.room.lyrics_visible.get())
        elif action == 'wake':
            self.room.wake_enabled.set(not self.room.wake_enabled.get())
            if self.room.wake_enabled.get():
                if not self.room.voice.start(wake=True):
                    self.room.wake_enabled.set(False)
                    self.notice='请先安装离线语音识别模型。'
            else:
                self.room.voice.wake.clear()
        elif action in ('pause','prev','next','stop'):
            if action=='pause':
                self.room.toggle_pause()
            elif action=='stop':
                self.room.run_job(self.app.music.stop)
            else:
                self.room.step(1 if action=='next' else -1)
        elif action=='harbor':
            self.hide()
            self.app.open_room()
        elif action=='hidePet':
            self.hide()
            self.conversation=False
            self.desktop.hide()
        elif action=='quit':
            self.app.stop()
        elif action=='size' and value in (.5,.75,1,1.5,2):
            self.app.pet.act('scale', value=value)
        elif action=='captionSize' and value in (-.1,.1):
            self.scale=max(.8,min(1.55,self.scale+value))
        elif action=='search' and isinstance(value,str) and value.strip():
            self.search_id+=1
            request=self.search_id
            self.notice='正在搜索…'
            self.results=[]
            def search():
                try:
                    songs=self.app.music.search(value.strip()[:120])
                    self.events.put({'action':'found','value':(request,songs,'')})
                except (RuntimeError,OSError,ValueError) as exc:
                    self.events.put({'action':'found','value':(request,[],str(exc))})
            threading.Thread(target=search,daemon=True).start()
        elif action=='found' and value[0]==self.search_id:
            self.results=value[1]
            self.notice=value[2] or ('' if self.results else '没有找到歌曲。')
        elif action in ('select','remove'):
            method=self.app.music.select if action=='select' else self.app.music.remove
            self.room.run_job(lambda:method(value))
        elif action in ('playResult','enqueue'):
            song=next((s for s in self.results if s['id']==value),None)
            if song:
                method=self.app.music.play if action=='playResult' else self.app.music.enqueue
                self.room.run_job(lambda:method(song))
        self.next_update=0

    def update(self,state,music):
        while not self.events.empty():
            self.dispatch(self.events.get_nowait())
        now=time.monotonic()
        if now<self.next_update:
            return
        self.next_update=now+.24
        visible=self.app.pet.pet_visible
        caption=''
        if visible and (self.conversation or self.room.lyrics_visible.get() and (music['playing'] or music['paused'])):
            caption=self.caption.text(state,music,self.conversation,self.mode=='dock')
        data={'caption':caption,'captionScale':self.scale,'music':music,'results':self.results,'notice':self.notice,
              'lyrics':self.room.lyrics_visible.get(),'wake':self.room.wake_enabled.get(),'voice':state['voice_id'],
              'voices':{k:v[0] for k,v in VOICES.items()},'position':music.get('position',0)}
        if self.mode == 'history':
            data['timeline'] = state['timeline']
        anchor=self.desktop.root.winfo_x(),self.desktop.root.winfo_y(),self.desktop.root.winfo_width(),self.desktop.root.winfo_height()
        if not visible and self.mode:
            self.hide()
        if (data,anchor)!=self.last:
            self.write('data',data=data)
            self.last=(data,anchor)
