#!/bin/zsh
# Launcher: runs job-search-buddy from its venv regardless of the current directory.
DIR="${0:A:h}"
PYTHONPATH="$DIR" exec "$DIR/.venv/bin/python" -m jsb "$@"
