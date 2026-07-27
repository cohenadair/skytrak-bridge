"""GSPro Open Connect v1 JSON building + TCP forwarding.

Shared between run_hook.py (real shot data from SkyTrak) and
send_test_shot.py (synthetic shots for testing the network path without a
launch monitor attached). See https://gsprogolf.com/GSProConnectV1.html.
"""

import json
import socket

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 921
BRIDGE_TIMEOUT = 5.0

DEFAULT_DEVICE_ID = "SkyTrakGSProConnectBridge"


def build_gspro_json(shot_number, ball_speed_mph, vla, hla, total_spin, spin_axis,
                      club_speed_mph=None, device_id=DEFAULT_DEVICE_ID):
    ball_data = {
        "Speed": ball_speed_mph,
        "SpinAxis": spin_axis,
        "TotalSpin": total_spin,
        "HLA": hla,
        "VLA": vla,
    }
    contains_club = club_speed_mph is not None
    payload = {
        "DeviceID": device_id,
        "Units": "Yards",
        "ShotNumber": shot_number,
        "APIversion": "1",
        "BallData": ball_data,
        "ShotDataOptions": {
            "ContainsBallData": True,
            "ContainsClubData": contains_club,
        },
    }
    if contains_club:
        payload["ClubData"] = {"Speed": club_speed_mph}
    return json.dumps(payload)


def send_to_bridge(json_str, host=BRIDGE_HOST, port=BRIDGE_PORT, timeout=BRIDGE_TIMEOUT):
    """Sends json_str to the bridge over a fresh TCP connection (matches the
    old Swift bridge's one-connection-per-shot behavior). Returns the
    bridge's response text, or None if it didn't send one before the
    timeout. Raises OSError (e.g. ConnectionRefusedError) if the connection
    itself fails -- callers decide how to report that.
    """
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall((json_str + "\r\n").encode("utf-8"))
        sock.settimeout(timeout)
        try:
            response = sock.recv(4096)
        except socket.timeout:
            return None
        return response.decode("utf-8", errors="replace") if response else None
