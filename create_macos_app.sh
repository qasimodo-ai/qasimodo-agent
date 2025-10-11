#!/bin/bash
set -e

APP_NAME="pyinstaller-test"
BUNDLE_NAME="${APP_NAME}.app"
DIST_DIR="dist"
APP_DIR="${DIST_DIR}/${BUNDLE_NAME}"

echo "Creating macOS .app bundle..."

# Create .app structure
mkdir -p "${APP_DIR}/Contents/MacOS"
mkdir -p "${APP_DIR}/Contents/Resources"

# Move the PyInstaller output into the bundle
mv "${DIST_DIR}/${APP_NAME}"/* "${APP_DIR}/Contents/MacOS/"
rmdir "${DIST_DIR}/${APP_NAME}"

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

# Make executable
chmod +x "${APP_DIR}/Contents/MacOS/${APP_NAME}"

# Create a zip for distribution with architecture suffix
ARCH=$(uname -m)
cd "${DIST_DIR}"
zip -r "${APP_NAME}-macos-${ARCH}.zip" "${BUNDLE_NAME}"
cd ..

echo "Created ${APP_NAME}-macos-${ARCH}.zip"
