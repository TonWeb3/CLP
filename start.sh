#!/usr/bin/env bash
# Start ClipForge. Creates a virtual environment and installs everything on the first run.
set -e
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is not installed. Install it first, for example:  sudo apt install ffmpeg"
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed."
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv || { echo "Could not create a virtual environment. On Ubuntu run:  sudo apt install python3-venv"; exit 1; }
fi
# shellcheck disable=SC1091
. .venv/bin/activate

if [ ! -f .venv/.deps-installed ] || [ requirements.txt -nt .venv/.deps-installed ]; then
  echo "Installing dependencies (first run only)..."
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
  touch .venv/.deps-installed
fi

# NVIDIA GPU: faster-whisper needs the CUDA 12 runtime libraries.
if command -v nvidia-smi >/dev/null 2>&1; then
  if ! python -c "import nvidia.cublas.lib, nvidia.cudnn.lib" >/dev/null 2>&1; then
    echo "Installing CUDA libraries for speech recognition..."
    pip install -q nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*" || echo "(could not install CUDA libraries; transcription will fall back to the CPU)"
  fi
  CUDA_LIBS="$(python -c 'import os, nvidia.cublas.lib, nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ":" + os.path.dirname(nvidia.cudnn.lib.__file__))' 2>/dev/null || true)"
  if [ -n "$CUDA_LIBS" ]; then
    export LD_LIBRARY_PATH="$CUDA_LIBS:${LD_LIBRARY_PATH:-}"
  fi
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo
  echo "Note: Ollama is not installed on this machine. Install it to run language models locally:"
  echo "  curl -fsSL https://ollama.com/install.sh | sh"
  echo "(Or use an OpenAI-compatible API from the Settings page.)"
  echo
fi

exec python main.py
