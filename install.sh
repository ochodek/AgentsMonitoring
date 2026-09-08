#!/usr/bin/env bash
# Agents Monitoring one-command installer.
# Usage:  curl -fsSL <raw-url>/install.sh | bash      (or run it from a clone)
set -euo pipefail
cd "$PWD" 2>/dev/null || cd "$HOME" 2>/dev/null || cd /

REPO="https://github.com/ochodek/AgentsMonitoring.git"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
err() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# Where to drop the launcher: prefer a writable $HOME dir ALREADY on PATH so `agentsmon` works in
# this very terminal right away (a piped installer can't change the parent shell's PATH). Else
# ~/.local/bin, added to the rc files for new shells.
pick_bindir() {
  oldifs="$IFS"; IFS=:
  for d in $PATH; do
    case "$d" in
      "$HOME"/*) if [ -d "$d" ] && [ -w "$d" ]; then IFS="$oldifs"; printf '%s' "$d"; return; fi ;;
    esac
  done
  IFS="$oldifs"; printf '%s' "$HOME/.local/bin"
}

PY="$(command -v python3 || true)"
[ -n "$PY" ] || err "python3 not found. Install Python 3.10+ first."
"$PY" - <<'PYEOF' || err "Python 3.10+ required."
import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)
PYEOF
say "Using $("$PY" --version)"
command -v tmux >/dev/null || say "note: tmux not found — agents run in tmux, install it before setup."

# Get the code (clone unless already inside it).
if [ -f pyproject.toml ] && grep -q "agents-monitoring" pyproject.toml 2>/dev/null; then
  SRC="$(pwd)"; say "Installing from current directory"
else
  SRC="${HOME}/.agentsmon-src"
  # git first — a real clone keeps `agentsmon update` working. But git over HTTPS can be
  # refused where plain HTTPS is fine: from a datacentre IP that GitHub rate-limits for
  # anonymous git, a PUBLIC repo answers 401 and git stops to ask for a password. On a fresh
  # server that is the user's first impression of the tool, and an installer must never block
  # on a prompt, so fall back to the source archive.
  fetch_tarball() {
    say "Fetching the source archive (no git)"
    tmp="$(mktemp -d)"
    url="https://codeload.github.com/ochodek/AgentsMonitoring/tar.gz/refs/heads/main"
    curl -fsSL --retry 2 "$url" -o "$tmp/src.tgz" || return 1
    tar xzf "$tmp/src.tgz" -C "$tmp" || return 1
    dir="$(find "$tmp" -maxdepth 1 -type d -name 'AgentsMonitoring-*' | head -1)"
    [ -n "$dir" ] || return 1
    rm -rf "$SRC"; mkdir -p "$SRC"; cp -R "$dir"/. "$SRC"/ || return 1
    rm -rf "$tmp"
    say "Installed from archive — 'agentsmon update' will ask you to re-run this installer."
  }
  if [ -d "$SRC/.git" ]; then
    say "Updating $SRC"
    GIT_TERMINAL_PROMPT=0 git -C "$SRC" pull --ff-only || fetch_tarball || err "Could not fetch the project."
  elif command -v git >/dev/null 2>&1; then
    say "Cloning into $SRC"
    # GitHub answers 401 to anonymous git from datacentre IPs intermittently — the very next
    # attempt usually succeeds (Hermes' own installer recovers the same way, on try 2 of 4).
    # Retry before giving up on git, because a real clone is what keeps `update` working;
    # the tarball is the last resort, not the second choice.
    n=1
    while [ "$n" -le 3 ]; do
      GIT_TERMINAL_PROMPT=0 git clone --depth 1 "$REPO" "$SRC" 2>/dev/null && break
      rm -rf "$SRC"
      n=$((n+1))
      [ "$n" -le 3 ] && { say "Clone refused, retrying ($n/3)"; sleep 2; }
    done
    [ -d "$SRC/.git" ] || fetch_tarball || err "Could not fetch the project."
  else
    fetch_tarball || err "Could not fetch the project (no git, and the archive download failed)."
  fi
fi

# pip is OPTIONAL — the package is pure standard library. Try pip (so hooks/other tools can
# import it), but EITHER WAY drop our own launcher into a dir on PATH so `agentsmon` is a real
# command right after install — no PYTHONPATH to remember, no new shell when a PATH dir is writable.
if "$PY" -m pip --version >/dev/null 2>&1 || "$PY" -m ensurepip --upgrade >/dev/null 2>&1; then
  "$PY" -m pip install --user --upgrade "$SRC" >/dev/null 2>&1 \
    || "$PY" -m pip install --user --break-system-packages --upgrade "$SRC" >/dev/null 2>&1 || true
fi
BIND="$(pick_bindir)"
mkdir -p "$BIND"
printf '#!/bin/sh\nexec env PYTHONPATH="%s" "%s" -m agentsmon "$@"\n' "$SRC" "$PY" > "$BIND/agentsmon"
chmod +x "$BIND/agentsmon"
RUN=("$BIND/agentsmon"); HOW="agentsmon"
case ":$PATH:" in
  *":$BIND:"*)
    say "Installed launcher in $BIND (already on PATH) — 'agentsmon' works now." ;;
  *)
    say "Installed launcher in $BIND — adding it to PATH for new shells."
    for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.zshrc"; do
      [ -e "$rc" ] || continue
      grep -qs "$BIND" "$rc" || echo "export PATH=\"$BIND:\$PATH\"" >> "$rc"
    done
    grep -qs "$BIND" "$HOME/.bashrc" 2>/dev/null || echo "export PATH=\"$BIND:\$PATH\"" >> "$HOME/.bashrc"
    export PATH="$BIND:$PATH"
    say "For THIS terminal, run:  export PATH=\"$BIND:\$PATH\"   (new terminals get it automatically)" ;;
esac

say "Run it later with:  $HOW status   (add more bots anytime with:  $HOW add)"
if [ -e /dev/tty ]; then say "Starting setup…"; exec "${RUN[@]}" setup </dev/tty
else say "Installed. Finish setup with:  $HOW setup"; fi
