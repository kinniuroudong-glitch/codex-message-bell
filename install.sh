#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

python3 "$SCRIPT_DIR/codex_message_bell.py" --install
python3 "$SCRIPT_DIR/codex_message_bell.py" --test-sound

echo "Codex Message Bell is installed."
echo "It will say: 我做好了，请回复"
