// Hooks SecurityWrapperService.OnShotBallData / OnShotEnded in the SkyTrak
// Unity/IL2CPP process. These fire the instant the launch monitor's own
// firmware reports computed ball data -- before Unity's flight animation.
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

const RVA_ON_SHOT_BALL_DATA = 0xDB4E54;
const RVA_ON_SHOT_ENDED = 0xDB52F0;

// ClubHeadData.PredictClubHeadData(TFlightData flightData, string clubType,
// bool isPracticeMode) -- this unit has no real club-head sensor, so
// SkyTrak's own displayed club speed/smash factor is *estimated* here from
// the ball flight data + selected club, not read from the STSW hardware
// struct (which is always 0 for clubSpeed on this box). ClubHeadData is a
// singleton; the computed fields land on `this` after the call returns.
const RVA_PREDICT_CLUB_HEAD_DATA = 0xABCDC8;
const CLUB_HEAD_DATA_CLUB_SPEED_OFFSET = 0x50;
const CLUB_HEAD_DATA_SMASH_FACTOR_OFFSET = 0x54;

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
        send(data);
    }

    Interceptor.attach(mod.base.add(RVA_ON_SHOT_BALL_DATA), {
        onEnter(args) {
            // args[0] = this, args[1] = shotParams object pointer
            try {
                readShotParams(args[1], 'OnShotBallData');
            } catch (e) {
                send('ERROR in OnShotBallData hook: ' + e);
            }
        }
    });

    Interceptor.attach(mod.base.add(RVA_ON_SHOT_ENDED), {
        onEnter(args) {
            try {
                readShotParams(args[1], 'OnShotEnded');
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
        '), and ClubHeadData.PredictClubHeadData (+' + RVA_PREDICT_CLUB_HEAD_DATA.toString(16) + ')');
}
