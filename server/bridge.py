"""Talks to the Claude Bridge script running inside FL Studio."""

import ast
import os
import threading

import config
import winmidi

TO_FL_PORT = config.get('port_to_fl')
FROM_FL_PORT = config.get('port_from_fl')

MAGIC = 0x7D
KIND_CMD = 0x01
KIND_REPLY = 0x02
CHUNK = 200


class BridgeError(Exception):
    pass


SETUP_HELP = (
    'Setup needed (docs/SETUP.md):\n'
    '1. Create two virtual MIDI ports named "%s" and "%s" (loopMIDI, or Windows MIDI Services loopback '
    'endpoints on Windows 11).\n'
    '2. In FL Studio: Options > MIDI settings. Input "%s": enable it, controller type '
    '"Claude Bridge", pick a port number. Output "%s": set the same port number.\n'
) % (TO_FL_PORT, FROM_FL_PORT, TO_FL_PORT, FROM_FL_PORT)


class Bridge:
    def __init__(self):
        self.out = None
        self.inp = None
        self.lock = threading.Lock()
        self.next_id = 1
        self.waiting = {}  # id -> [event, {chunk: bytes}, count]

    def connect(self):
        if self.out and self.inp:
            return
        outs, ins = winmidi.output_names(), winmidi.input_names()
        try:
            self.out = self.out or winmidi.MidiOut(TO_FL_PORT)
            self.inp = self.inp or winmidi.MidiIn(FROM_FL_PORT, self._on_sysex)
        except winmidi.MidiError as e:
            raise BridgeError('%s\nMIDI outputs: %s\nMIDI inputs: %s\n\n%s' % (e, outs, ins, SETUP_HELP))
        # The first message after opening the ports is sometimes lost; warm up with a harmless ping
        # (safe to repeat) before any real command is sent.
        for _ in range(6):
            try:
                self.run('1', timeout=2.0)
                return
            except BridgeError:
                continue

    def _on_sysex(self, data):
        if len(data) < 10 or data[1] != MAGIC or data[2] != KIND_REPLY:
            return
        msg_id = (data[3] << 7) | data[4]
        idx = (data[5] << 7) | data[6]
        count = (data[7] << 7) | data[8]
        slot = self.waiting.get(msg_id)
        if slot is None:
            return
        slot[1][idx] = data[9:-1]
        slot[2] = count
        if len(slot[1]) >= count:
            slot[0].set()

    def run(self, code, timeout=10.0):
        """Run Python code inside FL. Returns the value of the expression, or `result` for statements."""
        self.connect()
        with self.lock:
            msg_id = self.next_id
            self.next_id = self.next_id % 15000 + 1   # ids 15001+ are free for other tools sharing the ports
        event = threading.Event()
        self.waiting[msg_id] = [event, {}, 0]
        try:
            data = ascii(code).encode('ascii')
            parts = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b'']
            count = len(parts)
            for idx, part in enumerate(parts):
                frame = bytes([0xF0, MAGIC, KIND_CMD, (msg_id >> 7) & 0x7F, msg_id & 0x7F,
                               (idx >> 7) & 0x7F, idx & 0x7F, (count >> 7) & 0x7F, count & 0x7F]) + part + b'\xf7'
                self.out.send_sysex(frame)
            if not event.wait(timeout):
                raise BridgeError(
                    'No reply from FL Studio within %gs. Is FL open, and is "Claude Bridge" set as the controller '
                    'type for the "Claude to FL" input with a matching output port number?\n\n%s' % (timeout, SETUP_HELP))
            slot = self.waiting[msg_id]
            text = b''.join(slot[1][i] for i in range(slot[2])).decode('ascii')
        finally:
            self.waiting.pop(msg_id, None)
        reply = ast.literal_eval(text)
        if not reply.get('ok'):
            raise BridgeError('FL Studio error: %s%s' % (
                reply.get('error'), ('\nOutput:\n' + reply['output']) if reply.get('output') else ''))
        return reply.get('value'), reply.get('output', '')

    def send_note(self, channel, note, velocity, on=True):
        """Send a live MIDI note into FL (plays on the selected channel, or is recorded when recording)."""
        self.connect()
        status = (0x90 if on else 0x80) | (channel & 0x0F)
        self.out.send_short(status, note & 0x7F, velocity & 0x7F)
