#!/bin/zsh
# Install, stop, or inspect VEDA's watch-only per-user launchd service.
set -euo pipefail

project_root="${0:A:h:h}"
label="com.veda.watch"
domain="gui/$(id -u)"
agent_dir="$HOME/Library/LaunchAgents"
agent_file="$agent_dir/$label.plist"
template="$project_root/launchd/$label.plist.template"

case "${1:-}" in
  install)
    mkdir -p "$agent_dir" "$project_root/artifacts"
    sed "s|__PROJECT_ROOT__|$project_root|g" "$template" > "$agent_file"
    launchctl bootout "$domain/$label" 2>/dev/null || true
    launchctl bootstrap "$domain" "$agent_file"
    print "VEDA watcher installed: $agent_file"
    ;;
  stop)
    launchctl bootout "$domain/$label" 2>/dev/null || true
    print "VEDA watcher stopped. Its launchd file remains installed."
    ;;
  uninstall)
    launchctl bootout "$domain/$label" 2>/dev/null || true
    rm -f "$agent_file"
    print "VEDA watcher uninstalled."
    ;;
  status)
    launchctl print "$domain/$label"
    ;;
  *)
    print -u2 "Usage: $0 {install|stop|uninstall|status}"
    exit 2
    ;;
esac
