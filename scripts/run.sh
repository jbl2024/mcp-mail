#!/bin/sh
# Start the installed stdio server without dependency resolution or environment writes.
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_path="$project_root/.venv/bin/python"
if [ ! -x "$python_path" ]; then
    printf 'run: no usable installed environment; run scripts/install.sh first\n' >&2
    exit 1
fi

# Relative explicit YAML paths are resolved consistently against the checkout.
cd "$project_root"
exec "$python_path" -B -m mcp_mail
