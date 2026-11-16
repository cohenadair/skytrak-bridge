// Hooks SecurityWrapperService.OnShotBallData / OnShotEnded in the SkyTrak
// Unity/IL2CPP process. These fire the instant the launch monitor's own
// firmware reports computed ball data -- before Unity's flight animation.
//
// As of SkyTrak 6.x the argument is STSWFullShotParamsType, a subclass of
// STSWShotParamsType that only appends fields (moreFssShotParams @ 0x8C), so
// the base-class offsets below are unchanged.
//
// STSWShotParamsType is an IL2CPP reference type (class), so the "shotParams"
// argument is a plain object pointer. IL2CPP object layout is a 16-byte
// header (klass ptr + monitor) followed by fields in declared order, which
// matches the field offsets Il2CppDumper printed (first field at 0x10).
//
// STSWSpeedParamsType @ shotParams+0x10:
//   0x00 totalSpeed, 0x08 launchAngle, 0x10 horizontalAngle, 0x1C clubSpeed
// STSWSpinParamsType @ shotParams+0x34:
//   0x00 totalSpin, 0x04 backSpin, 0x08 sideSpin, 0x0C spinAxis

const RVA_ON_SHOT_BALL_DATA = 0x166A764;
const RVA_ON_SHOT_ENDED = 0x166AA8C;

// ClubHeadData.PredictClubHeadData(TFlightData flightData, string clubType,
// bool isPracticeMode) -- this unit has no real club-head sensor, so
// SkyTrak's own displayed club speed/smash factor is *estimated* here from
// the ball flight data + selected club, not read from the STSW hardware
// struct (which is always 0 for clubSpeed on this box). ClubHeadData is a
// singleton; the computed fields land on `this` after the call returns.
// In 6.x it's reached via ClubHeadDataCalculate.PredictClubHeadData(flightData,
// isPracticeMode), which resolves the club type and calls this one.
const RVA_PREDICT_CLUB_HEAD_DATA = 0x12CD9F4;
const CLUB_HEAD_DATA_CLUB_SPEED_OFFSET = 0x34;
const CLUB_HEAD_DATA_SMASH_FACTOR_OFFSET = 0x38;

// SecurityWrapperService.get_IsModePutting() -- true while the box is in
// putting mode (a pure getter: returns this+0x130 == 2). While it's true,
// the side angle (horizontalAngle) is clamped to +/- PUTTING_SIDE_CAP in the
// shot struct itself *before* SkyTrak's own code reads it, so SkyTrak's
// display and ball flight use the clamped value too. Only applies while the
// bridge is attached. The un-clamped value is still sent to the bridge.
const RVA_GET_IS_MODE_PUTTING = 0x1662DE8;
const PUTTING_SIDE_CAP = 0.0;
const HORIZONTAL_ANGLE_OFFSET = 0x10 + 0x10; // speedParams + horizontalAngle

// Auto-switching SkyTrak's shot mode (putting/normal) to follow the game's
// selected club -- see setPuttingMode() below. SkyTrak's own
// ISkyTrakSW.SetShotModePutting()/SetShotModeNormal() set
// _targetShotModeType and start the async SetShotModeBox() task, which
// switches the unit and fires SkyTrak's own UI change events -- the same path
// its UI uses. They must run on Unity's main thread, so they're called from a
// temporary hook on UniTask's PlayerLoopRunner.Run(), which the main thread
// calls every frame.
const RVA_GET_SKYTRAK_SW_INSTANCE = 0x1662E18;   // static SecurityWrapperService.get_Instance()
const RVA_SET_SHOT_MODE_PUTTING = 0x166E418;     // SecurityWrapperService.ISkyTrakSW.SetShotModePutting()
const RVA_SET_SHOT_MODE_NORMAL = 0x166E57C;      // SecurityWrapperService.ISkyTrakSW.SetShotModeNormal()
const RVA_PLAYER_LOOP_RUNNER_RUN = 0x901DCC8;    // Cysharp...PlayerLoopRunner.Run()
const SECURITY_WRAPPER_CURRENT_SHOT_MODE_OFFSET = 0x130;
const SECURITY_WRAPPER_TARGET_SHOT_MODE_OFFSET = 0x134;
const SHOT_MODE_NORMAL = 1;
const SHOT_MODE_PUTTING = 2;
// How long to wait for a requested switch to finish (SkyTrak sets
// _currentShotModeType to match _targetShotModeType once the unit confirms)
// before moving on to the next queued switch anyway.
const SHOT_MODE_SETTLE_TIMEOUT_MS = 10000;

// SecurityWrapperService.OnConnected() -- fires whenever SkyTrak (re)connects
// to the unit, including when it drops and reconnects mid-session on its own.
// After such a reconnect the unit comes back without its putting mode, but
// SkyTrak still believes it's in putting mode, so it never re-sends it and
// putts don't trigger. Asking it to switch to putting does nothing either
// ("already set"), so the fix is a round trip: switch to the other mode,
// wait for it to finish, then switch back. Delayed a few seconds so SkyTrak
// finishes its own post-connect setup (handedness, arming, MMS) first.
const RVA_ON_CONNECTED = 0x1667728;
const REAPPLY_SHOT_MODE_ON_RECONNECT = true;
const RECONNECT_REAPPLY_DELAY_MS = 3000;

function readIl2CppString(strPtr) {
    if (strPtr.isNull()) return null;
    const length = strPtr.add(0x10).readInt();
    return strPtr.add(0x14).readUtf16String(length);
}

const mod = Process.findModuleByName('GameAssembly.dylib');
if (!mod) {
    send('ERROR: GameAssembly.dylib not found');
} else {
    send('GameAssembly.dylib base=' + mod.base);

    // IL2CPP instance methods take (this, MethodInfo*); a pure getter
    // never touches the MethodInfo, so null is fine.
    const getIsModePutting = new NativeFunction(
        mod.base.add(RVA_GET_IS_MODE_PUTTING), 'bool', ['pointer', 'pointer']);

    // Clamps the side angle in place when the box is in putting mode.
    // Returns the side angle SkyTrak will now see, or null if not putting.
    // (+ 0.0 turns a -0 result into 0.)
    function clampPuttingSideAngle(service, objPtr) {
        if (!getIsModePutting(service, NULL)) return null;
        const hlaPtr = objPtr.add(HORIZONTAL_ANGLE_OFFSET);
        const clamped = Math.max(-PUTTING_SIDE_CAP, Math.min(PUTTING_SIDE_CAP, hlaPtr.readFloat())) + 0.0;
        hlaPtr.writeFloat(clamped);
        return clamped;
    }

    const getSkyTrakSwInstance = new NativeFunction(
        mod.base.add(RVA_GET_SKYTRAK_SW_INSTANCE), 'pointer', ['pointer']);
    const setShotModePutting = new NativeFunction(
        mod.base.add(RVA_SET_SHOT_MODE_PUTTING), 'void', ['pointer', 'pointer']);
    const setShotModeNormal = new NativeFunction(
        mod.base.add(RVA_SET_SHOT_MODE_NORMAL), 'void', ['pointer', 'pointer']);

    // get_Instance() returns an ISkyTrakSW; only call SecurityWrapperService
    // methods on it if that's what it really is.
    const il2cppObjectGetClass = mod.findExportByName('il2cpp_object_get_class');
    const il2cppClassGetName = mod.findExportByName('il2cpp_class_get_name');
    const objectGetClass = il2cppObjectGetClass &&
        new NativeFunction(il2cppObjectGetClass, 'pointer', ['pointer']);
    const classGetName = il2cppClassGetName &&
        new NativeFunction(il2cppClassGetName, 'pointer', ['pointer']);

    function className(obj) {
        if (!objectGetClass || !classGetName) return null;
        return classGetName(objectGetClass(obj)).readUtf8String();
    }

    // Shot-mode switches waiting to run on the main thread, in order. Each
    // entry is SHOT_MODE_NORMAL, SHOT_MODE_PUTTING, or REAPPLY (re-send
    // whatever mode SkyTrak currently believes it's in -- see
    // RVA_ON_CONNECTED above).
    const REAPPLY = 'reapply';
    let shotModeQueue = [];
    // The switch last started, while it's still finishing: { mode, deadline }.
    let settlingShotMode = null;
    let mainThreadListener = null;

    function modeName(mode) {
        switch (mode) {
            case SHOT_MODE_NORMAL: return 'normal';
            case SHOT_MODE_PUTTING: return 'putting';
            case 3: return 'dry swing';
            default: return 'no mode';
        }
    }

    // The SkyTrak device service, or null (after reporting why) if it isn't
    // usable right now.
    function getShotModeService(name) {
        const service = getSkyTrakSwInstance(NULL);
        if (service.isNull()) {
            send({ label: 'ShotMode', mode: name, result: 'skipped: SkyTrak device service not ready' });
            return null;
        }
        const cls = className(service);
        if (cls !== null && cls !== 'SecurityWrapperService') {
            send({ label: 'ShotMode', mode: name, result: 'skipped: unexpected device service ' + cls });
            return null;
        }
        return service;
    }

    function readShotModes(service) {
        return {
            current: service.add(SECURITY_WRAPPER_CURRENT_SHOT_MODE_OFFSET).readS32(),
            target: service.add(SECURITY_WRAPPER_TARGET_SHOT_MODE_OFFSET).readS32(),
        };
    }

    // Runs on Unity's main thread. Switches only if SkyTrak isn't already
    // in (or switching to) the requested mode. Returns true if a switch was
    // started.
    function applyShotMode(mode) {
        const name = modeName(mode);
        const service = getShotModeService(name);
        if (service === null) return false;
        const { current, target } = readShotModes(service);
        if (current === mode && target === mode) {
            send({ label: 'ShotMode', mode: name, result: 'already set' });
            return false;
        }
        (mode === SHOT_MODE_PUTTING ? setShotModePutting : setShotModeNormal)(service, NULL);
        send({ label: 'ShotMode', mode: name, result: 'switched', from: modeName(current) });
        return true;
    }

    // Runs on Unity's main thread. Turns a REAPPLY entry into a round trip
    // through the other mode and back to the one SkyTrak believes it's in.
    function expandReapply() {
        const service = getShotModeService('current');
        if (service === null) return;
        const believed = readShotModes(service).target;
        if (believed !== SHOT_MODE_NORMAL && believed !== SHOT_MODE_PUTTING) return;
        const other = believed === SHOT_MODE_PUTTING ? SHOT_MODE_NORMAL : SHOT_MODE_PUTTING;
        send({ label: 'Reconnected', mode: modeName(believed) });
        shotModeQueue.unshift(other, believed);
    }

    // Called every frame on Unity's main thread while shot-mode work is
    // pending. Waits for the last switch to finish before starting the next.
    function onMainThreadFrame() {
        if (settlingShotMode !== null) {
            const service = getSkyTrakSwInstance(NULL);
            const { current, target } = service.isNull() ? {} : readShotModes(service);
            if (current === settlingShotMode.mode && target === settlingShotMode.mode) {
                settlingShotMode = null;
            } else if (Date.now() > settlingShotMode.deadline) {
                send({ label: 'ShotMode', mode: modeName(settlingShotMode.mode),
                       result: 'still not confirmed by the unit after ' +
                               (SHOT_MODE_SETTLE_TIMEOUT_MS / 1000) + 's, moving on' });
                settlingShotMode = null;
            } else {
                return;
            }
        }
        if (shotModeQueue.length === 0) {
            stopMainThreadHook();
            return;
        }
        const next = shotModeQueue.shift();
        if (next === REAPPLY) {
            expandReapply();
        } else if (applyShotMode(next)) {
            settlingShotMode = { mode: next, deadline: Date.now() + SHOT_MODE_SETTLE_TIMEOUT_MS };
        }
    }

    // The main-thread hook is only installed while shot-mode work is
    // pending, so there's no per-frame cost the rest of the time.
    function startMainThreadHook() {
        if (mainThreadListener !== null) return;
        mainThreadListener = Interceptor.attach(mod.base.add(RVA_PLAYER_LOOP_RUNNER_RUN), {
            onEnter() {
                try {
                    onMainThreadFrame();
                } catch (e) {
                    send('ERROR switching SkyTrak shot mode: ' + e);
                    shotModeQueue = [];
                    settlingShotMode = null;
                    stopMainThreadHook();
                }
            }
        });
    }

    // Detaching from inside the listener's own callback isn't safe, so defer
    // it -- and skip it if new work arrived in the meantime.
    function stopMainThreadHook() {
        setTimeout(() => {
            if (mainThreadListener === null || shotModeQueue.length > 0 || settlingShotMode !== null) return;
            mainThreadListener.detach();
            mainThreadListener = null;
        }, 0);
    }

    // Called by the bridge (script.exports_sync.set_putting_mode) when the
    // game's selected club changes to or from the putter. Replaces any
    // switches still waiting, since only the latest club matters.
    rpc.exports = {
        setPuttingMode(putting) {
            shotModeQueue = [putting ? SHOT_MODE_PUTTING : SHOT_MODE_NORMAL];
            startMainThreadHook();
        },
    };

    if (REAPPLY_SHOT_MODE_ON_RECONNECT) {
        Interceptor.attach(mod.base.add(RVA_ON_CONNECTED), {
            onLeave() {
                setTimeout(() => {
                    shotModeQueue.push(REAPPLY);
                    startMainThreadHook();
                }, RECONNECT_REAPPLY_DELAY_MS);
            }
        });
    }

    function readShotParams(objPtr, label) {
        const speed = objPtr.add(0x10);
        const spin = objPtr.add(0x34);
        const data = {
            label: label,
            totalSpeed: speed.add(0x00).readFloat(),
            launchAngle: speed.add(0x08).readFloat(),
            horizontalAngle: speed.add(0x10).readFloat(),
            clubSpeed: speed.add(0x1C).readFloat(),
            totalSpin: spin.add(0x00).readFloat(),
            backSpin: spin.add(0x04).readFloat(),
            sideSpin: spin.add(0x08).readFloat(),
            spinAxis: spin.add(0x0C).readFloat(),
        };
        return data;
    }

    Interceptor.attach(mod.base.add(RVA_ON_SHOT_BALL_DATA), {
        onEnter(args) {
            // args[0] = this, args[1] = shotParams object pointer
            let data;
            try {
                // Read before clamping, so the bridge gets the raw value.
                data = readShotParams(args[1], 'OnShotBallData');
            } catch (e) {
                send('ERROR in OnShotBallData hook: ' + e);
                return;
            }
            try {
                // null when SkyTrak isn't in putting mode, otherwise the side
                // angle SkyTrak itself now uses.
                data.skytrakPuttingSideAngle = clampPuttingSideAngle(args[0], args[1]);
            } catch (e) {
                send('ERROR clamping putting side angle: ' + e);
            }
            send(data);
        }
    });

    Interceptor.attach(mod.base.add(RVA_ON_SHOT_ENDED), {
        onEnter(args) {
            try {
                // Clamp again in case OnShotEnded gets a fresh copy of the
                // data rather than the struct already clamped above.
                clampPuttingSideAngle(args[0], args[1]);
                send(readShotParams(args[1], 'OnShotEnded'));
            } catch (e) {
                send('ERROR in OnShotEnded hook: ' + e);
            }
        }
    });

    Interceptor.attach(mod.base.add(RVA_PREDICT_CLUB_HEAD_DATA), {
        onEnter(args) {
            this.instance = args[0];
            try {
                this.clubType = readIl2CppString(args[2]);
            } catch (e) {
                this.clubType = null;
            }
        },
        onLeave(retval) {
            try {
                send({
                    label: 'ClubHeadData',
                    clubType: this.clubType,
                    clubSpeed: this.instance.add(CLUB_HEAD_DATA_CLUB_SPEED_OFFSET).readFloat(),
                    smashFactor: this.instance.add(CLUB_HEAD_DATA_SMASH_FACTOR_OFFSET).readFloat(),
                });
            } catch (e) {
                send('ERROR in ClubHeadData hook: ' + e);
            }
        }
    });

    send('Hooks installed on OnShotBallData (+' + RVA_ON_SHOT_BALL_DATA.toString(16) +
        '), OnShotEnded (+' + RVA_ON_SHOT_ENDED.toString(16) +
        '), ClubHeadData.PredictClubHeadData (+' + RVA_PREDICT_CLUB_HEAD_DATA.toString(16) + ')' +
        (REAPPLY_SHOT_MODE_ON_RECONNECT ? ', and OnConnected (+' + RVA_ON_CONNECTED.toString(16) + ')' : ''));
}
