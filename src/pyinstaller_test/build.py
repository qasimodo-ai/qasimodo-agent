import subprocess
import sys
import os
import platform
import shutil
import tarfile
import zipfile
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
        "--name",
        "pyinstaller-test",
        bundle_mode,
        "--collect-all",
        "browser_use",
    ]

    # Add playwright browsers if they exist
    # Skip macOS due to unsolvable code signing issues with Chromium.app
    if playwright_browsers.exists() and platform.system() != "Darwin":
        chromium_dirs = list(playwright_browsers.glob("chromium-*"))
        if chromium_dirs:
            chromium_dir = chromium_dirs[0]

            # Use proper separator for --add-data based on platform
            separator = ";" if platform.system() == "Windows" else ":"
            args.extend(
                [
                    "--add-data",
                    f"{chromium_dir}{separator}ms-playwright/{chromium_dir.name}",
                ]
            )
            print(f"Including Chromium from: {chromium_dir}")
    elif platform.system() == "Darwin":
        print(
            "Skipping Chromium bundling on macOS (users must install Playwright separately)"
        )

    result = subprocess.run(args, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)

    # Package for macOS, Linux, or Windows
    if platform.system() == "Darwin":
        create_macos_app()
    elif platform.system() == "Linux":
        create_linux_package()
    elif platform.system() == "Windows":
        create_windows_package()


def create_macos_app():
    """Create macOS .app bundle from PyInstaller output."""
    app_name = "pyinstaller-test"
    bundle_name = f"{app_name}.app"
    dist_dir = Path("dist")
    build_dir = dist_dir / app_name
    app_dir = dist_dir / bundle_name
    resources_app_dir = app_dir / "Contents" / "Resources" / "app"
    arch = platform.machine()
    zip_name = f"{app_name}-macos-{arch}.zip"

    print("Creating macOS .app bundle...")

    if not build_dir.exists():
        print(f"Error: {build_dir} does not exist. Run the PyInstaller build first.")
        sys.exit(1)

    # Reset bundle directory
    if app_dir.exists():
        shutil.rmtree(app_dir)
    (app_dir / "Contents" / "MacOS").mkdir(parents=True)
    (app_dir / "Contents" / "Resources").mkdir(parents=True)

    # Copy the PyInstaller output without altering its layout
    if resources_app_dir.exists():
        shutil.rmtree(resources_app_dir)
    resources_app_dir.mkdir(parents=True)
    # Copy contents of build_dir into resources_app_dir (like rsync -a)
    for item in build_dir.iterdir():
        dest = resources_app_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # Launcher that delegates to the copied PyInstaller build
    launcher_script = app_dir / "Contents" / "MacOS" / app_name
    launcher_script.write_text(
        """#!/bin/bash
set -euo pipefail
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ROOT="$(cd "${THIS_DIR}/.." && pwd)"
PAYLOAD_DIR="${APP_ROOT}/Resources/app"
exec "${PAYLOAD_DIR}/$(basename "$0")" "$@"
"""
    )
    launcher_script.chmod(0o755)

    # Create Info.plist
    info_plist = app_dir / "Contents" / "Info.plist"
    info_plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>pyinstaller-test</string>
    <key>CFBundleIdentifier</key>
    <string>ai.qasimodo.pyinstaller-test</string>
    <key>CFBundleName</key>
    <string>PyInstaller Test</string>
    <key>CFBundleDisplayName</key>
    <string>PyInstaller Test</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
"""
    )

    # Remove old zip before creating a new one
    zip_path = dist_dir / zip_name
    if zip_path.exists():
        zip_path.unlink()

    # Create a zip for distribution with architecture suffix
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(app_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(dist_dir)
                zipf.write(file_path, arcname)

    print(f"Created {zip_name}")


def create_linux_package():
    """Create Linux package from PyInstaller output."""
    app_name = "pyinstaller-test"
    dist_dir = Path("dist")
    package_dir = dist_dir / f"{app_name}-linux"
    build_dir = dist_dir / app_name

    print("Creating Linux package...")

    # Rename the PyInstaller output directory
    if package_dir.exists():
        shutil.rmtree(package_dir)
    build_dir.rename(package_dir)

    # Create a launcher script
    launcher_script = package_dir / f"{app_name}.sh"
    launcher_script.write_text(
        f"""#!/bin/bash
DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
exec "$DIR/{app_name}" "$@"
"""
    )
    launcher_script.chmod(0o755)

    # Create a README
    readme = package_dir / "README.txt"
    readme.write_text(
        f"""{app_name.title()} - Linux

To run:
  ./{app_name}.sh

Or directly:
  ./{app_name}

To install system-wide (optional):
  sudo cp -r . /opt/{app_name}
  sudo ln -s /opt/{app_name}/{app_name}.sh /usr/local/bin/{app_name}
"""
    )

    # Create tar.gz
    tar_path = dist_dir / f"{app_name}-linux.tar.gz"
    if tar_path.exists():
        tar_path.unlink()

    # Create tar.gz from within dist_dir (like the original script)
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(package_dir, arcname=package_dir.name)

    print(f"Created {app_name}-linux.tar.gz")


def create_windows_package():
    """Create Windows package from PyInstaller output."""
    app_name = "pyinstaller-test"
    dist_dir = Path("dist")
    package_dir = dist_dir / f"{app_name}-windows"
    build_dir = dist_dir / app_name

    print("Creating Windows package...")

    # Rename the PyInstaller output directory
    if package_dir.exists():
        shutil.rmtree(package_dir)
    build_dir.rename(package_dir)

    # Create a README
    readme = package_dir / "README.txt"
    readme.write_text(
        f"""{app_name.title()} - Windows

To run:
  {app_name}.exe

Or from command line:
  .\\{app_name}.exe

To install system-wide (optional):
  1. Copy the entire folder to C:\\Program Files\\{app_name}
  2. Add C:\\Program Files\\{app_name} to your PATH environment variable
  3. Or create a shortcut to {app_name}.exe in your Start Menu
"""
    )

    # Create zip
    zip_path = dist_dir / f"{app_name}-windows.zip"
    if zip_path.exists():
        zip_path.unlink()

    # Create zip for distribution
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(package_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(dist_dir)
                zipf.write(file_path, arcname)

    print(f"Created {app_name}-windows.zip")
