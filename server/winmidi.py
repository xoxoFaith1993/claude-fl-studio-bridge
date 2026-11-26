"""Minimal Windows MIDI (winmm) access through ctypes. No extra packages needed."""

import ctypes
import ctypes.wintypes as wt
import threading
import time
from collections import deque

winmm = ctypes.WinDLL('winmm')

MHDR_DONE = 0x1
CALLBACK_NULL = 0x0


class MIDIHDR(ctypes.Structure):
    _fields_ = [
        ('lpData', ctypes.c_void_p),
        ('dwBufferLength', wt.DWORD),
        ('dwBytesRecorded', wt.DWORD),
        ('dwUser', ctypes.c_size_t),
        ('dwFlags', wt.DWORD),
        ('lpNext', ctypes.c_void_p),
        ('reserved', ctypes.c_size_t),
        ('dwOffset', wt.DWORD),
        ('dwReserved', ctypes.c_size_t * 8),
    ]


class MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ('wMid', wt.WORD), ('wPid', wt.WORD), ('vDriverVersion', wt.UINT),
        ('szPname', wt.WCHAR * 32), ('wTechnology', wt.WORD), ('wVoices', wt.WORD),
        ('wNotes', wt.WORD), ('wChannelMask', wt.WORD), ('dwSupport', wt.DWORD),
    ]


class MIDIINCAPSW(ctypes.Structure):
    _fields_ = [
        ('wMid', wt.WORD), ('wPid', wt.WORD), ('vDriverVersion', wt.UINT),
        ('szPname', wt.WCHAR * 32), ('dwSupport', wt.DWORD),
    ]


HANDLE = ctypes.c_void_p
PHDR = ctypes.POINTER(MIDIHDR)

winmm.midiOutGetNumDevs.restype = wt.UINT
winmm.midiInGetNumDevs.restype = wt.UINT
winmm.midiOutGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(MIDIOUTCAPSW), wt.UINT]
winmm.midiInGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(MIDIINCAPSW), wt.UINT]
winmm.midiOutOpen.argtypes = [ctypes.POINTER(HANDLE), wt.UINT, ctypes.c_size_t, ctypes.c_size_t, wt.DWORD]
winmm.midiInOpen.argtypes = [ctypes.POINTER(HANDLE), wt.UINT, ctypes.c_size_t, ctypes.c_size_t, wt.DWORD]
for _name in ('midiOutPrepareHeader', 'midiOutUnprepareHeader', 'midiOutLongMsg',
              'midiInPrepareHeader', 'midiInUnprepareHeader', 'midiInAddBuffer'):
    getattr(winmm, _name).argtypes = [HANDLE, PHDR, wt.UINT]
winmm.midiOutShortMsg.argtypes = [HANDLE, wt.DWORD]
for _name in ('midiOutClose', 'midiOutReset', 'midiInClose', 'midiInStart', 'midiInStop', 'midiInReset'):
    getattr(winmm, _name).argtypes = [HANDLE]


class MidiError(Exception):
    pass


def _check(rc, what):
    if rc != 0:
        raise MidiError('%s failed (winmm error %d)' % (what, rc))


def output_names():
    names = []
    for i in range(winmm.midiOutGetNumDevs()):
        caps = MIDIOUTCAPSW()
        if winmm.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


def input_names():
    names = []
    for i in range(winmm.midiInGetNumDevs()):
        caps = MIDIINCAPSW()
        if winmm.midiInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


def _find(names, wanted):
    wanted = wanted.lower()
    for i, n in enumerate(names):
        if n.lower() == wanted:
            return i
    for i, n in enumerate(names):
        if wanted in n.lower():
            return i
    return None


class MidiOut:
    def __init__(self, name):
        idx = _find(output_names(), name)
        if idx is None:
            raise MidiError('MIDI output "%s" not found' % name)
        self.handle = HANDLE()
        _check(winmm.midiOutOpen(ctypes.byref(self.handle), idx, 0, 0, CALLBACK_NULL), 'midiOutOpen')
        self.lock = threading.Lock()

    def send_sysex(self, data):
        with self.lock:
            buf = ctypes.create_string_buffer(bytes(data), len(data))
            hdr = MIDIHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = len(data)
            hdr.dwBytesRecorded = len(data)
            _check(winmm.midiOutPrepareHeader(self.handle, ctypes.byref(hdr), ctypes.sizeof(hdr)), 'prepare')
            try:
                _check(winmm.midiOutLongMsg(self.handle, ctypes.byref(hdr), ctypes.sizeof(hdr)), 'midiOutLongMsg')
                deadline = time.time() + 5
                while not (hdr.dwFlags & MHDR_DONE) and time.time() < deadline:
                    time.sleep(0.001)
            finally:
                winmm.midiOutUnprepareHeader(self.handle, ctypes.byref(hdr), ctypes.sizeof(hdr))

    def send_short(self, status, d1=0, d2=0):
        with self.lock:
            _check(winmm.midiOutShortMsg(self.handle, status | (d1 << 8) | (d2 << 16)), 'midiOutShortMsg')

    def close(self):
        winmm.midiOutReset(self.handle)
        winmm.midiOutClose(self.handle)


class MidiIn:
    """Collects incoming SysEx messages and passes each complete one to on_sysex(bytes)."""

    def __init__(self, name, on_sysex, buffers=32, size=4096):
        idx = _find(input_names(), name)
        if idx is None:
            raise MidiError('MIDI input "%s" not found' % name)
        self.on_sysex = on_sysex
        self.handle = HANDLE()
        _check(winmm.midiInOpen(ctypes.byref(self.handle), idx, 0, 0, CALLBACK_NULL), 'midiInOpen')
        self.queue = deque()
        self.keep = []
        for _ in range(buffers):
            buf = ctypes.create_string_buffer(size)
            hdr = MIDIHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = size
            self.keep.append((buf, hdr))
            self._add(hdr)
        self.partial = b''
        self.running = True
        _check(winmm.midiInStart(self.handle), 'midiInStart')
        self.thread = threading.Thread(target=self._poll, daemon=True)
        self.thread.start()

    def _add(self, hdr):
        hdr.dwFlags = 0
        hdr.dwBytesRecorded = 0
        _check(winmm.midiInPrepareHeader(self.handle, ctypes.byref(hdr), ctypes.sizeof(hdr)), 'prepare')
        _check(winmm.midiInAddBuffer(self.handle, ctypes.byref(hdr), ctypes.sizeof(hdr)), 'midiInAddBuffer')
        self.queue.append(hdr)

    def _poll(self):
        while self.running:
            if not self.queue or not (self.queue[0].dwFlags & MHDR_DONE):
                time.sleep(0.002)
                continue
            hdr = self.queue.popleft()
            data = ctypes.string_at(hdr.lpData, hdr.dwBytesRecorded)
            winmm.midiInUnprepareHeader(self.handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if self.running:
                self._add(hdr)
            self._feed(data)

    def _feed(self, data):
        stream = self.partial + data
        while True:
            start = stream.find(b'\xf0')
            if start < 0:
                stream = b''
                break
            end = stream.find(b'\xf7', start)
            if end < 0:
                stream = stream[start:]
                break
            msg = stream[start:end + 1]
            stream = stream[end + 1:]
            try:
                self.on_sysex(msg)
            except Exception:
                pass
        self.partial = stream

    def close(self):
        self.running = False
        winmm.midiInStop(self.handle)
        winmm.midiInReset(self.handle)
        self.thread.join(timeout=1)
        winmm.midiInClose(self.handle)
