#!/bin/zsh
# Create VEDA's own bridge environment. It never edits another project's bridge.
set -euo pipefail

project_root="${0:A:h:h}"
bridge_venv="$project_root/.bridge-venv"
bridge_source="$project_root/../AI Project-Ps5 Agent/ps5-ai-player/third_party/ps5rmtctl"
seed_venv="$project_root/../AI Project-Ps5 Agent/ps5-ai-player/.bridge-venv"
bridge_copy="$project_root/.bridge-source/ps5rmtctl"

if [[ ! -f "$bridge_source/pyproject.toml" ]]; then
  print -u2 "PS5 bridge source was not found at: $bridge_source"
  exit 1
fi
if [[ ! -x "$seed_venv/bin/python" ]]; then
  print -u2 "PS5 bridge seed environment was not found at: $seed_venv"
  exit 1
fi

# Clone known local dependencies; no package download and no shared editable link.
rm -rf "$bridge_venv"
cp -a "$seed_venv" "$bridge_venv"
rm -rf "$bridge_copy"
mkdir -p "${bridge_copy:h}"
cp -a "$bridge_source" "$bridge_copy"

site_dir="$($bridge_venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
rm -rf "$site_dir/ps5rmtctl" "$site_dir/ps5rmtctl-0.1.0.dist-info"
rm -f "$site_dir/__editable__.ps5rmtctl-0.1.0.pth" "$site_dir/__editable___ps5rmtctl_0_1_0_finder.py"
cp -a "$bridge_copy/ps5rmtctl" "$site_dir/ps5rmtctl"

print "VEDA bridge installed: $bridge_venv"
print "Pair it separately with: $project_root/scripts/bridge setup"
