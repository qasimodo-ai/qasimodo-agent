#!/usr/bin/env bash
set -e

# Build Docker image
docker build -t pyinstaller-test-builder .

# Create output directory if it doesn't exist
mkdir -p dist

# Run container and copy dist folder out
docker run --rm -v "$(pwd)/dist:/output" pyinstaller-test-builder

echo "Build complete! Executable is in ./dist/pyinstaller-test"
