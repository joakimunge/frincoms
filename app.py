"""Small shared-room voice chat for Windows and macOS."""

import hashlib
import hmac
import queue
import socket
import ssl
import struct
import threading
import tkinter as tk
from tkinter import ttk

import numpy as np
import sounddevice as sd

import audio_devices
from settings import load_settings, save_settings, settings_path, valid_relay_host
from relay_config import RELAY_HOST, RELAY_PORT, RELAY_CERT_SHA256


RATE = 48000
BLOCK = 960  # 20 ms
BYTES_PER_BLOCK = BLOCK * 2  # mono, signed 16-bit PCM


def input_level(samples):
    """Peak mic level, including the full negative range of int16."""
    return min(1.0, float(np.max(np.abs(samples.astype(np.int32)))) / 32768) if samples.size else 0.0


def receive_exact(connection, length, stop):
    data = bytearray()
    while len(data) < length and not stop.is_set():
        try:
            chunk = connection.recv(length - len(data))
        except socket.timeout:
            continue
        if not chunk:
            raise ConnectionError("The other person disconnected")
        data.extend(chunk)
    if stop.is_set():
        raise ConnectionError("Disconnected")
    return bytes(data)


def receive_packet(connection, stop):
    kind = receive_exact(connection, 1, stop)
    if kind == b"U":
        return kind, struct.unpack("!H", receive_exact(connection, 2, stop))[0]
    if kind == b"A":
        return kind, receive_exact(connection, BYTES_PER_BLOCK, stop)
    raise ConnectionError("Unexpected relay message")


def close_socket(connection):
    if connection is not None:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()


class VoiceApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Frincoms")
        self.root.resizable(False, False)
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.generation = 0
        self.stop_event = None
        self.connection = None
        self.muted = False
        self.deafened = False
        self.mic_level = 0.0
        self.meter_stream = None
        self.input_choices = []
        self.output_choices = []
        try:
            self.settings = load_settings()
            if not settings_path().exists():
                save_settings(self.settings)
            settings_error = None
        except (OSError, ValueError) as exc:
            self.settings = None
            settings_error = f"Could not load relay settings: {exc}"

        self.root.configure(background="#111827")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Panel.TFrame", background="#1f2937")
        style.configure("Title.TLabel", background="#1f2937", foreground="#f9fafb", font=("TkDefaultFont", 22, "bold"))
        style.configure("Hint.TLabel", background="#1f2937", foreground="#9ca3af", font=("TkDefaultFont", 10))
        style.configure("State.TLabel", background="#1f2937", foreground="#f9fafb", font=("TkDefaultFont", 13, "bold"))
        style.configure("Count.TLabel", background="#1f2937", foreground="#60a5fa", font=("TkDefaultFont", 13, "bold"))
        style.configure("Primary.TButton", font=("TkDefaultFont", 11, "bold"), padding=(16, 10), background="#2563eb", foreground="#ffffff", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#374151")])
        style.configure("Control.TButton", padding=(10, 8), background="#374151", foreground="#f9fafb", borderwidth=0)
        style.map("Control.TButton", background=[("active", "#4b5563")])
        style.configure("Relay.TEntry", padding=6, fieldbackground="#111827", foreground="#f9fafb")
        style.configure("Device.TCombobox", padding=6, fieldbackground="#111827", foreground="#f9fafb")
        style.configure("Meter.Horizontal.TProgressbar", background="#34d399", troughcolor="#111827", borderwidth=0)

        frame = ttk.Frame(root, padding=24, style="Panel.TFrame")
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="Frincoms", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        ttk.Label(frame, text="One room. Your people. Just talk.", style="Hint.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 18)
        )
        self.connection_state = tk.StringVar(value="Disconnected")
        self.people = tk.StringVar(value="0 people in room")
        self.state_label = ttk.Label(frame, textvariable=self.connection_state, style="State.TLabel")
        self.state_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(frame, textvariable=self.people, style="Count.TLabel").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(0, 20)
        )

        self.connect_button = ttk.Button(frame, text="Connect to Frincoms", command=self.connect, style="Primary.TButton")
        self.connect_button.grid(row=4, column=0, columnspan=2, padx=(0, 8), sticky="ew")
        self.disconnect_button = ttk.Button(frame, text="Disconnect", command=self.disconnect, style="Control.TButton")
        self.disconnect_button.grid(row=4, column=2, sticky="ew")
        self.disconnect_button.state(["disabled"])

        self.mute_button = ttk.Button(frame, text="Mute mic", command=self.toggle_mute, style="Control.TButton")
        self.mute_button.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0), padx=(0, 8))
        self.deafen_button = ttk.Button(frame, text="Deafen", command=self.toggle_deafen, style="Control.TButton")
        self.deafen_button.grid(row=5, column=2, sticky="ew", pady=(10, 0))

        ttk.Label(frame, text="Microphone", style="Hint.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(20, 0))
        self.input_select = ttk.Combobox(frame, state="readonly", width=36, style="Device.TCombobox")
        self.input_select.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.input_select.bind("<<ComboboxSelected>>", self.select_input)
        ttk.Label(frame, text="Input level", style="Hint.TLabel").grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.level_meter = ttk.Progressbar(frame, mode="determinate", maximum=100, style="Meter.Horizontal.TProgressbar")
        self.level_meter.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(4, 0))

        ttk.Label(frame, text="Speakers / headphones", style="Hint.TLabel").grid(row=10, column=0, columnspan=3, sticky="w", pady=(16, 0))
        self.output_select = ttk.Combobox(frame, state="readonly", width=36, style="Device.TCombobox")
        self.output_select.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(4, 0), padx=(0, 8))
        self.output_select.bind("<<ComboboxSelected>>", self.select_output)
        self.refresh_button = ttk.Button(frame, text="Refresh devices", command=self.refresh_devices, style="Control.TButton")
        self.refresh_button.grid(row=11, column=2, sticky="ew", pady=(4, 0))

        ttk.Label(frame, text="Relay address", style="Hint.TLabel").grid(row=12, column=0, columnspan=3, sticky="w", pady=(22, 0))
        self.relay_address = ttk.Entry(frame, width=42, style="Relay.TEntry")
        self.relay_address.grid(row=13, column=0, columnspan=2, sticky="ew", padx=(0, 8), pady=(4, 0))
        if self.settings is not None:
            self.relay_address.insert(0, self.settings["relay_host"])
        self.relay_button = ttk.Button(frame, text="Save address", command=self.save_relay_address, style="Control.TButton")
        self.relay_button.grid(row=13, column=2, sticky="ew", pady=(4, 0))

        self.status = tk.StringVar(value=settings_error or "Ready. Connect to join the shared room.")
        ttk.Label(frame, textvariable=self.status, wraplength=360, style="Hint.TLabel").grid(
            row=14, column=0, columnspan=3, sticky="w", pady=(16, 0)
        )
        self.refresh_devices()
        if self.settings is None:
            self.connect_button.state(["disabled"])
            self.relay_button.state(["disabled"])
            self.input_select.state(["disabled"])
            self.output_select.state(["disabled"])
        self.set_connection_state("Disconnected")
        self.root.after(100, self.process_events)
        self.root.after(50, self.update_level_meter)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    def active(self, generation):
        return generation == self.generation

    def set_connection_state(self, state):
        self.connection_state.set(state)
        colors = {"Connected": "#34d399", "Connecting": "#fbbf24", "Disconnected": "#f87171"}
        self.state_label.configure(foreground=colors[state])

    def post(self, generation, kind, message):
        self.events.put((generation, kind, message))

    def process_events(self):
        try:
            while True:
                generation, kind, message = self.events.get_nowait()
                if not self.active(generation):
                    continue
                if kind == "count":
                    self.people.set(f"{message} {'person' if message == 1 else 'people'} in room")
                    continue
                if kind == "audio_warning":
                    self.status.set(message)
                    continue
                self.status.set(message)
                if kind == "finished":
                    self.set_running(False)
                    self.set_connection_state("Disconnected")
                    self.people.set("0 people in room")
                    self.stop_event = None
                    self.start_meter_preview()
                elif kind == "connected":
                    self.set_connection_state("Connected")
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def set_running(self, running):
        self.connect_button.state(["disabled"] if running or self.settings is None else ["!disabled"])
        self.disconnect_button.state(["!disabled"] if running else ["disabled"])

    def update_level_meter(self):
        self.level_meter["value"] = round(self.mic_level * 100)
        self.root.after(50, self.update_level_meter)

    def record_level(self, indata):
        self.mic_level = input_level(indata)

    def stop_meter_preview(self):
        stream = self.meter_stream
        self.meter_stream = None
        if stream is not None:
            stream.stop()
            stream.close()
        self.mic_level = 0.0

    def start_meter_preview(self):
        if self.meter_stream is not None or (self.stop_event is not None and not self.stop_event.is_set()):
            return
        if self.settings is None:
            return
        try:
            device = audio_devices.resolve("input", self.settings["input_device"])
            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                                    device=device, callback=self.on_preview_input)
            stream.start()
            self.meter_stream = stream
        except (ValueError, sd.PortAudioError) as exc:
            if "stream" in locals():
                stream.close()
            self.status.set(f"Microphone level unavailable: {exc}")

    def on_preview_input(self, indata, frames, time_info, status):
        self.record_level(indata)

    def refresh_devices(self):
        try:
            inputs = audio_devices.choices("input")
            outputs = audio_devices.choices("output")
        except sd.PortAudioError as exc:
            self.status.set(f"Could not list audio devices: {exc}")
            return
        self.input_choices = inputs
        self.output_choices = outputs
        for widget, options, saved in (
            (self.input_select, inputs, self.settings["input_device"] if self.settings else None),
            (self.output_select, outputs, self.settings["output_device"] if self.settings else None),
        ):
            widget["values"] = [label for label, _, _ in options]
            selected = next((i for i, (_, identity, _) in enumerate(options) if identity == saved), None)
            if selected is None:
                widget.set("Unavailable — choose another device")
            else:
                widget.current(selected)
        self.start_meter_preview()

    def select_input(self, event=None):
        index = self.input_select.current()
        if index < 0 or self.settings is None:
            return
        if self.persist({**self.settings, "input_device": self.input_choices[index][1]}):
            self.stop_meter_preview()
            self.start_meter_preview()
            self.status.set("Microphone saved. Reconnect to use it in the call.")

    def select_output(self, event=None):
        index = self.output_select.current()
        if index < 0 or self.settings is None:
            return
        if self.persist({**self.settings, "output_device": self.output_choices[index][1]}):
            self.status.set("Output saved. Reconnect to use it in the call.")

    def persist(self, data):
        try:
            save_settings(data)
        except OSError as exc:
            self.status.set(f"Could not save settings: {exc}")
            return False
        self.settings = data
        return True

    def save_relay_address(self):
        host = self.relay_address.get().strip()
        if not valid_relay_host(host):
            self.status.set("Enter an IP address or hostname, without a port or URL.")
            return
        if self.persist({**self.settings, "relay_host": host}):
            self.relay_address.delete(0, tk.END)
            self.relay_address.insert(0, host)
            self.status.set(f"Relay address saved: {host}. Used for the next connection.")

    def start(self, target):
        self.stop_meter_preview()
        self.generation += 1
        generation = self.generation
        stop = threading.Event()
        with self.lock:
            self.stop_event = stop
        self.set_running(True)
        threading.Thread(target=target, args=(generation, stop), daemon=True).start()

    def connect(self):
        if not RELAY_CERT_SHA256:
            self.status.set("Relay certificate not configured.")
            return
        relay_host = self.settings["relay_host"]
        self.status.set("Connecting to relay…")
        self.set_connection_state("Connecting")
        self.start(lambda generation, stop: self.run_relay(generation, stop, relay_host))

    def register(self, generation, field, value):
        with self.lock:
            if not self.active(generation) or self.stop_event.is_set():
                return False
            setattr(self, field, value)
            return True

    def clear(self, generation, field, value):
        with self.lock:
            if self.active(generation) and getattr(self, field) is value:
                setattr(self, field, None)

    def run_relay(self, generation, stop, relay_host):
        connection = None
        try:
            raw = socket.create_connection((relay_host, RELAY_PORT), timeout=5)
            try:
                # Pin the relay's self-signed certificate before sending audio.
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                connection = context.wrap_socket(raw, server_hostname=relay_host)
                fingerprint = hashlib.sha256(connection.getpeercert(binary_form=True)).hexdigest()
                if not hmac.compare_digest(fingerprint, RELAY_CERT_SHA256):
                    raise ssl.SSLError("Relay certificate changed; connection refused")
            except Exception:
                close_socket(connection or raw)
                raise
            connection.settimeout(1)
            if not self.register(generation, "connection", connection):
                return
            connection.sendall(b"C")
            response = receive_exact(connection, 1, stop)
            if response == b"W":
                self.post(generation, "connected", "Connected to relay. Waiting for others…")
                self.post(generation, "count", 1)
                response = receive_exact(connection, 1, stop)
            if response != b"P":
                self.post(generation, "finished", "Relay refused the connection. Check that everyone has the latest app.")
                return
            self.post(generation, "connected", "Connected — voice is live.")
            audio_error = self.call(generation, stop, connection)
            if audio_error:
                self.post(generation, "finished", f"Audio device error: {audio_error}")
            elif not stop.is_set():
                self.post(generation, "finished", "Relay disconnected. Click Connect to rejoin.")
        except (OSError, ConnectionError, sd.PortAudioError) as exc:
            if not stop.is_set():
                self.post(generation, "finished", f"Connection failed: {exc}")
        finally:
            self.clear(generation, "connection", connection)
            close_socket(connection)

    def call(self, generation, stop, connection):
        outgoing = queue.Queue(maxsize=3)
        incoming = queue.Queue(maxsize=6)
        call_ended = threading.Event()

        def enqueue_latest(target, block):
            try:
                target.put_nowait(block)
            except queue.Full:
                try:
                    target.get_nowait()
                except queue.Empty:
                    pass
                target.put_nowait(block)

        def on_input(indata, frames, time_info, status):
            if not stop.is_set() and not call_ended.is_set():
                self.record_level(indata)
                enqueue_latest(outgoing, bytes(BYTES_PER_BLOCK) if self.muted else indata.tobytes())

        def on_output(outdata, frames, time_info, status):
            outdata.fill(0)
            if self.deafened:
                return
            try:
                block = incoming.get_nowait()
            except queue.Empty:
                return
            outdata[:, 0] = np.frombuffer(block, dtype="<i2")

        def send():
            try:
                while not stop.is_set() and not call_ended.is_set():
                    try:
                        block = outgoing.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    connection.sendall(block)
            except OSError:
                pass
            finally:
                call_ended.set()

        def receive():
            try:
                while not stop.is_set() and not call_ended.is_set():
                    kind, value = receive_packet(connection, stop)
                    if kind == b"U":
                        self.post(generation, "count", value)
                    else:
                        enqueue_latest(incoming, value)
            except (OSError, ConnectionError):
                pass
            finally:
                call_ended.set()

        try:
            input_device = audio_devices.resolve("input", self.settings["input_device"])
            output_device = audio_devices.resolve("output", self.settings["output_device"])
            with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                                device=input_device, callback=on_input), \
                 sd.OutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                                 device=output_device, callback=on_output):
                self.post(generation, "status", "Connected — voice is live.")
                threading.Thread(target=send, daemon=True).start()
                threading.Thread(target=receive, daemon=True).start()
                while not stop.is_set() and not call_ended.wait(0.1):
                    pass
        except (sd.PortAudioError, ValueError) as exc:
            return exc
        finally:
            call_ended.set()
            self.mic_level = 0.0
        return None

    def toggle_mute(self):
        self.muted = not self.muted
        self.mute_button.configure(text="Unmute mic" if self.muted else "Mute mic")

    def toggle_deafen(self):
        self.deafened = not self.deafened
        self.deafen_button.configure(text="Undeafen" if self.deafened else "Deafen")

    def disconnect(self):
        self.generation += 1
        with self.lock:
            if self.stop_event is not None:
                self.stop_event.set()
            connection = self.connection
            self.connection = None
        close_socket(connection)
        self.set_running(False)
        self.set_connection_state("Disconnected")
        self.people.set("0 people in room")
        self.status.set("Disconnected. Click Connect to rejoin.")
        self.stop_event = None
        self.start_meter_preview()

    def quit(self):
        self.disconnect()
        self.stop_meter_preview()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    VoiceApp(window)
    window.mainloop()
