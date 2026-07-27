"""Sends a synthetic shot to the GSPro Open Connect bridge on port 921,
without needing a real SkyTrak or launch monitor attached.

Values are randomly generated within realistic ranges for the yardage you
provide -- this is NOT a physics simulation, just plausible-looking numbers
for testing the JSON format and TCP delivery to automat-lm-bridge/GSPro.
"""

import random
import sys

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


def prompt_yardage():
    raw = input("Target yardage (blank to quit): ").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        print("Not a number, try again.")
        return prompt_yardage()
    if value <= 0 or value > 400:
        print("Keep it between 1 and 400 yards.")
        return prompt_yardage()
    return value


def send_shot(shot_number, yardage):
    shot = realistic_shot_for_yardage(yardage)
    print(
        f"  Ball {shot['ball_speed_mph']:.1f} mph | Club {shot['club_speed_mph']:.1f} mph | "
        f"Smash {shot['smash_factor']:.2f} | Launch {shot['launch_angle']:.1f}° | "
        f"Side {shot['side_angle']:.1f}° | Axis {shot['spin_axis']:.1f}° | "
        f"Spin {shot['total_spin']:.0f} rpm"
    )

    json_str = gspro_connect.build_gspro_json(
        shot_number=shot_number,
        ball_speed_mph=shot["ball_speed_mph"],
        vla=shot["launch_angle"],
        hla=shot["side_angle"],
        total_spin=shot["total_spin"],
        spin_axis=shot["spin_axis"],
        club_speed_mph=shot["club_speed_mph"],
        device_id=TEST_DEVICE_ID,
    )
    try:
        response = gspro_connect.send_to_bridge(json_str)
        if response:
            print(f"  -> bridge response: {response.strip()}")
        else:
            print("  -> sent (no response)")
    except OSError as e:
        print(f"  -> failed to send: {e}")


def main():
    print(f"Sending test shots to {gspro_connect.BRIDGE_HOST}:{gspro_connect.BRIDGE_PORT} "
          f"as DeviceID={TEST_DEVICE_ID!r}. Ctrl+C or blank input to quit.\n")
    shot_number = 0
    while True:
        try:
            yardage = prompt_yardage()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if yardage is None:
            break

        shot_number += 1
        send_shot(shot_number, yardage)
        print()


if __name__ == "__main__":
    main()
