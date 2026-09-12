#!/bin/bash
set -euo pipefail

APP_NAME="FEMBABE USB"
VOLUME_NAME="FEMBABE USB"
OUTPUT_DIR="dist"
STAGING_DIR="build/dmg"

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --target-architecture universal2 \
  --name "$APP_NAME" \
  --osx-bundle-identifier org.fembabe.usb \
  fembabe_usb.py

chmod 755 "$OUTPUT_DIR/$APP_NAME.app/Contents/MacOS/$APP_NAME"
codesign --force --deep --sign - "$OUTPUT_DIR/$APP_NAME.app"

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"
cp -R "$OUTPUT_DIR/$APP_NAME.app" "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"

hdiutil create \
  -volname "$VOLUME_NAME" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$OUTPUT_DIR/FEMBABE-USB-macOS.dmg"

codesign --verify --deep --strict "$OUTPUT_DIR/$APP_NAME.app"
hdiutil verify "$OUTPUT_DIR/FEMBABE-USB-macOS.dmg"
