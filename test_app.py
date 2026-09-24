"""Shared-room connection and settings checks without an audio device."""

import hashlib
import queue
import socket
import struct
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
import audio_devices
import relay
import settings
import numpy as np


class SettingsTests(unittest.TestCase):
    def test_address_defaults_and_persists(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            data = settings.load_settings(path)
            self.assertEqual(data["relay_host"], "2.29.39.53")
            self.assertIsNone(data["input_device"])
            self.assertIsNone(data["output_device"])
            data["relay_host"] = "relay.example.org"
            data["input_device"] = {"name": "USB Mic", "hostapi": "Core Audio", "occurrence": 0}
            data["output_device"] = {"name": "Headphones", "hostapi": "Core Audio", "occurrence": 0}
            settings.save_settings(data, path)
            self.assertEqual(settings.load_settings(path), data)

    def test_old_settings_keep_relay_address(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings.save_settings({"host_code": "EF" * 16, "friends": [], "relay_host": "relay.example.org"}, path)
            self.assertEqual(settings.load_settings(path), {"relay_host": "relay.example.org", "input_device": None, "output_device": None})

    def test_invalid_saved_device_is_reported(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings.save_settings({"relay_host": "2.29.39.53", "input_device": 123}, path)
            with self.assertRaisesRegex(ValueError, "audio device"):
                settings.load_settings(path)

    def test_invalid_settings_remain_intact(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                settings.load_settings(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_relay_address_validation(self):
        for address in ("2.29.39.53", "relay.example.org", "localhost", "2001:db8::1"):
            self.assertTrue(settings.valid_relay_host(address), address)
        for address in ("", " https://relay.example.org", "relay.example.org:38452", "bad host", "relay..org"):
            self.assertFalse(settings.valid_relay_host(address), address)


class AudioDeviceTests(unittest.TestCase):
    def test_device_choices_resolve_after_indices_change(self):
        mic = {"name": "USB Mic", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0}
        speakers = {"name": "Headphones", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2}
        with patch.object(audio_devices.sd, "query_hostapis", return_value=[{"name": "Core Audio"}]), \
             patch.object(audio_devices.sd, "query_devices", side_effect=[[mic, speakers], [speakers, mic]]):
            identity = audio_devices.choices("input")[1][1]
            self.assertEqual(audio_devices.resolve("input", identity), 1)
        self.assertEqual(identity, {"name": "USB Mic", "hostapi": "Core Audio", "occurrence": 0})

    def test_missing_selected_device_is_not_replaced_silently(self):
        with patch.object(audio_devices.sd, "query_hostapis", return_value=[]), \
             patch.object(audio_devices.sd, "query_devices", return_value=[]):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                audio_devices.resolve("input", {"name": "Gone", "hostapi": "Core Audio", "occurrence": 0})
        self.assertIsNone(audio_devices.resolve("output", None))

    def test_input_meter_peaks_include_negative_full_scale(self):
        self.assertEqual(app.input_level(np.array([[-32768], [0]], dtype=np.int16)), 1.0)
        self.assertAlmostEqual(app.input_level(np.array([[16384]], dtype=np.int16)), 0.5)
        self.assertEqual(app.input_level(np.array([], dtype=np.int16)), 0.0)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.server = relay.Relay()
        self.ready = queue.Queue()
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self.server.serve, args=("127.0.0.1", 0, self.ready, self.stop), daemon=True
        )
        self.thread.start()
        self.port = self.ready.get(timeout=2)

    def tearDown(self):
        self.stop.set()
        self.thread.join(2)

    def connect(self):
        client = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        client.sendall(b"C")
        return client

    def packet(self, client):
        return app.receive_packet(client, threading.Event())

    def next_count(self, client, expected):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            kind, value = self.packet(client)
            if kind == b"U" and value == expected:
                return
        self.fail(f"No participant update for {expected} callers")

    def test_mixer_excludes_own_voice_and_clips(self):
        a = struct.pack("<960h", *([30000] * 960))
        b = struct.pack("<960h", *([20000] * 960))
        c = struct.pack("<960h", *([20000] * 960))
        outputs = relay.mix([a, b, c])
        self.assertEqual(struct.unpack("<h", outputs[0][:2])[0], 32767)
        self.assertEqual(struct.unpack("<h", outputs[1][:2])[0], 32767)
        self.assertEqual(relay.mix([a])[0], relay.SILENCE)

    def test_four_people_join_and_room_stays_up_when_one_leaves(self):
        clients = []
        try:
            first = self.connect()
            clients.append(first)
            self.assertEqual(first.recv(1), b"W")
            for index in range(3):
                client = self.connect()
                clients.append(client)
                self.assertEqual(client.recv(1), b"P")
                if index == 0:
                    self.assertEqual(first.recv(1), b"P")
            self.assertEqual(len(self.server.peers), 4)
            self.next_count(first, 4)
            clients[2].close()
            deadline = time.monotonic() + 2
            while len(self.server.peers) != 3 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(self.server.peers), 3)
            self.next_count(first, 3)
            fifth = self.connect()
            clients.append(fifth)
            self.assertEqual(fifth.recv(1), b"P")
            self.assertEqual(len(self.server.peers), 4)
            self.next_count(first, 4)
        finally:
            for client in clients:
                client.close()

    def test_three_person_audio_mixed_over_sockets(self):
        clients = []
        try:
            first = self.connect()
            clients.append(first)
            self.assertEqual(first.recv(1), b"W")
            for _ in range(2):
                client = self.connect()
                clients.append(client)
                self.assertEqual(client.recv(1), b"P")
            self.assertEqual(first.recv(1), b"P")
            for client in clients:
                client.settimeout(2)
            a = struct.pack("<960h", *([100] * 960))
            b = struct.pack("<960h", *([200] * 960))
            c = struct.pack("<960h", *([300] * 960))
            # Feed frames long enough to align with the relay's mixing ticks.
            for _ in range(12):
                for client, frame in zip(clients, (a, b, c)):
                    client.sendall(frame)
                time.sleep(0.02)
            for client, expected in zip(clients, (500, 400, 300)):
                observed = set()
                for _ in range(18):
                    kind, value = self.packet(client)
                    if kind == b"U":
                        continue
                    sample = struct.unpack("<h", value[:2])[0]
                    observed.add(sample)
                    if expected in observed:
                        break
                self.assertIn(expected, observed)
        finally:
            for client in clients:
                client.close()

    def test_waiting_disconnect_frees_room(self):
        first = self.connect()
        self.assertEqual(first.recv(1), b"W")
        first.close()
        deadline = time.monotonic() + 2
        while self.server.peers and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.server.peers, [])
        with self.connect() as replacement:
            self.assertEqual(replacement.recv(1), b"W")

    def test_client_connect_flow(self):
        results = []
        def client():
            instance = app.VoiceApp.__new__(app.VoiceApp)
            instance.lock = threading.Lock()
            instance.generation = 1
            instance.stop_event = threading.Event()
            instance.connection = None
            instance.post = lambda generation, kind, message: results.append((kind, message))

            def call(generation, stop, connection):
                frame = struct.pack("<960h", *([100] * 960))
                observed = set()
                for _ in range(18):
                    connection.sendall(frame)
                    kind, response = self.packet(connection)
                    if kind == b"A":
                        observed.add(struct.unpack("<h", response[:2])[0])
                self.assertIn(100, observed)
                stop.set()
                return None

            instance.call = call
            return instance

        first = client()
        second = client()

        class TestSocket:
            def __init__(self, raw):
                self.raw = raw

            def getpeercert(self, binary_form):
                return b"test cert"

            def __getattr__(self, name):
                return getattr(self.raw, name)

        with patch.object(app, "RELAY_PORT", self.port), \
             patch.object(app, "RELAY_CERT_SHA256", hashlib.sha256(b"test cert").hexdigest()), \
             patch.object(app.ssl, "SSLContext") as context:
            context.return_value.wrap_socket.side_effect = lambda raw, server_hostname: TestSocket(raw)
            first_thread = threading.Thread(target=first.run_relay, args=(1, first.stop_event, "127.0.0.1"))
            first_thread.start()
            deadline = time.monotonic() + 2
            while not self.server.peers and time.monotonic() < deadline:
                time.sleep(0.01)
            second.run_relay(1, second.stop_event, "127.0.0.1")
            first_thread.join(2)
        self.assertFalse(first_thread.is_alive())
        self.assertIn(("connected", "Connected to relay. Waiting for others…"), results)
        self.assertIn(("count", 1), results)
        self.assertFalse(any("Connection failed" in message for _, message in results if isinstance(message, str)), results)


if __name__ == "__main__":
    unittest.main()
