#!/usr/bin/env bash
# Configure, sync, and build a debug APK from local settings.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
python3 configure.py
npx cap sync android
(cd android && ./gradlew assembleDebug)
