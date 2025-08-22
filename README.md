# PunchPad

PunchPad — “Clock in. Clock out. No drama.”

## What is PunchPad?
A lightweight, future-friendly time clock app. This step initializes brand/theme constants and base config only.

## Quick start (dev)

1. Create and activate a virtualenv:
   - Linux/macOS:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```
   - Windows (PowerShell):
     ```powershell
     python -m venv .venv
     .venv\\Scripts\\Activate.ps1
     ```

2. Install in editable mode and run the CLI:
   ```bash
  pip install -e .
  punchpad --help
   ```

### Installation
- Dev install: `pip install -e .`
- Usage: `punchpad --help`

### Kiosk / PIN Mode
- Set an employee PIN (via bootstrap or repo helpers).
- Run kiosk mode and enter the PIN when prompted:
  ```bash
  python -m punchpad_app kiosk pin --source $(hostname)
  ```
- Defaults:
  - `kiosk.debounce_seconds` = 30 (prevents double taps)
  - `kiosk.pin_attempt_window_seconds` = 300
  - `kiosk.pin_max_attempts_per_window` = 5
  - `kiosk.lockout_minutes` = 10
- Change defaults via the `settings` table using repo helpers.

### Reports
- Daily totals:
  ```bash
  python -m punchpad_app report daily --emp 1 --start 2025-08-01 --end 2025-08-07
  ```
- Period total:
  ```bash
  python -m punchpad_app report period --emp 1 --start 2025-08-01 --end 2025-08-15
  ```
- CSV export:
  ```bash
  python -m punchpad_app report daily --emp 1 --start 2025-08-01 --end 2025-08-07 --csv ./report.csv
  ```

### Kiosk Run Mode (Fullscreen)
- Start the fullscreen kiosk loop:
  ```bash
  python -m punchpad_app kiosk run
  ```
- What users see:
  - A large prompt: "PunchPad — Enter PIN"
  - After entering a valid PIN: big banner "PUNCHED IN" or "PUNCHED OUT" with local HH:MM
  - If debounced: banner "Duplicate punch blocked"
  - If locked out: banner "Locked"
- Admin note: adjust debounce/lockout in `settings` (e.g., `kiosk.debounce_seconds`, `kiosk.pin_max_attempts_per_window`).
- Testing shortcut (non-interactive single iteration):
  ```bash
  python -m punchpad_app kiosk run --pin 1234 --result_ms 10
  ```

### Web UI (Local Kiosk)
- Start the local-only web UI:
  ```bash
  python -m punchpad_app kiosk web
  ```
- Then open `http://127.0.0.1:8765/` in a browser. You'll see a clean PIN screen.
- Submitting a valid PIN will show a big banner ("PUNCHED IN" or "PUNCHED OUT") with local time, then auto-return to the PIN screen after 2 seconds.
- Duplicate within debounce shows a "Duplicate punch blocked" banner. Too many bad PINs shows a "Locked" banner.
- Notes:
  - Local only by default. To expose on LAN: `--host 0.0.0.0` (ensure your network is trusted before doing this).
  - Change auto-redirect seconds with `--redirect-seconds N`.
  - All logic (debounce, lockout, queue fallback) matches the CLI.

## Data directory
- Windows: `C:\\ProgramData\\PunchPad\\`
- Non-Windows (Linux/macOS): override via env `PUNCHPAD_DATA_DIR`. If not set, defaults to `~/.local/share/punchpad`.

## Theme summary
- Tagline: “Clock in. Clock out. No drama.”
- Colors:
  - primary: `#1F3A5F` (deep blue)
  - accent: `#00C2A8` (mint)
  - bg: `#F5F7FA`
  - text: `#0F172A`
  - success: `#16A34A`
  - danger: `#DC2626`
- Fonts: Prefer Segoe UI on Windows; fall back to system sans elsewhere.
