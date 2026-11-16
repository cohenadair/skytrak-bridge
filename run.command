#!/bin/bash
# Double-click this file in Finder (or run `./run.command` in a terminal) to
# set up everything needed (if it isn't already) and start the bridge.
#
# Safe to run repeatedly -- each step checks whether it's already satisfied
# and skips itself if so, so this is also just "the" way to start the
# bridge day to day.

set -e
cd "$(dirname "$0")"

echo "=== SkyTrak GSPro Connect Bridge ==="
echo ""

# --- Homebrew ---
if ! command -v brew >/dev/null 2>&1; then
    echo "[setup] Homebrew not found -- installing (you may be asked for your password)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
else
    echo "[setup] Homebrew already installed, skipping."
fi

# --- Xcode Command Line Tools (codesign, lipo, xattr) ---
if ! xcode-select -p >/dev/null 2>&1; then
    echo "[setup] Xcode Command Line Tools not found -- launching Apple's installer."
    echo "        Complete that installer, then run this script again."
    xcode-select --install
    exit 1
else
    echo "[setup] Xcode Command Line Tools already installed, skipping."
fi

# --- Python 3.12 (system python3 is 3.9, too old for frida-tools) ---
PYTHON312="/opt/homebrew/bin/python3.12"
if [ ! -x "$PYTHON312" ]; then
    echo "[setup] Python 3.12 not found -- installing via Homebrew..."
    brew install python@3.12
else
    echo "[setup] Python 3.12 already installed, skipping."
fi

# --- Virtualenv + frida-tools ---
if [ -x "./venv/bin/python3" ] && ./venv/bin/python3 -c "import frida" >/dev/null 2>&1; then
    echo "[setup] Virtual environment + frida-tools already set up, skipping."
else
    echo "[setup] Setting up Python virtual environment..."
    rm -rf venv
    "$PYTHON312" -m venv venv
    ./venv/bin/python3 -m pip install --upgrade pip -q
    echo "[setup] Installing frida-tools..."
    ./venv/bin/python3 -m pip install frida-tools -q
fi

# With Frida 17.16.4, SkyTrak 6.x crashes (EXC_BAD_ACCESS in
# _Unwind_RaiseException, jumping into freed Frida memory) a few seconds
# after the bridge detaches. 17.26.2 was verified not to, so upgrade any
# existing venv below that.
MIN_FRIDA="17.26.2"
if ! ./venv/bin/python3 -c "import frida,sys; v=tuple(map(int,frida.__version__.split('.')[:3])); sys.exit(v < tuple(map(int,'$MIN_FRIDA'.split('.'))))" >/dev/null 2>&1; then
    echo "[setup] Upgrading Frida to >= $MIN_FRIDA..."
    ./venv/bin/python3 -m pip install -q -U "frida>=$MIN_FRIDA" frida-tools
else
    echo "[setup] Frida is >= $MIN_FRIDA, skipping upgrade."
fi

# --- SkyTrakInstrumented.app ---
INSTRUMENTED_APP="reverse-eng/SkyTrakInstrumented.app"
if [ -d "$INSTRUMENTED_APP" ]; then
    if pgrep -x SKYTRAK >/dev/null 2>&1; then
        echo "[setup] SkyTrakInstrumented.app already running, skipping launch."
    else
        echo "[setup] Launching SkyTrakInstrumented.app..."
        open "$INSTRUMENTED_APP"
        for i in $(seq 1 30); do
            if pgrep -x SKYTRAK >/dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        if ! pgrep -x SKYTRAK >/dev/null 2>&1; then
            echo "[warn] SkyTrakInstrumented.app didn't start within 30s -- continuing anyway."
        fi
    fi
else
    echo "[setup] SkyTrakInstrumented.app doesn't exist yet -- the next step will offer to create it."
    echo "        Once it's created, run this script again to launch it and start the bridge."
fi

echo ""
echo "=== Starting bridge ==="
echo ""
exec ./venv/bin/python3 run_hook.py "$@"
