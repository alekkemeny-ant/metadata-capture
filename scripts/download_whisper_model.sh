#!/usr/bin/env bash
set -euo pipefail
DIR="$(dirname "$0")/../agent/models"
mkdir -p "$DIR"
URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
curl -L -o "$DIR/ggml-base.en.bin" "$URL"
echo "Downloaded to $DIR/ggml-base.en.bin"
