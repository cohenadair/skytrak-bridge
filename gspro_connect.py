"""GSPro Open Connect v1 JSON building + TCP forwarding.

Shared between run_hook.py (real shot data from SkyTrak) and
send_test_shot.py (synthetic shots for testing the network path without a
launch monitor attached). See https://gsprogolf.com/GSProConnectV1.html.

Shots go to Muni Golf (https://munigolfsim.com), which listens for Open
Connect clients itself on the port it shows in-game (9210 by default). Like
GSPro, it's talked to over a single long-lived connection with periodic
heartbeats -- the way real launch monitor software does it.
"""

import json
import socket
import threading
import time

MUNI_HOST = "127.0.0.1"
MUNI_PORT = 9210
CONNECT_TIMEOUT = 5.0

DEFAULT_DEVICE_ID = "SkyTrakGSProConnectBridge"

# How often a persistent connection sends an "I'm still here and ready"
# heartbeat, and how long it waits before retrying a failed connect.
HEARTBEAT_INTERVAL = 5.0
RECONNECT_INTERVAL = 3.0

# SkyTrak's side angle (HLA) on putts is unreliable, so when the game says
# the player is putting, the side angle sent is clamped to +/- this many
# degrees (0 = always dead straight). The current club comes from Muni Golf's
# Code 201 "Player Information" messages; see cap_putting_side().
PUTTING_SIDE_CAP = 0.0
PUTTER_CLUB = "PT"


def _base_payload(shot_number, device_id):
    return {
        "DeviceID": device_id,
        "Units": "Yards",
        "ShotNumber": shot_number,
        "APIversion": "1",
    }


def build_gspro_json(shot_number, ball_speed_mph, vla, hla, total_spin, spin_axis,
                      club_speed_mph=None, device_id=DEFAULT_DEVICE_ID):
    contains_club = club_speed_mph is not None
    payload = _base_payload(shot_number, device_id)
    payload["BallData"] = {
        "Speed": ball_speed_mph,
        "SpinAxis": spin_axis,
        "TotalSpin": total_spin,
        "HLA": hla,
        "VLA": vla,
    }
    payload["ShotDataOptions"] = {
        "ContainsBallData": True,
        "ContainsClubData": contains_club,
        "LaunchMonitorIsReady": True,
        "LaunchMonitorBallDetected": True,
        "IsHeartBeat": False,
    }
    if contains_club:
        payload["ClubData"] = {"Speed": club_speed_mph}
    return json.dumps(payload)


def build_heartbeat_json(device_id=DEFAULT_DEVICE_ID):
    payload = _base_payload(0, device_id)
    payload["ShotDataOptions"] = {
        "ContainsBallData": False,
        "ContainsClubData": False,
        "LaunchMonitorIsReady": True,
        "LaunchMonitorBallDetected": True,
        "IsHeartBeat": True,
    }
    return json.dumps(payload)


def is_putting(player_info):
    return bool(player_info) and player_info.get("Club") == PUTTER_CLUB


def cap_putting_side(hla, player_info, cap=PUTTING_SIDE_CAP):
    """Returns the side angle to send: clamped to [-cap, cap] when the game
    says the player has the putter out, otherwise hla unchanged."""
    if not is_putting(player_info):
        return hla
    # + 0.0 turns a -0.0 result (negative hla, cap 0) into 0.0.
    return max(-cap, min(cap, hla)) + 0.0


def _iter_json_messages(buffer):
    """Yields (message, remaining_buffer) for each complete JSON object at
    the start of buffer. Receivers may or may not newline-separate replies,
    and a reply can be split across recv() calls, so this parses objects
    directly rather than splitting on lines."""
    decoder = json.JSONDecoder()
    while True:
        buffer = buffer.lstrip()
        if not buffer:
            return
        try:
            message, end = decoder.raw_decode(buffer)
        except ValueError:
            return  # incomplete -- wait for more data
        buffer = buffer[end:]
        yield message, buffer


class PersistentConnection:
    """One long-lived Open Connect connection, as GSPro-style receivers
    (Muni Golf) expect: connects in the background, keeps retrying until the
    game is listening, sends heartbeats so it shows the launch monitor as
    ready, and drains whatever the game sends back.

    log is called with (level, message) for connection state changes and,
    when verbose, every message the receiver sends.
    """

    def __init__(self, host, port, device_id=DEFAULT_DEVICE_ID, log=None, verbose=False):
        self.host = host
        self.port = port
        self.device_id = device_id
        self._log = log or (lambda level, msg: print(f"[{level}] {msg}"))
        self._verbose = verbose
        # Receivers ack heartbeats with the same Code 200 as shots; tracked so
        # those acks can be left out of the log instead of burying the prompt.
        self._last_sent_heartbeat = False
        # Latest "Player" block from the game's Code 201 messages (Handed,
        # Club, Surface, DistanceToTarget), or None until one arrives.
        self.player_info = None
        self._sock = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_connect_error = None
        threading.Thread(target=self._maintain, daemon=True).start()

    @property
    def connected(self):
        return self._sock is not None

    def send(self, json_str, heartbeat=False):
        """Sends json_str on the open connection. Raises OSError if there
        isn't one (or the send fails) -- the background loop reconnects."""
        with self._lock:
            if self._sock is None:
                raise ConnectionError(f"not connected to {self.host}:{self.port}")
            try:
                self._sock.sendall((json_str + "\n").encode("utf-8"))
                self._last_sent_heartbeat = heartbeat
            except OSError:
                self._drop_locked()
                raise

    def close(self):
        self._stop.set()
        with self._lock:
            self._drop_locked()

    def wait_until_connected(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.connected and time.monotonic() < deadline:
            time.sleep(0.1)
        return self.connected

    def _drop_locked(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _maintain(self):
        while not self._stop.is_set():
            if self._sock is None:
                self._try_connect()
                if self._sock is None:
                    self._stop.wait(RECONNECT_INTERVAL)
                    continue
            try:
                self.send(build_heartbeat_json(self.device_id), heartbeat=True)
            except OSError:
                self._log("warn", f"lost connection to {self.host}:{self.port}, reconnecting...")
                continue
            self._stop.wait(HEARTBEAT_INTERVAL)

    def _try_connect(self):
        try:
            sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        except OSError as e:
            # Only log when the reason changes, so a game that isn't open
            # yet doesn't spam the shot table every few seconds.
            if str(e) != self._last_connect_error:
                self._log("warn", f"can't reach {self.host}:{self.port} ({e}) -- "
                                  "is the game open and showing that port? Retrying...")
                self._last_connect_error = str(e)
            return
        sock.settimeout(None)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        with self._lock:
            self._sock = sock
        self._last_connect_error = None
        self._log("info", f"connected to {self.host}:{self.port}")
        threading.Thread(target=self._read, args=(sock,), daemon=True).start()

    def _read(self, sock):
        buffer = ""
        while True:
            try:
                data = sock.recv(4096)
            except OSError:
                data = b""
            if not data:
                with self._lock:
                    if self._sock is sock:
                        self._drop_locked()
                        if not self._stop.is_set():
                            self._log("warn", f"{self.host}:{self.port} closed the connection, reconnecting...")
                return
            buffer += data.decode("utf-8", errors="replace")
            for message, buffer in _iter_json_messages(buffer):
                self._handle_message(message)
            if len(buffer) > 65536:
                buffer = ""  # not JSON we understand; don't grow forever

    def _handle_message(self, message):
        if not isinstance(message, dict):
            return
        if message.get("Code") == 201 and isinstance(message.get("Player"), dict):
            self.player_info = message["Player"]
        if self._verbose and not (self._last_sent_heartbeat and message.get("Code") == 200):
            self._log("info", f"receiver: {json.dumps(message)}")


class ShotSender:
    """Sends shots to Muni Golf over one persistent connection."""

    def __init__(self, host=MUNI_HOST, port=MUNI_PORT, device_id=DEFAULT_DEVICE_ID,
                 log=None, verbose=False):
        self.host = host
        self.port = port
        self._connection = PersistentConnection(self.host, self.port, device_id,
                                                log=log, verbose=verbose)

    @property
    def player_info(self):
        """Muni's latest reported player state, or None until it sends one."""
        return self._connection.player_info

    @property
    def address(self):
        return f"{self.host}:{self.port}"

    def send(self, json_str):
        """Sends a shot. Muni's replies are logged by the reader when
        verbose. Raises OSError on failure."""
        self._connection.send(json_str)

    def wait_until_connected(self, timeout):
        return self._connection.wait_until_connected(timeout)

    def close(self):
        self._connection.close()


def add_receiver_arguments(parser):
    parser.add_argument("--host", default=MUNI_HOST,
                        help=f"Muni Golf host (default: {MUNI_HOST})")
    parser.add_argument("--port", type=int, default=MUNI_PORT,
                        help=f"Muni Golf's Open Connect port, as shown in-game (default: {MUNI_PORT})")
