#!/bin/sh
# Prepare production dependencies once; never connect to a mailbox during installation.
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
environment_path="$project_root/.venv"

fail() {
    printf 'install: %s\n' "$*" >&2
    exit 1
}

command -v uv >/dev/null 2>&1 || fail "uv is required to install mcp-mail"
[ -f "$project_root/uv.lock" ] || fail "uv.lock is missing; use a complete project checkout"
if [ -e "$environment_path" ]; then
    [ -d "$environment_path" ] && [ -w "$environment_path" ] ||
        fail ".venv must be a writable directory for the installing user"
else
    [ -w "$project_root" ] || fail "the installing user cannot create .venv in this checkout"
fi

# Fix the environment location and install a non-editable package for deployment.
if ! UV_PROJECT_ENVIRONMENT="$environment_path" uv sync \
    --project "$project_root" --locked --no-dev --no-editable \
    --reinstall-package mcp-mail; then
    fail "dependency installation failed; review the uv error above and check network access, disk space and permissions of .venv and its contents. Run installation as its owner, or have an administrator provision a writable environment."
fi

python_path="$environment_path/bin/python"
[ -x "$python_path" ] || fail "installation did not create an executable Python interpreter"
if ! "$python_path" -c 'from mcp_mail.server import mcp; from mcp_mail.smoke import main'; then
    fail "installed server imports failed; installation is incomplete"
fi
printf 'Installation verified. Configure IMAP_HOST, IMAP_USER and IMAP_PASSWORD, then launch: %s/scripts/run.sh\n' "$project_root" >&2
