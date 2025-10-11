import subprocess
import sys
import os
import platform
from pathlib import Path


def build():
    # Find playwright chromium directory
    home = Path.home()

    # Playwright cache location varies by platform
    if platform.system() == "Windows":
        playwright_browsers = home / "AppData" / "Local" / "ms-playwright"
        bundle_mode = "--onedir"  # Use onedir for Windows installer
    elif platform.system() == "Darwin":  # macOS
        playwright_browsers = home / "Library" / "Caches" / "ms-playwright"
        bundle_mode = "--onedir"  # Use onedir for .app bundle
    else:  # Linux
        playwright_browsers = home / ".cache" / "ms-playwright"
        bundle_mode = "--onedir"  # Use onedir for AppImage

    args = [
        "pyinstaller",
        "src/pyinstaller_test/__main__.py",
        "--name", "pyinstaller-test",
        bundle_mode,
        "--collect-all", "browser_use"
    ]

    # Add playwright browsers if they exist
    # Skip macOS due to unsolvable code signing issues with Chromium.app
    if playwright_browsers.exists() and platform.system() != "Darwin":
        chromium_dirs = list(playwright_browsers.glob("chromium-*"))
        if chromium_dirs:
            chromium_dir = chromium_dirs[0]

            # Use proper separator for --add-data based on platform
            separator = ";" if platform.system() == "Windows" else ":"
            args.extend([
                "--add-data", f"{chromium_dir}{separator}ms-playwright/{chromium_dir.name}"
            ])
            print(f"Including Chromium from: {chromium_dir}")
    elif platform.system() == "Darwin":
        print("Skipping Chromium bundling on macOS (users must install Playwright separately)")

    result = subprocess.run(args)
    sys.exit(result.returncode)
