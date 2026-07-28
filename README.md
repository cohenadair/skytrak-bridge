# SkyTrak GSPro Connect Bridge

Reads live shot data directly out of SkyTrak's own running app (via [Frida](https://frida.re/)
instrumentation of its Unity/IL2CPP internals). Data is available the instant it's computed by the Skytrak app.

Speaks the [GSPro Open Connect](https://gsprogolf.com/GSProConnectV1.html) protocol on port 921
-- the same local JSON protocol [automat.golf](https://automat.golf) picks up via
`automat-lm-bridge`.

**Status:** live shot capture works, prints to a table in your terminal, and forwards each valid
shot as GSPro Open Connect JSON to `automat-lm-bridge` on `127.0.0.1:921`.

## TL;DR

Already done the one-time setup below? Here's the whole day-to-day flow:

1. Run `automat-lm-bridge` (get it from automat.golf).
2. Launch `reverse-eng/SkyTrakInstrumented.app` -- **not** the real SkyTrak app -- and connect it
   to your unit as usual.
3. `./venv/bin/python3 run_hook.py`
4. Hit balls. Shots print live in your terminal and forward automatically to automat.golf.
5. Use **Chrome**, not Safari, for automat.golf -- Safari blocks it (see Troubleshooting).
6. Ctrl+C to stop.

Haven't set up yet? Start at One-time setup below.

## Requirements

- macOS, Apple Silicon
- OG SkyTrak launch monitor (Skytrak+ not tested) and the SkyTrak app installed in `/Applications`
- Xcode command line tools (for `codesign`, `lipo`, `xattr`)
- [Homebrew](https://brew.sh)
- [.NET SDK](https://dotnet.microsoft.com/) (to build Il2CppDumper)
- Python 3.10+ (macOS's built-in `python3` is 3.9, too old for `frida-tools` --
  `brew install python@3.12` if needed)

## One-time setup

1. Clone this repo and set up a virtualenv:

   ```bash
   git clone git@github.com:cohenadair/skytrak-gspro-connect-bridge.git
   cd skytrak-gspro-connect-bridge
   /opt/homebrew/bin/python3.12 -m venv venv
   ./venv/bin/pip install --upgrade pip
   ./venv/bin/pip install frida-tools
   ```

2. Build [Il2CppDumper](https://github.com/Perfare/Il2CppDumper) -- used to verify SkyTrak's
   internal function addresses haven't shifted after an update:

   ```bash
   git clone --depth 1 https://github.com/Perfare/Il2CppDumper.git reverse-eng/Il2CppDumper
   dotnet publish reverse-eng/Il2CppDumper/Il2CppDumper/Il2CppDumper.csproj \
     -c Release -f net8.0 -r osx-arm64 --self-contained true \
     -o reverse-eng/Il2CppDumper/publish
   ```

3. Install SkyTrak normally (`/Applications/SkyTrak.app`) and run it once so it's paired with
   your launch monitor as usual.

4. Run the bridge. On first run it detects that `SkyTrakInstrumented.app` (the re-signed working
   copy Frida actually attaches to) doesn't exist yet and offers to create it automatically:

   ```bash
   ./venv/bin/python3 run_hook.py
   ```

   Answer `y` when prompted. This copies `/Applications/SkyTrak.app` into
   `reverse-eng/SkyTrakInstrumented.app`, strips its quarantine flag, and re-signs it with the
   entitlements Frida needs to attach (`reverse-eng/entitlements.plist`). The original app, and
   Apple's signature on it, are left completely untouched.

5. If macOS prompts for Screen Recording or other permissions, grant them to
   `SkyTrakInstrumented.app` the same way you would for the real app.

## Day-to-day use

1. Launch `reverse-eng/SkyTrakInstrumented.app` -- **not** the app in `/Applications`. Frida
   can't attach to the real one; it's missing the debug entitlement `SkyTrakInstrumented.app` was
   re-signed with (Hardened Runtime blocks `task_for_pid()` without it). Don't run both at once
   -- they'll fight over the connection to your SkyTrak unit.
2. Connect it to your SkyTrak unit as usual.
3. In a terminal:

   ```bash
   cd skytrak-gspro-connect-bridge
   ./venv/bin/python3 run_hook.py
   ```

4. On startup, the script checks whether `/Applications/SkyTrak.app` has been updated since
   `SkyTrakInstrumented.app` was last refreshed. If so, it'll ask:

   ```
   SkyTrak.app has been updated (5.6.14.6.30.25.9 -> 5.7.0.0.1.2.3). Update SkyTrakInstrumented.app to match? [y/N]
   ```

   - **Yes**: re-copies and re-signs `SkyTrakInstrumented.app`, then re-runs Il2CppDumper and
     checks whether the hardcoded function addresses (RVAs) in `hook_shot_data.js` still point
     at the right code. If any have moved, it prints exactly which ones changed -- that means
     `hook_shot_data.js` needs manual updating (new RVAs, and possibly re-checked struct field
     offsets) before the bridge can be trusted on the new version.
   - **No**: keeps using the existing `SkyTrakInstrumented.app` as-is.

   It also always logs a warning if `SkyTrakInstrumented.app`'s version doesn't match
   `KNOWN_GOOD_SKYTRAK_VERSION` in `run_hook.py` -- that constant records the last version this
   bridge's hooks were actually verified against. Update it by hand once you've confirmed a newer
   version still works correctly.

5. Hit some shots. You'll see a live table like:

   ```
   ---------------------------------------------------------------------------------------------------------
   | Shot       | Ball (mph) | Club (mph) | Smash      | Launch (°) | Axis (°)   | Side (°)   | Spin (rpm) |
   ---------------------------------------------------------------------------------------------------------
   | 1          | 104.5      | 69.6       | 1.50       | 15.3       | 33.6       | 5.1        | 2238       |
   ```

   Rows tagged `(no ball data -- not forwarded)` mean SkyTrak couldn't read the ball for that
   swing (e.g. it was moved out of the hitting area mid-detection) -- shown for visibility, but
   not valid shots.

6. Ctrl+C to stop. The bridge detaches from SkyTrak cleanly and prints a closing border.

## Forwarding to automat.golf

Each valid shot (see the "no ball data" note above) is sent as GSPro Open Connect JSON over a
fresh TCP connection to `127.0.0.1:921`, where `automat-lm-bridge` (run separately -- get it from
automat.golf) picks it up and forwards it on to the automat.golf web app -- the same local
port/protocol GSPro itself listens on. `run_hook.py` prints the bridge's response (or a clear
error if the connection fails) right after each row.

`automat-lm-bridge` also serves a status page at `http://127.0.0.1:9091/` -- useful for confirming
it's up and seeing connection/shot status without needing automat.golf itself open. If shots
print in the table but never show up there, check that `automat-lm-bridge` is actually running.

### Testing without a launch monitor

`send_test_shot.py` sends synthetic shots to the same port/protocol without needing SkyTrak or
Frida involved at all -- useful for testing the network path (or automat.golf/GSPro's receiving
end) in isolation:

```bash
./venv/bin/python3 send_test_shot.py
```

It prompts for a target yardage and generates plausible (not physically simulated) ball/club
speed, launch angle, spin, etc. for that distance, tagged with a distinct `DeviceID`
(`SkyTrakGSProConnectBridge-TestShotSender`) so it's identifiable as test data. Repeats until you
give it blank input or Ctrl+C.

Note: individual values (speed, launch, spin, side angle) stay realistic, but the requested
yardage isn't currently a reliable predictor of `automat-lm-bridge`'s own computed carry distance
-- it's only tuned for stat-level realism, not hitting an exact distance.

To test end to end:

1. Start the bridge: `./automat-lm-bridge-v1.9.9b-macos-arm64` (no `sudo` needed).
2. Open `http://127.0.0.1:9091/` to confirm it's listening.
3. In another terminal, run `send_test_shot.py` and enter a yardage.
4. Confirm receipt via the script's own `-> bridge response: ...` line, the bridge's terminal log
   (`[shot:...] ... → carry ...`), and/or automat.golf itself if it's open.

## Updating SkyTrak

If SkyTrak auto-updates itself, just run `run_hook.py` as normal -- it detects the version change
and walks you through refreshing `SkyTrakInstrumented.app`, as described above.

## Troubleshooting

- **`attach()` hangs for 8+ seconds with a `[warn]` message**: a previous run didn't detach
  cleanly (e.g. was force-killed) and left a broken Frida agent inside SkyTrak's process. Quit
  `SkyTrakInstrumented.app` and relaunch it, then try again.
- **Never run two copies of `run_hook.py` against the same SkyTrak process at once** -- two
  concurrent Frida sessions hooking the same process has crashed SkyTrak outright during
  development. Ctrl+C one before starting another.
- Always let the script exit via Ctrl+C rather than force-killing it (`kill -9`), so it can
  detach cleanly and avoid the two issues above.
- **Use Chrome, not Safari, for automat.golf.** Safari blocks the `EventSource` connection
  automat.golf's page makes to `automat-lm-bridge`'s local status endpoint
  (`http://127.0.0.1:9091/events`) with a console error like `EventSource cannot load ... due to
  access control checks`. Neither of Safari's Develop-menu "Disable Cross-Origin Restrictions" or
  "Disable Local File Restrictions" fixes it (this isn't classic CORS -- more likely Safari's
  stricter enforcement of local-network-access from public HTTPS pages). Confirmed with the
  automat.golf developer that switching to Chrome resolves it.

## Repo layout

- `run_hook.py` -- the bridge: attaches to SkyTrak, prints live shot data, forwards valid shots to
  `automat-lm-bridge`, handles SkyTrak-update/RVA-drift detection.
- `hook_shot_data.js` -- the Frida script with the actual hooks
  (`SecurityWrapperService.OnShotBallData`/`OnShotEnded`, `ClubHeadData.PredictClubHeadData`) and
  their hardcoded RVAs.
- `gspro_connect.py` -- builds GSPro Open Connect JSON and sends it over TCP; shared by
  `run_hook.py` and `send_test_shot.py`.
- `send_test_shot.py` -- sends synthetic shots to test the network path without a launch monitor.
- `reverse-eng/` -- one-time reverse-engineering tooling and generated artifacts (gitignored
  except `entitlements.plist`): `Il2CppDumper/`, `dump/` (decompiled-ish output used to find the
  RVAs above), `SkyTrakInstrumented.app` (the re-signed working copy).

## Disclosure
All code in this repo is vibe-coded using Claude Code.
