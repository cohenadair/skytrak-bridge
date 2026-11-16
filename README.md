# SkyTrak GSPro Connect Bridge

On Apple Silicon devices, reads live shot data directly out of SkyTrak's own running app (via [Frida](https://frida.re/)
instrumentation of its Unity/IL2CPP internals). Data is available the instant it's computed by the 
Skytrak app.

Speaks the [GSPro Open Connect](https://gsprogolf.com/GSProConnectV1.html) protocol, sending shots
to **[Muni Golf](https://munigolfsim.com)** -- a native Mac golf sim that listens for Open Connect
launch monitors on the port it shows in-game (9210 unless you change it). The bridge keeps one
connection open with periodic heartbeats, like real launch monitor software does, and reconnects
automatically if the game is closed/reopened.

**Status:** live shot capture works and prints to a table in your terminal. Forwarding to Muni
Golf is implemented to the Open Connect spec but not yet verified against the game itself.

## TL;DR

1. Open Muni Golf and note the Open Connect port it shows (9210 by default).
2. Double-click **`run.command`** (or run `./run.command` in a terminal). It installs anything it
   needs (Homebrew, Python, the venv, `frida-tools`), launches `SkyTrakInstrumented.app` if it's
   not already running, and starts the bridge -- on a first run it'll also offer to set up
   `SkyTrakInstrumented.app` itself, answer `y`. See one-time setup below if anything about that
   needs more explanation. If Muni uses a port other than 9210, run `./run.command --port <port>`.
   Using the SkyTrak app on its own, without Muni? Run `./run.command --software skytrak` instead
   (see "Using SkyTrak's app on its own" below).
3. Connect your SkyTrak LM to the app as usual.
4. Hit balls. Shots print live in your terminal and forward automatically to Muni Golf.
5. Press Ctrl+C in the terminal window to stop the bridge.

## Requirements

- macOS, Apple Silicon
- OG SkyTrak launch monitor (Skytrak+ not tested, but may work) and the SkyTrak app installed in `/Applications`
- Everything else (Homebrew, Xcode command line tools, Python 3.12) is installed automatically by `run.command` if it isn't already present.

## One-time setup (contributing)

```bash
git clone git@github.com:cohenadair/skytrak-gspro-connect-bridge.git
cd skytrak-gspro-connect-bridge
./run.command
```

`run.command` checks for Homebrew, Xcode command line tools, Python 3.12, and a working
`frida-tools` install, installing whichever of those are missing (skipping any that are already
there -- safe to run repeatedly, and it's also just how you start the bridge day to day). It then
runs the bridge itself, which on a first run detects that `SkyTrakInstrumented.app` (the re-signed
working copy Frida actually attaches to) doesn't exist yet and offers to create it automatically --
answer `y`. This copies `/Applications/SkyTrak.app` into `reverse-eng/SkyTrakInstrumented.app`,
strips its quarantine flag, and re-signs it with the entitlements Frida needs to attach
(`reverse-eng/entitlements.plist`). The original app, and Apple's signature on it, are left
completely untouched. This step requires SkyTrak already installed normally in `/Applications` and
run once so it's paired with your launch monitor as usual.

If macOS prompts for Screen Recording or other permissions, grant them to
`SkyTrakInstrumented.app` the same way you would for the real app.

If Xcode command line tools weren't already installed, `run.command` launches Apple's own
installer and stops -- complete that, then run `run.command` again.

### Optional: RVA-drift verification

`run.command` doesn't build [Il2CppDumper](https://github.com/Perfare/Il2CppDumper), which is only
used to verify SkyTrak's internal function addresses haven't shifted after an update (see Updating
SkyTrak below) -- skipping it just means that specific check is skipped too; everything else works
the same. To enable it, install the [.NET SDK](https://dotnet.microsoft.com/) and build it by hand:

```bash
git clone --depth 1 https://github.com/Perfare/Il2CppDumper.git reverse-eng/Il2CppDumper
dotnet publish reverse-eng/Il2CppDumper/Il2CppDumper/Il2CppDumper.csproj \
  -c Release -f net8.0 -r osx-arm64 --self-contained true \
  -o reverse-eng/Il2CppDumper/publish
```

## Day-to-day use

1. Double-click `run.command`, or run `./run.command` in a terminal from the repo root. It
   launches `SkyTrakInstrumented.app` for you if it's not already running -- **not** the app in
   `/Applications`; Frida can't attach to that one, since it's missing the debug entitlement
   `SkyTrakInstrumented.app` was re-signed with (Hardened Runtime blocks `task_for_pid()` without
   it). Don't run the real app at the same time -- they'll fight over the connection to your
   SkyTrak unit.
2. Connect it to your SkyTrak unit as usual.
3. On startup, the script checks whether `/Applications/SkyTrak.app` has been updated since
   `SkyTrakInstrumented.app` was last refreshed. If so, it'll ask:

   ```
   SkyTrak.app has been updated (5.8.27.11.10.25.10 -> 6.2.111.10.7.26.11). Update SkyTrakInstrumented.app to match? [y/N]
   ```

   - **Yes**: re-copies and re-signs `SkyTrakInstrumented.app`, then (if Il2CppDumper is set up --
     see "Optional: RVA-drift verification" above) re-runs it and checks whether the hardcoded
     function addresses (RVAs) and `ClubHeadData` field offsets in `hook_shot_data.js` still
     match. If any have moved, it prints exactly which ones changed -- that means
     `hook_shot_data.js` needs manual updating before the bridge can be trusted on the new
     version. (The shot-struct offsets inside `STSWShotParamsType` aren't auto-checked; compare
     them against `reverse-eng/dump/dump.cs` by hand after a major update.)
   - **No**: keeps using the existing `SkyTrakInstrumented.app` as-is.

   It also always logs a warning if `SkyTrakInstrumented.app`'s version doesn't match
   `KNOWN_GOOD_SKYTRAK_VERSION` in `run_hook.py` -- that constant records the last version this
   bridge's hooks were actually verified against. Update it by hand once you've confirmed a newer
   version still works correctly.

4. Hit some shots. You'll see a live table like:

   ```
   ---------------------------------------------------------------------------------------------------------
   | Shot       | Ball (mph) | Club (mph) | Smash      | Launch (°) | Axis (°)   | Side (°)   | Spin (rpm) |
   ---------------------------------------------------------------------------------------------------------
   | 1          | 104.5      | 69.6       | 1.50       | 15.3       | 33.6       | 5.1        | 2238       |
   ```

   Rows tagged `(no ball data -- not forwarded)` mean SkyTrak couldn't read the ball for that
   swing (e.g. it was moved out of the hitting area mid-detection) -- shown for visibility, but
   not valid shots.

5. Ctrl+C to stop. The bridge detaches from SkyTrak cleanly and prints a closing border.

## Using SkyTrak's app on its own

By default the bridge assumes you're playing Muni Golf (`--software muni`). If you're only using
SkyTrak's own app, run:

```bash
./run.command --software skytrak
```

Shots still print in the table, and the SkyTrak-side putting fixes still apply (the side-angle
clamp and re-sending putting mode after a reconnect). Nothing is sent to Muni, though: the bridge
doesn't try to connect to it, prints no Muni warnings, and doesn't switch SkyTrak's putting mode
to follow Muni's club. Bad reads show as `(no ball data)`, without the "not forwarded" note.
`--host` and `--port` are ignored in this mode.

## Forwarding to Muni Golf

Each valid shot is sent as GSPro Open Connect JSON to Muni Golf at `127.0.0.1:9210`
over a single long-lived connection, with a heartbeat (`IsHeartBeat: true`,
`LaunchMonitorIsReady: true`) every 5 seconds so the game sees the launch monitor as connected and
ready. If the game isn't running yet, the bridge prints a warning once and keeps retrying in the
background; shots hit while disconnected print `[error] failed to forward ...` and are dropped.
`--host`/`--port` override the address (e.g. Muni running on another Mac on your network).

Set `DEBUG_ENABLED = True` in `run_hook.py` to print everything Muni sends back.

### Putting side-angle cap

SkyTrak's side angle (HLA) on putts is unreliable, so on putts it's forced to 0° (dead straight)
in two places:

- **Muni Golf**: when Muni reports the putter is out (its Code 201 "Player Information" messages
  include `"Club":"PT"`), the side angle sent to the game is clamped to ±`PUTTING_SIDE_CAP` in
  `gspro_connect.py` (currently 0). The shot table still shows SkyTrak's own number, with a
  `(putt: side sent as 0.0)` note on rows where the cap changed it.
- **SkyTrak itself**: when the SkyTrak app is in putting mode, `hook_shot_data.js` clamps the
  side angle to ±`PUTTING_SIDE_CAP` (also 0, set separately in that file) inside SkyTrak's own
  shot data, before SkyTrak reads it, so its display and ball flight are straight too. This only
  happens while the bridge is attached. Rows where it changed the value get a
  `(putt: SkyTrak side set to 0.0)` note (combined with the Muni note when both apply, e.g.
  `(putt: side sent as 0.0; SkyTrak side set to 0.0)`).

The bridge also keeps SkyTrak's shot mode in step with Muni: when Muni reports the putter is out,
it switches SkyTrak to putting mode (the same switch SkyTrak's own UI makes, which also puts the
unit itself into putting mode), and back to normal mode when you pick another club. You'll see an
`[info] Switched from normal to putting` line when it does. It only acts when Muni's club changes,
so you can still change SkyTrak's mode by hand in between. Turn it off with
`AUTO_SWITCH_SKYTRAK_PUTTING_MODE` in `run_hook.py`.

SkyTrak sometimes drops its connection to the unit mid-session and reconnects on its own. The unit
comes back without putting mode, but SkyTrak still thinks it's in putting mode, so it never
re-sends it and putts stop registering. To fix this, the bridge waits 3 seconds after every
reconnect and then switches SkyTrak to the other mode and back again. That's the same as picking
a non-putter club in Muni and then the putter. You'll see `[info] SkyTrak reconnected to the unit
-- re-sending putting mode`, followed by `Switched from putting to normal` and
`Switched from normal to putting`. Turn it off with
`REAPPLY_SHOT_MODE_ON_RECONNECT` in `hook_shot_data.js`.

`send_test_shot.py` follows the same rule: while the putter is out it asks for a putt distance in
feet, sends a putt-speed shot with a deliberately sloppy side angle, and shows when it was capped.

## Testing without a launch monitor

`send_test_shot.py` sends synthetic shots to Muni Golf without needing SkyTrak or Frida involved
at all -- useful for testing the network path (or Muni's receiving end) in isolation:

```bash
./venv/bin/python3 send_test_shot.py              # Muni Golf on 9210
./venv/bin/python3 send_test_shot.py --port 9211  # if Muni shows a different port
```

It prompts for a target yardage and generates plausible (not physically simulated) ball/club
speed, launch angle, spin, etc. for that distance, tagged with a distinct `DeviceID`
(`SkyTrakGSProConnectBridge-TestShotSender`) so it's identifiable as test data. Repeats until you
give it blank input or Ctrl+C.

Note: individual values (speed, launch, spin, side angle) stay realistic, but the requested
yardage isn't a reliable predictor of the game's own computed carry distance -- it's only tuned
for stat-level realism, not hitting an exact distance.

To test end to end:

1. Open Muni Golf and note the Open Connect port it shows.
2. Run `send_test_shot.py` (with `--port` if it isn't 9210) and enter a yardage.
3. Confirm the shot shows up in Muni. Since the script runs with Muni's replies logged, its
   `[info] receiver: ...` lines also show what Muni sent back.

## Updating SkyTrak

If SkyTrak auto-updates itself, just run `run.command` as normal -- it detects the version change
and walks you through refreshing `SkyTrakInstrumented.app`, as described above.

## Troubleshooting

- **SkyTrak stops reading shots (often right after a `(no ball data -- not forwarded)` row), but
  it's still connected**: if it happened after SkyTrak reconnected to the unit, the unit has
  probably lost its shot mode. The bridge now fixes this automatically (see "Putting side-angle
  cap" above). If it still happens, pick a different club in Muni and then switch back (or toggle
  SkyTrak's own putting mode off and on). That makes SkyTrak send the mode to the unit again, so
  you don't need to restart the app. SkyTrak's own log, `~/Library/Logs/SkyTrak/SKYTRAK/Player.log`,
  shows reconnects as `RIPE_EVT_BOX_CONNECTED`.
- **`attach()` hangs for 8+ seconds with a `[warn]` message**: a previous run didn't detach
  cleanly (e.g. was force-killed) and left a broken Frida agent inside SkyTrak's process. Quit
  `SkyTrakInstrumented.app` and relaunch it, then try again.
- **Never run two copies of `run_hook.py` against the same SkyTrak process at once** -- two
  concurrent Frida sessions hooking the same process has crashed SkyTrak outright during
  development. Ctrl+C one before starting another.
- Always let the script exit via Ctrl+C rather than force-killing it (`kill -9`), so it can
  detach cleanly and avoid the two issues above.

## Repo layout

- `run.command` -- installs prerequisites (if missing), launches `SkyTrakInstrumented.app` (if not
  already running), and starts the bridge; the normal way to run this day to day.
- `run_hook.py` -- the bridge: attaches to SkyTrak, prints live shot data, forwards valid shots to
  Muni Golf, handles SkyTrak-update/RVA-drift detection.
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
