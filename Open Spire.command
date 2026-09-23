#!/bin/zsh
# Double-click this file in Finder to open a fresh watch-only Spire terminal.

cd -- "${0:A:h}" || exit 1

if [[ -x /opt/homebrew/bin/codex ]]; then
  exec /opt/homebrew/bin/codex
elif (( $+commands[codex] )); then
  exec codex
else
  print "Codex was not found. Install it first, then reopen this launcher."
  read -k 1 "?Press any key to close."
fi
