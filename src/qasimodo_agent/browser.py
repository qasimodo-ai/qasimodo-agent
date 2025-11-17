from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path


def find_bundled_chromium() -> str | None:
    if getattr(sys, "frozen", False):
        bundle_dir = Path(sys._MEIPASS)
        playwright_dir = bundle_dir / "ms-playwright"
        if playwright_dir.exists():
            chromium_dirs = list(playwright_dir.glob("chromium-*"))
            if chromium_dirs:
                chromium_dir = chromium_dirs[0]
                if platform.system() == "Windows":
                    executable = chromium_dir / "chrome-win" / "chrome.exe"
                elif platform.system() == "Darwin":
                    executable = chromium_dir / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
                else:
                    executable = chromium_dir / "chrome-linux" / "chrome"
                if executable.exists():
                    return str(executable)
    return None


def ensure_chromium_installed() -> None:
    if platform.system() != "Darwin":
        return
    playwright_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    if playwright_cache.exists() and list(playwright_cache.glob("chromium-*")):
        return
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to install Chromium: {exc}") from exc


__all__ = ["find_bundled_chromium", "ensure_chromium_installed"]
