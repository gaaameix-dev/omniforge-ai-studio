#!/usr/bin/env bash
# Fetch a headless Godot so `engine_build.validate` can open scaffolded projects.
# Unity and Unreal need their own installers; this script only covers Godot.
set -euo pipefail
VERSION="${1:-4.2.2-stable}"
DEST="${2:-$PWD/.tools/godot}"
URL="https://github.com/godotengine/godot/releases/download/${VERSION}/Godot_v${VERSION}_linux.x86_64.zip"

mkdir -p "$DEST"
echo "downloading $URL"
curl -L --fail -o "$DEST/godot.zip" "$URL"
unzip -o -q "$DEST/godot.zip" -d "$DEST"
chmod +x "$DEST"/Godot_v*_linux.x86_64
BINARY="$(ls "$DEST"/Godot_v*_linux.x86_64 | head -1)"
echo "installed: $BINARY"
"$BINARY" --headless --version
cat <<TIP

Point OmniForge at it:
  export OMNIFORGE_ENGINE_COMMANDS="godot:$BINARY --headless --path {project} --export-release Linux"
  export OMNIFORGE_ENGINE_VALIDATE_COMMANDS="godot:$BINARY --headless --path {project} --quit"
TIP
