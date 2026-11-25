#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="AI NetGuard"
APP_BUNDLE="/Applications/${APP_NAME}.app"
CONTENTS_DIR="${APP_BUNDLE}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RES_DIR="${CONTENTS_DIR}/Resources"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "[1/4] Generating 1024x1024 icon with ImageMagick..."
ICON_PNG="${WORK_DIR}/icon_1024.png"
/opt/homebrew/bin/magick -size 1024x1024 xc:none \
  -draw "fill '#1e3a8a' roundrectangle 64,64 960,960 210,210" \
  \( -size 896x896 gradient:'#3b82f6'-'#6d28d9' \
     \( +clone -alpha extract -fill black -colorize 100% -fill white -draw "roundrectangle 0,0 895,895 210,210" \) \
     -alpha off -compose CopyOpacity -composite \) \
  -geometry +64+64 -compose Over -composite \
  -fill "rgba(255,255,255,0.14)" -stroke "rgba(255,255,255,0.94)" -strokewidth 34 \
  -draw "path 'M 512,210 L 748,302 L 748,498 C 748,656 648,770 512,824 C 376,770 276,656 276,498 L 276,302 Z'" \
  -fill none -stroke "#4ade80" -strokewidth 54 \
  -draw "stroke-linecap round stroke-linejoin round polyline 394,515 478,602 642,422" \
  "$ICON_PNG"

echo "[2/4] Building Apple .icns iconset..."
ICONSET_DIR="${WORK_DIR}/AppIcon.iconset"
mkdir -p "$ICONSET_DIR"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_PNG" --out "${ICONSET_DIR}/icon_${size}x${size}.png" >/dev/null
  size2x=$((size * 2))
  sips -z "$size2x" "$size2x" "$ICON_PNG" --out "${ICONSET_DIR}/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET_DIR" -o "${WORK_DIR}/AppIcon.icns"

echo "[3/4] Assembling ${APP_BUNDLE}..."
rm -rf "$APP_BUNDLE"
mkdir -p "$MACOS_DIR" "$RES_DIR"
cp "${WORK_DIR}/AppIcon.icns" "${RES_DIR}/AppIcon.icns"

cat > "${CONTENTS_DIR}/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>AI NetGuard</string>
  <key>CFBundleDisplayName</key>
  <string>AI NetGuard</string>
  <key>CFBundleIdentifier</key>
  <string>com.panyuanhao.ai-net-guard-app</string>
  <key>CFBundleVersion</key>
  <string>2.0.0</string>
  <key>CFBundleShortVersionString</key>
  <string>2.0.0</string>
  <key>CFBundleExecutable</key>
  <string>AINetGuard</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
  </dict>
</dict>
</plist>
EOF

echo "[3.5/4] Compiling native Apple Silicon macOS executable (Swift + WKWebView)..."
swiftc -O -swift-version 5 \
  -framework Cocoa \
  -framework WebKit \
  "${SCRIPT_DIR}/AINetGuardApp.swift" \
  -o "${MACOS_DIR}/AINetGuard"
chmod +x "${MACOS_DIR}/AINetGuard"

echo "[4/4] Signing & registering app with LaunchServices..."
codesign --force --deep --sign - "$APP_BUNDLE"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_BUNDLE"
echo "✅ Built and registered: ${APP_BUNDLE}"
