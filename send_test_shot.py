"""Sends synthetic shots over GSPro Open Connect to Muni Golf (port 9210 by
default), without needing a real SkyTrak or launch monitor attached.

Values are randomly generated within realistic ranges for the yardage you
provide -- this is NOT a physics simulation, just plausible-looking numbers
for testing the JSON format and TCP delivery to the receiver.
"""

import argparse
import random
import os
import sys
import termios
import threading
import tty

import gspro_connect

TEST_DEVICE_ID = "SkyTrakGSProConnectBridge-TestShotSender"

# (launch angle range, total spin range, smash factor range, carry yards per
# mph of ball speed) by rough club category. Not real ballistics -- just
# keeps numbers in believable neighborhoods for the given yardage.
_SHOT_PROFILES = [
    (100, (26, 34), (7500, 9500), (1.15, 1.28), (1.6, 1.9)),   # wedge
    (160, (20, 27), (6500, 8000), (1.28, 1.35), (1.9, 2.1)),   # short iron
    (200, (15, 21), (4500, 6500), (1.35, 1.42), (2.1, 2.3)),   # mid/long iron
    (float("inf"), (8, 14), (2000, 3200), (1.42, 1.50), (2.3, 2.6)),  # driver
]


def realistic_shot_for_yardage(yardage):
    for max_yardage, launch_range, spin_range, smash_range, carry_per_mph_range in _SHOT_PROFILES:
        if yardage <= max_yardage:
            break

    ball_speed_mph = yardage / random.uniform(*carry_per_mph_range)
    ball_speed_mph = max(40.0, min(190.0, ball_speed_mph))  # sane floor/ceiling regardless of the above

    smash_factor = random.uniform(*smash_range)
    return {
        "ball_speed_mph": ball_speed_mph,
        "club_speed_mph": ball_speed_mph / smash_factor,
        "smash_factor": smash_factor,
        "launch_angle": random.uniform(*launch_range),
        "side_angle": random.uniform(-3.0, 3.0),  # HLA -- kept tight, not far off line
        "spin_axis": random.uniform(-6.0, 6.0),
        "total_spin": random.uniform(*spin_range),
    }


def realistic_putt_for_feet(feet):
    # Rough fit to real putting data (~4.7 mph for 10 ft, ~8.2 mph for 30
    # ft). Side is deliberately sloppy, like SkyTrak's, to exercise the cap.
    return {
        "ball_speed_mph": 1.5 * feet ** 0.5 * random.uniform(0.95, 1.05),
        "club_speed_mph": None,
        "smash_factor": None,
        "launch_angle": random.uniform(1.0, 3.0),
        "side_angle": random.uniform(-2.0, 2.0),
        "spin_axis": 0.0,
        "total_spin": random.uniform(100, 400),
    }


# Muni's replies arrive on a background thread while the prompt is waiting
# for input. To keep them from landing on top of the prompt, the prompt is
# read by read_line() (not input()), which tracks exactly what's been typed;
# log() prints each message above the prompt and redraws it.
# The prompt text comes from a function, so a redraw also picks up changes
# -- e.g. Muni reporting the putter right after connecting turns "Target
# yardage" into "Putt distance in feet".
_prompt_for = None      # returns the prompt text; None when not reading
_shown_prompt = None    # prompt text last drawn
_typed = ""             # what's been typed at the active prompt so far
_output_lock = threading.Lock()


def log(level, message):
    global _shown_prompt
    with _output_lock:
        line = f"[{level}] {message}"
        if _prompt_for is None:
            print(line, flush=True)
            return
        _shown_prompt = _prompt_for()
        sys.stdout.write(f"\r\033[K{line}\n{_shown_prompt}{_typed}")
        sys.stdout.flush()


def read_line(prompt_for):
    """Like input(prompt_for()), minus line editing beyond backspace --
    enough for typing a number. Returns (text, prompt text shown when Enter
    was pressed). Raises KeyboardInterrupt on Ctrl+C and EOFError on Ctrl+D
    (on an empty line), like input()."""
    global _prompt_for, _shown_prompt, _typed
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        prompt = prompt_for()
        return input(prompt), prompt

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    with _output_lock:
        _prompt_for, _shown_prompt, _typed = prompt_for, prompt_for(), ""
        sys.stdout.write(_shown_prompt)
        sys.stdout.flush()
    try:
        tty.setcbreak(fd)  # keystrokes one at a time, no echo; Ctrl+C still works
        in_escape = False
        while True:
            ch = os.read(fd, 1).decode("utf-8", errors="ignore")
            with _output_lock:
                if in_escape:
                    # Drop arrow/function key sequences (ESC [ ... letter).
                    in_escape = not ch.isalpha() and ch != "~"
                elif ch == "\x1b":
                    in_escape = True
                elif ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return _typed, _shown_prompt
                elif ch in ("\x7f", "\b"):
                    if _typed:
                        _typed = _typed[:-1]
                        sys.stdout.write("\b \b")
                elif ch == "\x04":
                    if not _typed:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        raise EOFError
                elif ch.isprintable() and ch:
                    _typed += ch
                    sys.stdout.write(ch)
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        with _output_lock:
            _prompt_for, _shown_prompt, _typed = None, None, ""


PUTT_PROMPT = "Putt distance in feet (blank to quit): "
SHOT_PROMPT = "Target yardage (blank to quit): "


def prompt_yardage(sender):
    """Asks for a distance, in feet while Muni has the putter out and yards
    otherwise. Returns (distance, whether the prompt shown asked for feet),
    or (None, None) on blank input."""
    while True:
        raw, shown = read_line(
            lambda: PUTT_PROMPT if gspro_connect.is_putting(sender.player_info) else SHOT_PROMPT)
        raw = raw.strip()
        if not raw:
            return None, None
        try:
            value = float(raw)
        except ValueError:
            print("Not a number, try again.")
            continue
        if value <= 0 or value > 400:
            print("Keep it between 1 and 400.")
            continue
        return value, shown == PUTT_PROMPT


def send_shot(sender, shot_number, distance, putting=False):
    shot = realistic_putt_for_feet(distance) if putting else realistic_shot_for_yardage(distance)
    club = "-" if shot["club_speed_mph"] is None else f"{shot['club_speed_mph']:.1f} mph"
    smash = "-" if shot["smash_factor"] is None else f"{shot['smash_factor']:.2f}"
    print(
        f"  Ball {shot['ball_speed_mph']:.1f} mph | Club {club} | "
        f"Smash {smash} | Launch {shot['launch_angle']:.1f}° | "
        f"Side {shot['side_angle']:.1f}° | Axis {shot['spin_axis']:.1f}° | "
        f"Spin {shot['total_spin']:.0f} rpm"
    )

    sent_hla = gspro_connect.cap_putting_side(shot["side_angle"], sender.player_info)
    if sent_hla != shot["side_angle"]:
        print(f"  (putt: side sent as {sent_hla:.1f}°)")

    json_str = gspro_connect.build_gspro_json(
        shot_number=shot_number,
        ball_speed_mph=shot["ball_speed_mph"],
        vla=shot["launch_angle"],
        hla=sent_hla,
        total_spin=shot["total_spin"],
        spin_axis=shot["spin_axis"],
        club_speed_mph=shot["club_speed_mph"],
        device_id=TEST_DEVICE_ID,
    )
    try:
        sender.send(json_str)
        print(f"  -> sent to {sender.address}")
    except OSError as e:
        print(f"  -> failed to send: {e}")


def main():
    parser = argparse.ArgumentParser(description="Send synthetic test shots over GSPro Open Connect.")
    gspro_connect.add_receiver_arguments(parser)
    args = parser.parse_args()

    sender = gspro_connect.ShotSender(host=args.host, port=args.port,
                                      device_id=TEST_DEVICE_ID, log=log, verbose=True)
    print(f"Sending test shots to Muni Golf at {sender.address} "
          f"as DeviceID={TEST_DEVICE_ID!r}. Ctrl+C or blank input to quit.\n")
    sender.wait_until_connected(timeout=2)
    shot_number = 0
    while True:
        try:
            yardage, prompted_putting = prompt_yardage(sender)
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if yardage is None:
            break

        # Muni can change club while input() is waiting (e.g. switching to
        # the putter once the ball is on the green), so decide putt vs full
        # shot from the club as of now, not as of when the prompt printed.
        putting = gspro_connect.is_putting(sender.player_info)
        if putting != prompted_putting:
            if putting:
                print(f"  (Muni switched to the putter -- sending a {yardage:g} ft putt)")
            else:
                print(f"  (Muni switched off the putter -- sending a {yardage:g} yd shot)")

        shot_number += 1
        send_shot(sender, shot_number, yardage, putting)
        print()
    sender.close()


if __name__ == "__main__":
    main()
