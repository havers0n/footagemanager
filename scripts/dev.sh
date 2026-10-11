#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_dir/backend/.venv/bin/python"
export PATH="$project_dir/.tools/ffmpeg:$PATH"

if [[ ! -x "$python_bin" ]]; then
  echo "Run scripts/setup-wsl.sh first."
  exit 1
fi

cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$project_dir/backend"
"$python_bin" -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload &

cd "$project_dir/frontend"
npm run dev -- --host 127.0.0.1 &

wait
