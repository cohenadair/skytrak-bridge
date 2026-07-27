import signal
import sys
import termios
import threading
import time
import traceback

import frida

# Force line-buffered stdout regardless of how this is launched (a real tty
# already line-buffers, but anything else -- a wrapper script, a non-tty
# launch -- silently falls back to full block buffering, which is why shot
# rows were only appearing on exit instead of live).
sys.stdout.reconfigure(line_buffering=True)

PROCESS_NAME = "SKYTRAK"
SCRIPT_PATH = "hook_shot_data.js"

# MARK: - Shot table output

# Units confirmed from the IL2CPP dump: a MpsToMph() conversion utility
# exists (Assembly-CSharp's Units class), meaning the raw STSW struct fields
# are natively in meters/second, converted to mph only for display. No
# conversion utility exists for spin (always rpm) or angles (always degrees,
# matching the "Deg"-suffixed field names elsewhere in the dump).
#
# Ball is converted m/s -> mph for display only (to match SkyTrak's own
# on-screen units) -- the underlying value forwarded to the bridge later
# stays in raw m/s. Club/Smash come from ClubHeadData's estimate, which is
# already in mph (confirmed against SkyTrak's own on-screen value -- see
# mps_to_mph usage below). Axis and Side are ordered to match SkyTrak's own
# table.
SHOT_TABLE_COLUMNS = ["Shot", "Ball (mph)", "Club (mph)", "Smash", "Launch (°)", "Axis (°)", "Side (°)", "Spin (rpm)"]
SHOT_TABLE_COLUMN_WIDTH = 10

# Same conversion constant found in the dump's Units class (Mph_To_Mps =
# 0.44704723), used inverted here so displayed mph matches SkyTrak's own
# math as closely as possible rather than a generic textbook constant.
MPH_TO_MPS = 0.44704723


def mps_to_mph(mps):
    return mps / MPH_TO_MPS

# Set by print_shot_table_header and reused by the termination handler so the
# closing border matches the table's width.
shot_table_border = ""
shot_counter = 0


def pad_column(value, width=SHOT_TABLE_COLUMN_WIDTH):
    if len(value) >= width:
        return value[:width]
    return value + " " * (width - len(value))


def shot_table_row(values):
    return "| " + " | ".join(pad_column(v) for v in values) + " |"


def print_shot_table_header():
    global shot_table_border
    header = shot_table_row(SHOT_TABLE_COLUMNS)
    shot_table_border = "-" * len(header)
    print(shot_table_border)
    print(header)
    print(shot_table_border)


def print_shot_table_footer():
    if shot_table_border:
        print(shot_table_border)


def print_shot_table_row(shot_number, ball_speed, club_speed, smash_factor, launch_angle, side_angle, spin_axis, total_spin, valid):
    # ball_speed arrives in raw m/s; convert to mph for display only.
    # club_speed (from ClubHeadData) is already mph -- no conversion.
    club_str = "-" if club_speed is None else f"{club_speed:.1f}"
    smash_str = "-" if smash_factor is None else f"{smash_factor:.2f}"
    values = [
        str(shot_number),
        f"{mps_to_mph(ball_speed):.1f}",
        club_str,
        smash_str,
        f"{launch_angle:.1f}",
        f"{spin_axis:.1f}",
        f"{side_angle:.1f}",
        f"{total_spin:.0f}",
    ]
    row = shot_table_row(values)
    if not valid:
        row += "  (no ball data -- not forwarded)"
    print(row)


def is_valid_shot(payload):
    # The box fires OnShotBallData with an all-zero struct when it couldn't
    # read the ball (e.g. it was moved out of the hitting area mid-detection,
    # which SkyTrak's own UI surfaces as a "ball data couldn't be read"
    # error popup). Ball speed is never genuinely 0 for a real shot.
    return payload["totalSpeed"] != 0


# MARK: - Terminal control-character echo suppression
#
# The tty, not this process, echoes "^C" when Ctrl+C is pressed (controlled
# by the ECHOCTL local flag). Turn it off while running so the interrupt
# doesn't clutter the table, and restore it on exit.

_saved_termios = None


def suppress_control_character_echo():
    global _saved_termios
    try:
        fd = sys.stdin.fileno()
        _saved_termios = termios.tcgetattr(fd)
        raw = termios.tcgetattr(fd)
        raw[3] = raw[3] & ~termios.ECHOCTL  # lflags
        termios.tcsetattr(fd, termios.TCSANOW, raw)
    except Exception:
        pass


def restore_control_character_echo():
    if _saved_termios is not None:
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, _saved_termios)
        except Exception:
            pass


_active_session = None


def install_shot_table_termination_handler():
    def handler(signum, frame):
        restore_control_character_echo()
        print_shot_table_footer()
        # Detach cleanly so the injected agent unloads -- skipping this is
        # what left the target process with a stale/broken agent last time,
        # which made every subsequent attach() hang forever with no error.
        if _active_session is not None:
            try:
                _active_session.detach()
                print("[info] session detached cleanly")
            except Exception as e:
                print("[error] detach failed:", repr(e))
        sys.exit(0)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


# MARK: - Frida message handling
#
# OnShotBallData and ClubHeadData.PredictClubHeadData are separate,
# independently-firing hooks with no shared shot ID, so a single-slot
# rendezvous pairs them up: whichever arrives first waits briefly for the
# other. If ClubHeadData never shows up (e.g. it doesn't fire in some mode),
# the grace-period timer flushes the row with "-" for Club/Smash rather than
# waiting forever.

CLUB_DATA_GRACE_PERIOD = 1.5  # seconds to wait for the paired ClubHeadData event

_pending_ball = None  # {"shot_number": int, "payload": dict, "timer": Timer}
_pending_club = None  # a ClubHeadData payload arrived before its ball data
_state_lock = threading.Lock()


def _print_merged_row(shot_number, ball_payload, club_payload):
    print_shot_table_row(
        shot_number=shot_number,
        ball_speed=ball_payload["totalSpeed"],
        club_speed=club_payload["clubSpeed"] if club_payload else None,
        smash_factor=club_payload["smashFactor"] if club_payload else None,
        launch_angle=ball_payload["launchAngle"],
        side_angle=ball_payload["horizontalAngle"],
        spin_axis=ball_payload["spinAxis"],
        total_spin=ball_payload["totalSpin"],
        valid=is_valid_shot(ball_payload),
    )


def _on_grace_period_expired(shot_number):
    global _pending_ball
    with _state_lock:
        pending = _pending_ball
        if pending is None or pending["shot_number"] != shot_number:
            return
        _pending_ball = None
    _print_merged_row(shot_number, pending["payload"], None)


def on_message(message, data):
    global shot_counter, _pending_ball, _pending_club

    if message["type"] != "send":
        if message["type"] == "error":
            print("[error]", message["stack"])
        return

    payload = message["payload"]
    if not isinstance(payload, dict) or "label" not in payload:
        # Startup/status messages (module base, hook-install confirmation).
        print("[info]", payload)
        return

    label = payload["label"]

    if label == "ClubHeadData":
        with _state_lock:
            waiting = _pending_ball
            if waiting is not None:
                waiting["timer"].cancel()
                _pending_ball = None
            else:
                _pending_club = payload
        if waiting is not None:
            _print_merged_row(waiting["shot_number"], waiting["payload"], payload)
        return

    if label != "OnShotBallData":
        # OnShotEnded fires right after with identical values (confirmed by
        # real-shot testing) -- no separate row needed.
        return

    shot_counter += 1
    shot_number = shot_counter

    with _state_lock:
        # If a previous row's grace timer somehow hasn't fired yet (shots
        # taken in very rapid succession), flush it now rather than losing
        # it when this shot's data overwrites the pending slot.
        stale = _pending_ball
        if stale is not None:
            stale["timer"].cancel()
            _pending_ball = None

        club_payload = _pending_club
        _pending_club = None
        if club_payload is None:
            timer = threading.Timer(CLUB_DATA_GRACE_PERIOD, _on_grace_period_expired, args=(shot_number,))
            timer.daemon = True
            _pending_ball = {"shot_number": shot_number, "payload": payload, "timer": timer}
            timer.start()

    if stale is not None:
        _print_merged_row(stale["shot_number"], stale["payload"], None)
    if club_payload is not None:
        _print_merged_row(shot_number, payload, club_payload)


def on_detached(reason, crash):
    # Fires on any detach, including our own clean shutdown (reason
    # "application-requested"), which isn't an error. Anything else means
    # the target quit/crashed or the agent died -- surface that immediately
    # instead of silently going quiet.
    if reason == "application-requested":
        return
    print(f"[error] session detached unexpectedly: reason={reason} crash={crash}")


def _attach_with_watchdog(device, pid, warn_after=8.0):
    result = {}

    def do_attach():
        try:
            result["session"] = device.attach(pid)
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=do_attach, daemon=True)
    t.start()
    t.join(warn_after)
    if t.is_alive():
        print(
            f"[warn] attach() has been hanging for {warn_after:.0f}s. This "
            "usually means a previous run left a stale Frida agent inside "
            "the target process. Quit SkyTrakInstrumented.app, relaunch it, "
            "and try again."
        )
        t.join()  # keep waiting -- report whatever eventually happens

    if "error" in result:
        raise result["error"]
    return result["session"]


def main():
    global _active_session

    print("[info] enumerating processes...")
    device = frida.get_local_device()
    target = None
    for p in device.enumerate_processes():
        if p.name.upper() == PROCESS_NAME:
            target = p
            break
    if target is None:
        print(f"[error] could not find a running process matching '{PROCESS_NAME}'")
        sys.exit(1)
    print(f"[info] found target: pid={target.pid}")

    print("[info] attaching...")
    session = _attach_with_watchdog(device, target.pid)
    _active_session = session
    session.on("detached", on_detached)
    print("[info] attached")

    print("[info] loading hook script...")
    with open(SCRIPT_PATH) as f:
        source = f.read()
    script = session.create_script(source)
    script.on("message", on_message)
    script.load()
    print("[info] hook script loaded")

    print_shot_table_header()
    suppress_control_character_echo()
    install_shot_table_termination_handler()

    while True:
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        restore_control_character_echo()
        traceback.print_exc()
        sys.exit(1)
