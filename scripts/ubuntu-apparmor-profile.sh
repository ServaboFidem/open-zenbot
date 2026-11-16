#!/usr/bin/env bash
# Install (or remove) the AppArmor profile Electron needs on Ubuntu 23.10 and later.
#
# Ubuntu blocks unprivileged user namespaces (kernel.apparmor_restrict_unprivileged_userns=1),
# which Chromium's sandbox needs, so the app exits at start with "The SUID sandbox helper
# binary was found, but is not configured correctly". This profile lets only this repo's
# Electron binary use user namespaces. It survives `npm install`; run it again if the repo moves.
#
#   ./scripts/ubuntu-apparmor-profile.sh             # write and load the profile (asks for sudo)
#   ./scripts/ubuntu-apparmor-profile.sh --dry-run   # print the profile, change nothing
#   ./scripts/ubuntu-apparmor-profile.sh --remove    # unload and delete it
#
# Never work around this with --no-sandbox: the design pane runs bot-made pages.

set -euo pipefail

name=open-zenbot-electron
profile_path=/etc/apparmor.d/$name
mode=install

for arg in "$@"; do
  case "$arg" in
    --dry-run) mode=dry-run ;;
    --remove) mode=remove ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != Linux ]]; then
  echo "This is for Linux (Ubuntu) only." >&2
  exit 1
fi

if [[ "$mode" == remove ]]; then
  if [[ ! -e "$profile_path" ]]; then
    echo "No profile at $profile_path; nothing to remove."
    exit 0
  fi
  sudo apparmor_parser -R "$profile_path" || true
  sudo rm -f "$profile_path"
  echo "Removed $profile_path."
  exit 0
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
electron="$repo_root/node_modules/electron/dist/electron"

if [[ ! -x "$electron" ]]; then
  echo "Electron not found at $electron." >&2
  echo "Run 'npm install' (or ./scripts/dev.sh once) from $repo_root first." >&2
  exit 1
fi

# AppArmor matches the path as a glob, so refuse characters that would change its meaning.
case "$electron" in
  *[[:space:]*?\[\]{}\"#^,]*)
    echo "The repo path has a character an AppArmor profile path can't hold: $electron" >&2
    echo "Move the repo to a plain path and run this again." >&2
    exit 1 ;;
esac

if ! command -v apparmor_parser >/dev/null 2>&1; then
  echo "apparmor_parser not found: AppArmor isn't installed, so no profile is needed." >&2
  exit 1
fi

restrict="$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)"
if [[ "$restrict" != 1 ]]; then
  echo "Note: kernel.apparmor_restrict_unprivileged_userns is not 1 here, so Electron"
  echo "shouldn't need this profile. Installing it anyway is harmless."
fi

profile="abi <abi/4.0>,
include <tunables/global>

profile $name $electron flags=(unconfined) {
  userns,
  include if exists <local/$name>
}"

if [[ "$mode" == dry-run ]]; then
  echo "Would write $profile_path:"
  echo
  echo "$profile"
  exit 0
fi

printf '%s\n' "$profile" | sudo tee "$profile_path" >/dev/null
sudo apparmor_parser -r "$profile_path"
echo "Loaded $profile_path for $electron."
echo "Start the app with ./scripts/dev.sh."
