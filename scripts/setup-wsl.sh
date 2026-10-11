#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tools_dir="$project_dir/.tools"
mkdir -p "$tools_dir"

if command -v python3.11 >/dev/null 2>&1; then
  python_cmd="python3.11"
elif command -v python3.12 >/dev/null 2>&1; then
  python_cmd="python3.12"
else
  echo "Python 3.11 or 3.12 is required."
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "FFmpeg was not found; installing a local build..."
  ffmpeg_temp="$(mktemp -d)"
  curl -fL --retry 3 -o "$ffmpeg_temp/ffmpeg.tar.xz" \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
  tar -xJf "$ffmpeg_temp/ffmpeg.tar.xz" -C "$ffmpeg_temp"
  mkdir -p "$tools_dir/ffmpeg"
  cp "$(find "$ffmpeg_temp" -type f -path '*/bin/ffmpeg' -print -quit)" "$tools_dir/ffmpeg/ffmpeg"
  cp "$(find "$ffmpeg_temp" -type f -path '*/bin/ffprobe' -print -quit)" "$tools_dir/ffmpeg/ffprobe"
  chmod +x "$tools_dir/ffmpeg/ffmpeg" "$tools_dir/ffmpeg/ffprobe"
  rm -r "$ffmpeg_temp"
fi
export PATH="$tools_dir/ffmpeg:$PATH"

"$python_cmd" -m venv "$project_dir/backend/.venv"
"$project_dir/backend/.venv/bin/pip" install --upgrade pip wheel
"$project_dir/backend/.venv/bin/pip" install -r "$project_dir/backend/requirements-core.txt"

if [[ "${1:-}" == "--with-ai" ]]; then
  "$project_dir/backend/.venv/bin/pip" install -r "$project_dir/backend/requirements-ai.txt"
fi

cd "$project_dir/frontend"
npm install
npm run build

echo "Setup complete. Run start-footage-manager.cmd from Windows."
