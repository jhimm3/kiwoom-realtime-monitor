#!/bin/sh
# One-time administrator installation, after capture AND persistence finish.
set -eu
PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin
export PATH
umask 077
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PYTHON=$(command -v python3)
exec "$PYTHON" -I -S "$HERE/../../scripts/nas_operator_install.py" "$@"
