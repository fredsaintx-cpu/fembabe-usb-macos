"""
FEMBABE USB Streaming Helper
============================
Bridges OBS  ->  iPhone (over USB)  so you can push your OBS scene into a
mobile-only LIVE (TikTok / Instagram / etc.) through the phone.

How it works:
  * Talks to Apple's usbmuxd (/var/run/usbmuxd on macOS) to detect the iPhone over USB.
  * Opens a local RTMP endpoint  rtmp://127.0.0.1:<port>/live  (key: stream)
    that OBS publishes to, and relays those bytes over the USB tunnel to the
    companion app running on the phone (which must be LIVE first).

It is a transparent TCP <-> USB relay: the phone runs the real RTMP server,
we just carry the bytes across the cable.

Env vars:
  FEMBABE_DEMO=1        fake a connected iPhone (for previewing the UI)
  FEMBABE_TOP=1         keep the window always-on-top
  FEMBABE_PHONE_PORT=N  RTMP port the companion app listens on inside the phone
                        (default 1935 - must match the phone app)
"""
import os
import sys
import socket
import struct
import threading
import queue
import plistlib
import platform
import tkinter as tk
from tkinter import font as tkfont

_IS_MAC = platform.system() == 'Darwin'

# DPI scaling — on macOS tkinter handles Retina natively; use 1.0
_SYS_DPI = 96
_SCALE = 1.0

def px(n):
    return round(n * _SCALE)

# usbmuxd: Unix domain socket on macOS, TCP on Windows
if _IS_MAC:
    USBMUXD_ADDR = '/var/run/usbmuxd'
else:
    USBMUXD_ADDR = ('127.0.0.1', 27015)

LOCAL_PORTS = list((1935, 1936, 1937, 1938))
PHONE_PORT  = int(os.environ.get('FEMBABE_PHONE_PORT', '1935'))
STREAM_APP  = 'live'
STREAM_KEY  = 'stream'
DEMO        = os.environ.get('FEMBABE_DEMO') == '1'
ALWAYS_TOP  = os.environ.get('FEMBABE_TOP') == '1'
CLIENT_LABEL = 'fembabe-usb-1.0'


def _resource_path(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


ICON_PATH = _resource_path('fembabe.png')

C_TOP      = '#160b2e'
C_BOTTOM   = '#2c1656'
C_BADGE    = '#38205f'
C_BADGE_BD = '#6d45c9'
C_CARD     = '#241145'
C_CARD_BD  = '#5b39a6'
C_ACCENT   = '#8b5cf6'
C_ACCENT2  = '#a78bfa'
C_WHITE    = '#ffffff'
C_SUBTLE   = '#cbb8f2'
C_FAINT    = '#9a82cf'
C_OK       = '#34d399'
C_WAIT     = '#f5b544'
C_ERR      = '#f2647e'

INIT_W, INIT_H = 860, 340
MIN_W,  MIN_H  = 700, 320
MARGIN = px(28)

STR = {
    'en': {
        'title':    'FEMBABE',
        'subtitle': 'USB Streaming Helper',
        'url_label': 'Stream URL (copy into OBS):',
        'copy':     'Copy',
        'copied':   'Stream URL copied to clipboard  ✓',
        'vcam_tip': 'FEMBABE iOS VCam client: the RTMP box will appear blank — toggle LIVE ON in the app first, then start streaming in OBS.',
        'hint':     'OBS:  Settings → Stream → Service ‘Custom’    •    Server = the URL above    •    Stream Key = ' + STREAM_KEY + '    •    start LIVE on your phone first.',
        's_conn':   'Connected',
        's_stream': 'Streaming to iPhone',
        's_wait':   'Waiting for iPhone…',
        's_nodrv':  'Connect and trust your iPhone, then reopen the app',
        's_noport': 'No local port free (1935-1938)',
        'lang_btn': '中文',
    },
    'zh': {
        'title':    'FEMBABE',
        'subtitle': 'USB 推流助手',
        'url_label': '推流地址（复制到 OBS）：',
        'copy':     '复制',
        'copied':   '已复制推流地址  ✓',
        'vcam_tip': 'FEMBABE iOS 虚拟摄像头：RTMP 框显示空白属正常 — 先在 App 内开启 LIVE，再在 OBS 开始推流。',
        'hint':     'OBS：设置 → 推流 → 服务“自定义”    •    服务器填上面地址    •    推流码填 ' + STREAM_KEY + '    •    手机端先开 LIVE。',
        's_conn':   '已连接',
        's_stream': '正在推流到 iPhone',
        's_wait':   '等待 iPhone…',
        's_nodrv':  '请连接并信任 iPhone，然后重新打开应用',
        's_noport': '本地端口不可用（1935-1938）',
        'lang_btn': 'EN',
    },
}


class Usbmux:
    """Minimal usbmuxd (plist) client - Listen for devices + Connect a port."""
    TYPE_PLIST = 8

    def __init__(self):
        self._tag = 0

    def _pack(self, payload):
        self._tag += 1
        body = plistlib.dumps(payload)
        hdr  = struct.pack('<IIII', 16 + len(body), 1, self.TYPE_PLIST, self._tag)
        return hdr + body

    @staticmethod
    def _recv_exact(sock, n):
        buf = b''
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError('usbmuxd closed the connection')
            buf += chunk
        return buf

    def _recv_plist(self, sock):
        length, _ver, _typ, _tag = struct.unpack('<IIII', self._recv_exact(sock, 16))
        return plistlib.loads(self._recv_exact(sock, length - 16))

    @staticmethod
    def _open():
        if _IS_MAC:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(USBMUXD_ADDR)
            return s
        return socket.create_connection(USBMUXD_ADDR, timeout=5)

    def listen(self, on_change, stop_evt):
        """Blocking loop. Calls on_change(sorted_usb_device_ids, err)."""
        devices = {}
        try:
            sock = self._open()
        except OSError as exc:
            on_change(None, f'usbmuxd unreachable: {exc}')
            return
        try:
            sock.sendall(self._pack({
                'MessageType':        'Listen',
                'ClientVersionString': CLIENT_LABEL,
                'ProgName':           'FEMBABE USB',
                'kLibUSBMuxVersion':   3,
            }))
            sock.settimeout(1.0)
            while not stop_evt.is_set():
                try:
                    length, _v, _t, _g = struct.unpack('<IIII', self._recv_exact(sock, 16))
                    sock.settimeout(None)
                    msg = plistlib.loads(self._recv_exact(sock, length - 16))
                    sock.settimeout(1.0)
                    mt = msg.get('MessageType')
                    if mt == 'Attached':
                        props = msg.get('Properties', {})
                        if props.get('ConnectionType') == 'USB':
                            devices[msg['DeviceID']] = 'USB'
                            on_change(sorted(devices), None)
                    elif mt == 'Detached':
                        if devices.pop(msg.get('DeviceID'), None) is not None:
                            on_change(sorted(devices), None)
                except socket.timeout:
                    continue
        except OSError as exc:
            on_change(None, f'usbmuxd error: {exc}')
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def connect(self, device_id, port):
        sock = self._open()
        sock.sendall(self._pack({
            'MessageType':        'Connect',
            'DeviceID':           int(device_id),
            'PortNumber':         socket.htons(port),
            'ClientVersionString': CLIENT_LABEL,
            'ProgName':           'FEMBABE USB',
        }))
        resp = self._recv_plist(sock)
        if resp.get('Number', -1) != 0:
            sock.close()
            raise ConnectionError(f'usbmux Connect failed (Number={resp.get("Number")})')
        sock.settimeout(None)
        return sock


class Relay:
    def __init__(self, mux, get_device, on_status):
        self.mux        = mux
        self.get_device = get_device
        self.on_status  = on_status
        self.port       = None
        self._srv       = None
        self._active    = 0
        self._lock      = threading.Lock()
        self._stop      = threading.Event()

    def start(self):
        for p in LOCAL_PORTS:
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(('127.0.0.1', p))
                srv.listen(4)
                self._srv = srv
                self.port = p
                break
            except OSError:
                continue
        if self._srv is None:
            return None
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self.port

    def _accept_loop(self):
        self._srv.settimeout(1.0)
        while not self._stop.is_set():
            try:
                client, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client):
        device = self.get_device()
        if device is None:
            client.close()
            return
        try:
            usb = self.mux.connect(device, PHONE_PORT)
        except OSError:
            client.close()
            return
        self._bump(1)
        done = threading.Event()

        def pump(src, dst):
            try:
                while not done.is_set():
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                done.set()

        t1 = threading.Thread(target=pump, args=(client, usb), daemon=True)
        t2 = threading.Thread(target=pump, args=(usb, client), daemon=True)
        t1.start()
        t2.start()
        done.wait()
        for s in (client, usb):
            try:
                s.close()
            except OSError:
                pass
        self._bump(-1)

    def _bump(self, delta):
        with self._lock:
            self._active += delta
            n = self._active
        self.on_status(n)

    def stop(self):
        self._stop.set()
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass


def _rgb(h):
    return (int(h[i:i+2], 16) for i in (1, 3, 5))


def round_rect(c, x1, y1, x2, y2, r=14, **kw):
    pts = [
        x1+r, y1,   x2-r, y1,
        x2,   y1,   x2,   y1+r,
        x2,   y2-r, x2,   y2,
        x2-r, y2,   x1+r, y2,
        x1,   y2,   x1,   y2-r,
        x1,   y1+r, x1,   y1,
        x1+r, y1,
    ]
    c.create_polygon(pts, smooth=True, **kw)


class App:
    def __init__(self, root):
        self.root      = root
        self.lang      = 'en'
        self.events    = queue.Queue()
        self.devices   = []
        self.streaming = 0
        self.driver_ok = True
        self.port      = None
        self.stop_evt  = threading.Event()
        self._last_size = (0, 0)

        root.title('FEMBABE USB Streaming Helper')
        root.geometry(f'{px(INIT_W)}x{px(INIT_H)}+80+80')
        root.minsize(px(MIN_W), px(MIN_H))
        root.resizable(True, True)
        root.configure(bg=C_TOP)
        if ALWAYS_TOP:
            root.attributes('-topmost', True)

        # PyInstaller places bundled resources under sys._MEIPASS.
        try:
            if os.path.exists(ICON_PATH):
                self._icon = tk.PhotoImage(file=ICON_PATH)
                root.iconphoto(True, self._icon)
        except Exception:
            pass

        root.after(500, self._apply_win_icons)

        # Fonts — macOS-native families, fall back gracefully
        if _IS_MAC:
            title_f = 'SF Pro Display'
            body_f  = 'Helvetica Neue'
            semi_f  = 'Helvetica Neue'
            mono_f  = 'Menlo'
        else:
            title_f = 'Segoe UI'
            body_f  = 'Segoe UI'
            semi_f  = 'Segoe UI Semibold'
            mono_f  = 'Consolas'

        self.f_title  = tkfont.Font(family=title_f, size=27, weight='bold')
        self.f_sub    = tkfont.Font(family=body_f,  size=12)
        self.f_label  = tkfont.Font(family=body_f,  size=11)
        self.f_status = tkfont.Font(family=semi_f,  size=13)
        self.f_hint   = tkfont.Font(family=body_f,  size=9)
        self.f_mono   = tkfont.Font(family=mono_f,  size=13)
        self.f_btn    = tkfont.Font(family=semi_f,  size=10)

        self.url_var = tk.StringVar(value='')

        self.c = tk.Canvas(root, width=INIT_W, height=INIT_H,
                           highlightthickness=0, bd=0, bg=C_TOP)
        self.c.pack(fill='both', expand=True)

        self._build_widgets()
        self.c.bind('<Configure>', self._relayout)

        self.mux   = Usbmux()
        self.relay = Relay(self.mux, self._current_device, self._on_relay_status)
        self._start_backend()

        root.protocol('WM_DELETE_WINDOW', self._quit)
        root.after(120, self._drain_events)

    def _apply_win_icons(self):
        # No-op on macOS; Windows-specific icon setup was here originally
        pass

    def _build_widgets(self):
        c = self.c
        t = STR[self.lang]

        self.entry = tk.Entry(
            c, textvariable=self.url_var, font=self.f_mono,
            bg=C_CARD, fg=C_WHITE, insertbackground=C_WHITE,
            relief='flat', bd=0, state='readonly',
            readonlybackground=C_CARD,
        )
        self.win_entry = c.create_window(0, 0, anchor='w', window=self.entry)

        self.copy_btn = tk.Button(
            c, text=t['copy'], font=self.f_btn,
            bg=C_ACCENT, fg=C_WHITE,
            activebackground=C_ACCENT2, activeforeground=C_WHITE,
            relief='flat', bd=0, cursor='hand2',
            command=self._copy,
        )
        self.win_copy = c.create_window(0, 0, anchor='w', window=self.copy_btn)

        self.lang_btn = tk.Button(
            c, text=t['lang_btn'], font=self.f_btn,
            bg=C_BADGE, fg=C_SUBTLE,
            activebackground=C_TOP, activeforeground=C_WHITE,
            relief='flat', bd=0, cursor='hand2',
            command=self._toggle_lang,
        )
        self.win_lang = c.create_window(0, 0, anchor='se', window=self.lang_btn)

    def _relayout(self, event=None):
        c = self.c
        w = event.width  if event else c.winfo_width()
        h = event.height if event else c.winfo_height()
        if (w, h) == self._last_size or w < 10:
            return
        self._last_size = (w, h)
        t = STR[self.lang]

        c.delete('deco')

        # Gradient background
        r1, g1, b1 = _rgb(C_TOP)
        r2, g2, b2 = _rgb(C_BOTTOM)
        for y in range(h):
            k   = y / max(h - 1, 1)
            col = f'#{int(r1 + (r2-r1)*k):02x}{int(g1 + (g2-g1)*k):02x}{int(b1 + (b2-b1)*k):02x}'
            c.create_line(0, y, w, y, fill=col, tags='deco')

        # Badge
        bs = px(80)
        bx = MARGIN
        by = px(18)
        round_rect(c, bx, by, bx+bs, by+bs,
                   r=px(18), fill=C_BADGE, outline=C_BADGE_BD, width=px(2), tags='deco')

        # Logo icon stripes
        _fw  = round(bs * 0.46)
        _fh  = round(bs * 0.63)
        _th  = max(round(bs * 0.095), 2)
        _mth = max(round(bs * 0.075), 2)
        _mw  = round(_fw * 0.7)
        _fl  = bx + (bs - _fw) // 2
        _ft  = by + (bs - _fh) // 2
        _my  = _ft + round(_fh * 0.42)
        c.create_rectangle(_fl, _ft, _fl+_th-1, _ft+_fh-1, fill=C_WHITE, outline='', tags='deco')
        c.create_rectangle(_fl, _ft, _fl+_fw-1, _ft+_th-1, fill=C_WHITE, outline='', tags='deco')
        c.create_rectangle(_fl, _my, _fl+_mw-1, _my+_mth-1, fill=C_WHITE, outline='', tags='deco')

        # Title
        tx = bx + bs + px(22)
        c.create_text(tx, px(40), anchor='w', fill=C_WHITE, font=self.f_title,
                      text=t['title'], tags='deco')
        c.create_text(tx + self.f_title.measure(t['title']) + px(12), px(42),
                      anchor='w', fill=C_ACCENT2, font=self.f_title, text='♥', tags='deco')
        c.create_text(tx + px(2), px(74), anchor='w', fill=C_SUBTLE,
                      font=self.f_sub, text=t['subtitle'], tags='deco')

        # Separator
        c.create_line(MARGIN, px(104), w-MARGIN, px(104), fill='#3d2570', tags='deco')
        c.create_line(MARGIN, px(104), px(150), px(104), fill=C_ACCENT, width=2, tags='deco')

        # Status
        self.i_dot = c.create_oval(
            MARGIN+px(2), px(123), MARGIN+px(16), px(137),
            fill=C_WAIT, outline='', tags='deco')
        self.i_status = c.create_text(
            MARGIN+px(26), px(130), anchor='w', fill=C_WHITE,
            font=self.f_status, text=t['s_wait'], tags='deco')
        self.i_url_top = c.create_text(
            w-MARGIN, px(130), anchor='e', fill=C_FAINT,
            font=self.f_mono, text='', tags='deco')

        # URL card
        self.i_label = c.create_text(
            MARGIN, px(166), anchor='w', fill=C_SUBTLE,
            font=self.f_label, text=t['url_label'], tags='deco')

        card_r = w - px(150)
        cy1, cy2 = px(182), px(224)
        round_rect(c, MARGIN, cy1, card_r, cy2,
                   r=px(12), fill=C_CARD, outline=C_CARD_BD, width=1, tags='deco')
        cymid = (cy1 + cy2) // 2

        c.coords(self.win_entry, MARGIN+px(18), cymid)
        c.itemconfig(self.win_entry, width=card_r - MARGIN - px(34))
        c.coords(self.win_copy, w-px(132), cymid)
        c.itemconfig(self.win_copy, width=px(104), height=cy2-cy1)
        c.coords(self.win_lang, w-MARGIN+px(4), h-px(18))
        c.itemconfig(self.win_lang, width=px(66), height=px(28))

        # Tips
        self.i_vcam_tip = c.create_text(
            MARGIN, px(232), anchor='nw', fill=C_WHITE,
            font=self.f_hint, width=w - 2*MARGIN, text=t['vcam_tip'], tags='deco')
        self.i_hint = c.create_text(
            MARGIN, px(252), anchor='nw', fill=C_WHITE,
            font=self.f_hint, width=w - 2*MARGIN, text=t['hint'], tags='deco')

        self.copy_btn.config(text=t['copy'])
        self.lang_btn.config(text=t['lang_btn'])

        c.tag_lower('deco')
        self._refresh_status()

    def _start_backend(self):
        self.port = self.relay.start()
        if self.port is None:
            self.driver_ok = False
            self.url_var.set('')
        else:
            self.url_var.set(f'rtmp://127.0.0.1:{self.port}/{STREAM_APP}')

        if DEMO:
            self.devices = [1]
            return

        def on_change(dev_list, err):
            self.events.put(('mux', dev_list, err))

        threading.Thread(
            target=self.mux.listen,
            args=(on_change, self.stop_evt),
            daemon=True,
        ).start()

    def _current_device(self):
        return self.devices[0] if self.devices else None

    def _on_relay_status(self, n):
        self.events.put(('stream', n, None))

    def _drain_events(self):
        try:
            while True:
                kind, a, _b = self.events.get_nowait()
                if kind == 'mux':
                    if a is None:
                        self.driver_ok = False
                    else:
                        self.driver_ok = True
                        self.devices   = a
                elif kind == 'stream':
                    self.streaming = a
                self._refresh_status()
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    def _refresh_status(self):
        if not hasattr(self, 'i_dot'):
            return
        t = STR[self.lang]
        if self.port is None:
            dot, txt = C_ERR, t['s_noport']
        elif not self.driver_ok:
            dot, txt = C_ERR, t['s_nodrv']
        elif self.streaming > 0:
            dot, txt = C_OK, t['s_stream']
        elif self.devices:
            dot, txt = C_OK, t['s_conn']
        else:
            dot, txt = C_WAIT, t['s_wait']

        self.c.itemconfig(self.i_dot, fill=dot)
        self.c.itemconfig(self.i_status, text=txt)
        self.c.itemconfig(self.i_url_top,
            text=self.url_var.get() if (self.devices or DEMO) else '')

    def _copy(self):
        url = self.url_var.get()
        if not url:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.c.itemconfig(self.i_hint, text=STR[self.lang]['copied'], fill=C_ACCENT2)
        self.root.after(1600, lambda: self.c.itemconfig(
            self.i_hint, text=STR[self.lang]['hint'], fill=C_WHITE))

    def _toggle_lang(self):
        self.lang = 'zh' if self.lang == 'en' else 'en'
        self._last_size = (0, 0)
        self._relayout()

    def _quit(self):
        self.stop_evt.set()
        self.relay.stop()
        self.root.destroy()


def main():
    while True:
        try:
            root = tk.Tk()
            App(root)
            root.mainloop()
            return
        except Exception:
            pass


if __name__ == '__main__':
    main()
