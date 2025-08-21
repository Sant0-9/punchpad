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

2. Run the dev stub:
   ```bash
   python -m punchpad_app
   ```

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
