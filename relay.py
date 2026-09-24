"""TLS voice relay for one shared, multi-person room."""

import argparse
import queue
import select
import socket
import ssl
import struct
import threading
import time


PORT = 38452
SAMPLES = 960
FRAME_SIZE = SAMPLES * 2  # 20 ms of 48 kHz mono int16 audio
SILENCE = bytes(FRAME_SIZE)


def read_exact(connection, count):
    result = bytearray()
    while len(result) < count:
        chunk = connection.recv(count - len(result))
        if not chunk:
            raise ConnectionError("Connection closed")
        result.extend(chunk)
    return bytes(result)


def close(connection):
    if connection is not None:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            connection.close()
        except OSError:
            pass


def put_latest(target, frame):
    try:
        target.put_nowait(frame)
    except queue.Full:
        try:
            target.get_nowait()
        except queue.Empty:
            pass
        target.put_nowait(frame)


def mix(frames):
    """Add signed PCM samples and clip, without sending a speaker their own voice."""
    samples = [struct.unpack("<960h", frame) for frame in frames]
    totals = [sum(channel[i] for channel in samples) for i in range(SAMPLES)]
    return [struct.pack("<960h", *(max(-32768, min(32767, totals[i] - channel[i]))
                                  for i in range(SAMPLES))) for channel in samples]


def ready_peers(peers):
    return [peer for peer in peers if peer.ready.is_set() and not peer.closed.is_set()]


class Peer:
    def __init__(self, connection):
        self.connection = connection
        self.incoming = queue.Queue(maxsize=3)
        self.outgoing = queue.Queue(maxsize=3)
        self.count_updates = queue.Queue(maxsize=1)
        self.ready = threading.Event()
        self.closed = threading.Event()


class Relay:
    def __init__(self, context=None):
        self.context = context
        self.peers = []
        self.lock = threading.Lock()
        self.mixer = threading.Thread(target=self.mix_loop, daemon=True)
        self.mixer.start()

    def handle(self, connection):
        peer = None
        try:
            if self.context is not None:
                connection.settimeout(10)
                connection = self.context.wrap_socket(connection, server_side=True)
            connection.settimeout(10)
            if read_exact(connection, 1) != b"C":
                connection.sendall(b"N")
                return
            peer = Peer(connection)
            with self.lock:
                self.peers = [existing for existing in self.peers if not existing.closed.is_set()]
                if not self.peers:
                    connection.sendall(b"W")
                    self.peers.append(peer)
                else:
                    # Complete the existing waiter's handshake before sending
                    # any audio to it. Further arrivals simply join the call.
                    if len(self.peers) == 1 and not self.peers[0].ready.is_set():
                        try:
                            self.peers[0].connection.sendall(b"P")
                        except OSError:
                            self.peers[0].closed.set()
                            close(self.peers[0].connection)
                            self.peers.clear()
                            connection.sendall(b"W")
                            self.peers.append(peer)
                        else:
                            self.peers[0].ready.set()
                    if peer not in self.peers:
                        connection.sendall(b"P")
                        peer.ready.set()
                        self.peers.append(peer)
                self.update_counts()

            self.wait_and_stream(peer)
        except (OSError, ConnectionError):
            pass
        finally:
            if peer is not None:
                peer.closed.set()
                with self.lock:
                    if peer in self.peers:
                        self.peers.remove(peer)
                        self.update_counts()
            close(connection)

    def update_counts(self):
        """Queue a fresh count for each paired caller (self.lock held)."""
        peers = ready_peers(self.peers)
        for peer in peers:
            put_latest(peer.count_updates, len(peers))

    def wait_and_stream(self, peer):
        connection = peer.connection
        if not peer.ready.is_set():
            while not peer.ready.wait(0.2):
                if select.select([connection], [], [], 0)[0]:
                    # A waiting client does not transmit until paired.
                    connection.recv(1)
                    return
        connection.settimeout(None)
        threading.Thread(target=self.send_loop, args=(peer,), daemon=True).start()
        while True:
            put_latest(peer.incoming, read_exact(connection, FRAME_SIZE))

    def send_loop(self, peer):
        try:
            while not peer.closed.is_set():
                try:
                    count = peer.count_updates.get_nowait()
                except queue.Empty:
                    pass
                else:
                    peer.connection.sendall(b"U" + struct.pack("!H", count))
                try:
                    frame = peer.outgoing.get(timeout=0.02)
                except queue.Empty:
                    continue
                peer.connection.sendall(b"A" + frame)
        except OSError:
            close(peer.connection)

    def mix_loop(self):
        next_tick = time.monotonic()
        while True:
            next_tick += 0.02
            time.sleep(max(0, next_tick - time.monotonic()))
            if next_tick < time.monotonic() - 0.02:
                next_tick = time.monotonic()
            with self.lock:
                peers = ready_peers(self.peers)
            if not peers:
                continue
            frames = []
            for peer in peers:
                try:
                    frames.append(peer.incoming.get_nowait())
                except queue.Empty:
                    frames.append(SILENCE)
            for peer, frame in zip(peers, mix(frames)):
                put_latest(peer.outgoing, frame)

    def serve(self, host="0.0.0.0", port=PORT, ready=None, stop=None):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
            listener.listen(32)
            listener.settimeout(0.2)
            if ready is not None:
                ready.put(listener.getsockname()[1])
            while stop is None or not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self.handle, args=(connection,), daemon=True).start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Frincoms TLS relay")
    parser.add_argument("--cert", required=True, help="TLS certificate chain PEM")
    parser.add_argument("--key", required=True, help="TLS private key PEM")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    print(f"Frincoms relay listening on {args.port}", flush=True)
    Relay(context).serve(port=args.port)
