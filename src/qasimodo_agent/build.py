import subprocess
import sys
import os
import platform
import shutil
import tarfile
import zipfile
from pathlib import Path


def get_version():
    """Read version from VERSION.in file."""
    version_file = Path("VERSION.in")
    if not version_file.exists():
        print(f"Warning: {version_file} not found, using default version 1.0.0")
        return "1.0.0"
    version = version_file.read_text().strip()
    return version


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
        "src/qasimodo_agent/__main__.py",
        "--name",
        "qasimodo-agent",
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
    app_name = "qasimodo-agent"
    bundle_name = f"{app_name}.app"
    dist_dir = Path("dist")
    build_dir = dist_dir / app_name
    app_dir = dist_dir / bundle_name
    resources_app_dir = app_dir / "Contents" / "Resources" / "app"
    arch = platform.machine()
    zip_name = f"{app_name}-macos-{arch}.zip"
    version = get_version()

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
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>qasimodo-agent</string>
    <key>CFBundleIdentifier</key>
    <string>ai.qasimodo.qasimodo-agent</string>
    <key>CFBundleName</key>
    <string>Qasimodo Agent</string>
    <key>CFBundleDisplayName</key>
    <string>Qasimodo Agent</string>
    <key>CFBundleVersion</key>
    <string>{version}</string>
    <key>CFBundleShortVersionString</key>
    <string>{version}</string>
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
    app_name = "qasimodo-agent"
    dist_dir = Path("dist")
    package_dir = dist_dir / f"{app_name}-linux"
    build_dir = dist_dir / app_name

    print("Creating Linux package...")

    # Rename the PyInstaller output directory
    if package_dir.exists():
        shutil.rmtree(package_dir)
    shutil.copytree(build_dir, package_dir)
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
    """Create Windows installer using Inno Setup."""
    app_name = "qasimodo-agent"
    dist_dir = Path("dist")
    build_dir = dist_dir / app_name
    installer_iss = Path("installer.iss")
    version = get_version()

    print("Creating Windows installer...")

    if not build_dir.exists():
        print(f"Error: {build_dir} does not exist. Run the PyInstaller build first.")
        sys.exit(1)

    if not installer_iss.exists():
        print(f"Error: {installer_iss} does not exist.")
        sys.exit(1)

    # Read installer.iss template and replace version
    installer_content = installer_iss.read_text()
    # Replace hardcoded version with dynamic version
    installer_content = installer_content.replace(
        "AppVersion=1.0.0", f"AppVersion={version}"
    )

    # Ensure dist_dir exists for temporary file
    dist_dir.mkdir(parents=True, exist_ok=True)

    # Write to temporary file for compilation
    temp_iss = dist_dir / "installer_temp.iss"
    temp_iss.write_text(installer_content)

    # Try to find iscc.exe (Inno Setup Compiler)
    # Common locations on Windows
    iscc_paths = [
        Path("C:/Program Files (x86)/Inno Setup 6/iscc.exe"),
        Path("C:/Program Files/Inno Setup 6/iscc.exe"),
        Path("C:/Program Files (x86)/Inno Setup 5/iscc.exe"),
        Path("C:/Program Files/Inno Setup 5/iscc.exe"),
    ]

    # Check if iscc is in PATH
    iscc = shutil.which("iscc")
    if iscc:
        iscc_path = Path(iscc)
    else:
        # Try common installation paths
        iscc_path = None
        for path in iscc_paths:
            if path.exists():
                iscc_path = path
                break

    if not iscc_path:
        print("Error: Inno Setup Compiler (iscc.exe) not found.")
        print("Please install Inno Setup or ensure iscc.exe is in your PATH.")
        sys.exit(1)

    print(f"Using Inno Setup Compiler: {iscc_path}")

    # Run Inno Setup Compiler with temporary file
    result = subprocess.run(
        [str(iscc_path), str(temp_iss)],
        check=False,
    )

    # Clean up temporary file
    if temp_iss.exists():
        temp_iss.unlink()

    if result.returncode != 0:
        print(f"Error: Inno Setup Compiler failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    installer_exe = dist_dir / f"{app_name}-setup.exe"
    if installer_exe.exists():
        print(f"Created {installer_exe.name}")
    else:
        print(
            f"Warning: Expected installer {installer_exe} not found after compilation."
        )
