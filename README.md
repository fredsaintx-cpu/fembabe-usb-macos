# FEMBABE USB for macOS

Universal macOS build of the FEMBABE USB Streaming Helper for Intel and Apple
Silicon Macs.

The app opens a local RTMP endpoint for OBS and relays the stream to a
connected iPhone through Apple's usbmuxd service.

## Usage

1. Connect the iPhone to the Mac using USB.
2. Unlock the iPhone and trust the Mac if prompted.
3. Open `FEMBABE USB.app`.
4. Enable LIVE in the FemBabe VCam overlay on the iPhone.
5. In OBS, choose a custom streaming service and use the URL shown by the app.
6. Use `stream` as the stream key.

## Build

The GitHub Actions workflow uses PyInstaller on macOS and packages the app in
a compressed DMG. The app is ad-hoc signed for local use, not notarized with an
Apple Developer ID.

If Gatekeeper blocks the downloaded app, right-click it and choose **Open**.
