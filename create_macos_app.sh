#!/bin/bash
set -euo pipefail

APP_NAME="pyinstaller-test"
BUNDLE_NAME="${APP_NAME}.app"
DIST_DIR="dist"
BUILD_DIR="${DIST_DIR}/${APP_NAME}"
APP_DIR="${DIST_DIR}/${BUNDLE_NAME}"
RESOURCES_APP_DIR="${APP_DIR}/Contents/Resources/app"
ARCH="$(uname -m)"
ZIP_NAME="${APP_NAME}-macos-${ARCH}.zip"

echo "Creating macOS .app bundle..."

if [[ ! -d "${BUILD_DIR}" ]]; then
  echo "Error: ${BUILD_DIR} does not exist. Run the PyInstaller build first."
  exit 1
fi

# Reset bundle directory
rm -rf "${APP_DIR}"
mkdir -p "${APP_DIR}/Contents/MacOS"
mkdir -p "${APP_DIR}/Contents/Resources"

# Copy the PyInstaller output without altering its layout
rm -rf "${RESOURCES_APP_DIR}"
mkdir -p "${RESOURCES_APP_DIR}"
rsync -a "${BUILD_DIR}/" "${RESOURCES_APP_DIR}/"

# Launcher that delegates to the copied PyInstaller build
cat > "${APP_DIR}/Contents/MacOS/${APP_NAME}" << 'EOF'
#!/bin/bash
set -euo pipefail
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ROOT="$(cd "${THIS_DIR}/.." && pwd)"
PAYLOAD_DIR="${APP_ROOT}/Resources/app"
exec "${PAYLOAD_DIR}/$(basename "$0")" "$@"
EOF

chmod +x "${APP_DIR}/Contents/MacOS/${APP_NAME}"

# Create Info.plist
cat > "${APP_DIR}/Contents/Info.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
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
EOF

# Remove old zip before creating a new one
rm -f "${DIST_DIR}/${ZIP_NAME}"

# Create a zip for distribution with architecture suffix
cd "${DIST_DIR}"
zip -r "${APP_NAME}-macos-${ARCH}.zip" "${BUNDLE_NAME}"
cd ..

echo "Created ${ZIP_NAME}"
