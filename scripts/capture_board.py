"""Screenshot the live React board for each ticket (output/ticket_101.png ... 103.png).

Start the backend (port 8000) and `npm run dev` (port 5173) first. This opens
http://localhost:5173/?ticket=<id> in headless Edge or Chrome and saves what the
board shows. It only reads from the running app; it changes nothing.

    python scripts/capture_board.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser() -> str:
    for candidate in BROWSERS:
        if Path(candidate).is_file():
            return candidate
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("Install Edge or Chrome to capture the board.")


def main() -> None:
    browser = find_browser()
    for ticket_id in (101, 102, 103):
        target = ROOT / "output" / f"ticket_{ticket_id}.png"
        with tempfile.TemporaryDirectory() as profile:
            subprocess.run(
                [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                 "--window-size=1600,1300", "--virtual-time-budget=10000", f"--screenshot={target}",
                 f"http://localhost:5173/?ticket={ticket_id}"],
                check=True, timeout=90, capture_output=True,
            )
        print(f"saved {target.relative_to(ROOT)} ({target.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
