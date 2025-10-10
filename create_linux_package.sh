#!/bin/bash
set -e

APP_NAME="pyinstaller-test"
DIST_DIR="dist"
PACKAGE_DIR="${DIST_DIR}/${APP_NAME}-linux"

echo "Creating Linux package..."

# Rename the PyInstaller output directory
mv "${DIST_DIR}/${APP_NAME}" "${PACKAGE_DIR}"

# Create a launcher script
cat > "${PACKAGE_DIR}/${APP_NAME}.sh" << 'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/pyinstaller-test" "$@"
EOF

chmod +x "${PACKAGE_DIR}/${APP_NAME}.sh"

# Create a README
cat > "${PACKAGE_DIR}/README.txt" << 'EOF'
PyInstaller Test - Linux

To run:
  ./pyinstaller-test.sh

Or directly:
  ./pyinstaller-test

To install system-wide (optional):
  sudo cp -r . /opt/pyinstaller-test
  sudo ln -s /opt/pyinstaller-test/pyinstaller-test.sh /usr/local/bin/pyinstaller-test
EOF

# Create tar.gz
cd "${DIST_DIR}"
tar czf "${APP_NAME}-linux.tar.gz" "${APP_NAME}-linux"
cd ..

echo "Created ${APP_NAME}-linux.tar.gz"
