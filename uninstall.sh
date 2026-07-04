#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

python3 "$SCRIPT_DIR/codex_message_bell.py" --uninstall

echo "Codex Message Bell is uninstalled."
