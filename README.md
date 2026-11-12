# Wire Judge Live

Wire Judge Live is a desktop companion for VTOL VR. Track aircraft on a live
map, record and replay flights, and review scored carrier landings.

## Run on Windows

If you already have `WireJudgeLive.exe`, double-click it. To build your own copy:

1. Clone this repository or download and extract the ZIP from GitHub.
2. Install Python 3.11 or newer, then double-click `build.bat` in the project root.
3. Open `WireJudgeLive.exe` next to `build.bat`. You can share this single file with other players.

## Run from source

With Python 3.11 or newer, open a terminal in the project folder and run:

```sh
python -m pip install -r requirements.txt
python app.py
```

On Windows, use `py -3` in place of `python` if needed. For live tracking, point
a compatible VTOL VR telemetry mod at `127.0.0.1:27850`.
