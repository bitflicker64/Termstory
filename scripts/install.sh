#!/bin/bash
# TermStory Installer v0.6.4
#TEST 
set -euo pipefail

echo "=== TermStory Installer v0.6.4 ==="

# ── Find Python ────────────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PYTHON="$cmd"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "❌ Python 3 not found. Please install it and re-run."
  exit 1
fi
echo "  Python: $($PYTHON --version)"
echo "  pip:    $($PYTHON -m pip --version 2>&1 | awk '{print $1, $2}')"

# ── Global state ──────────────────────────────────────────────────────────────
# The EXIT trap reads this to restore a stranded backup if the script is
# interrupted mid-install (SIGTERM, SIGINT, unchecked error).
_BACKUP_DIR=""
_SRC_DIR=""

# ── Download & extract ─────────────────────────────────────────────────────────
WORK_DIR=$(mktemp -d)

cleanup() {
  # Restore stranded venv backup if script was aborted mid-install
  if [ -n "$_BACKUP_DIR" ] && [ -d "$_BACKUP_DIR" ]; then
    rm -rf "$HOME/.termstory-venv"
    [ -d "$_BACKUP_DIR/termstory-venv" ] && mv "$_BACKUP_DIR/termstory-venv" "$HOME/.termstory-venv"
    rm -rf "$_BACKUP_DIR"
  fi
  rm -rf "${WORK_DIR:-}"
}
trap cleanup EXIT

echo "  Downloading TermStory..."
if ! curl -fsSL \
    "https://github.com/bitflicker64/Termstory/archive/refs/heads/main.tar.gz" \
    -o "$WORK_DIR/termstory.tar.gz"; then
  echo "❌ Download failed. Check your internet connection."
  exit 1
fi

tar -xzf "$WORK_DIR/termstory.tar.gz" -C "$WORK_DIR"
SRC_DIR="$WORK_DIR/Termstory-main"

if [ ! -d "$SRC_DIR" ]; then
  echo "❌ Extracted archive doesn't contain expected directory: $SRC_DIR"
  exit 1
fi

# ── pip version helper ─────────────────────────────────────────────────────────
# Spawns one Python subprocess; result used at most once per strategy.
pip_major_version() {
  "$PYTHON" -c "import pip; print(int(pip.__version__.split('.')[0]))" 2>/dev/null || echo "0"
}

# ── Shell RC integration ──────────────────────────────────────────────────────
# Marker line so we can idempotently find/remove OUR export later.
# The marker is a comment containing a stable token, so uninstall.sh can
# regex-anchor and only ever touch our own insert (never user config).
RC_MARKER="# Added by termstory installer"
RC_EXPORT_LINE='export PATH="$HOME/.termstory-venv/bin:$PATH"'
RC_FULL_BLOCK="${RC_MARKER}${RC_EXPORT_LINE}"

# Returns 0 if the export AND its marker both appear in this file as
# an adjacent block (marker immediately followed by the export line).
rc_has_export() {
  local rc="$1"
  [ -f "$rc" ] || return 1
  awk -v marker="$RC_MARKER" -v export_line="$RC_EXPORT_LINE" '
    BEGIN { prev_marker = 0 }
    prev_marker == 1 && $0 == export_line { found = 1; exit }
    { prev_marker = ($0 == marker ? 1 : 0) }
    END { exit (found ? 0 : 1) }
  ' "$rc"
}

# Pick the candidate RC file for the active shell, in order of preference.
# Always returns a target path (creating it if absent for the fresh-shell case)
# so the prompt can offer to bring up a brand new RC file when the user wants one.
rc_target_for_install() {
  local shell_name
  shell_name="${SHELL##*/}"

  case "$shell_name" in
    zsh)
      echo "$HOME/.zshrc"
      return 0
      ;;
    bash)
      # Prefer .bashrc if it exists; fall back to .bash_profile for macOS.
      # If neither exists yet, surface .bashrc — it's the modern choice.
      if [ -f "$HOME/.bashrc" ] || ! [ -f "$HOME/.bash_profile" ]; then
        echo "$HOME/.bashrc"
      else
        echo "$HOME/.bash_profile"
      fi
      return 0
      ;;
  esac
  return 1
}

# Append the marker+export block to a file, but only if not present.
rc_append_export() {
  local rc="$1"
  if rc_has_export "$rc"; then
    return 0
  fi
  {
    echo ""
    echo "${RC_MARKER}"
    echo "${RC_EXPORT_LINE}"
  } >> "$rc"
}

# Print manual PATH instructions. Used whenever we can't prompt the user
# (non-TTY, fresh-shell case where the user declined, etc.).
print_manual_path_instructions() {
  local target="${1:-}"
  echo ""
  echo "  For permanent access, add this to your shell RC file:"
  echo ""
  echo "    # Added by termstory installer"
  echo '    export PATH="$HOME/.termstory-venv/bin:$PATH"'
  if [ -n "$target" ]; then
    echo ""
    echo "  Recommended file: $target"
  fi
  echo ""
  echo "  Run right now (no shell restart needed):"
  echo '    $HOME/.termstory-venv/bin/termstory today'
}

offer_path_export() {
  # If neither stdin nor /dev/tty is interactive, just print manual steps.
  # We check /dev/tty because piped curls and similar redirect stdin but
  # /dev/tty is still the controlling terminal — that's the only way to
  # read user input reliably.
  if ! [ -t 0 ] && ! [ -r /dev/tty ]; then
    print_manual_path_instructions
    return 0
  fi

  local target
  if ! target=$(rc_target_for_install); then
    print_manual_path_instructions
    return 0
  fi

  if rc_has_export "$target"; then
    echo "  ✅ PATH export already present in $target"
    return 0
  fi

  # If the RC file doesn't exist yet, confirm with the user before creating it
  # — we never auto-modify dotfiles without consent.
  if [ ! -f "$target" ]; then
    echo ""
    printf '  Create %s with the termstory PATH export? [y/N] ' "$(basename "$target")"
    local reply
    read -r reply </dev/tty 2>/dev/null || reply=""
    case "$reply" in
      [Yy]|[Yy][Ee][Ss])
        : # fall through to append below
        ;;
      *)
        print_manual_path_instructions "$target"
        return 0
        ;;
    esac
  else
    echo ""
    printf '  Add export PATH="$HOME/.termstory-venv/bin:$PATH" to %s? [y/N] ' "$(basename "$target")"
    local reply
    read -r reply </dev/tty 2>/dev/null || reply=""
    case "$reply" in
      [Yy]|[Yy][Ee][Ss])
        : # fall through
        ;;
      *)
        echo ""
        echo "  Manual setup:"
        echo "    Append to $target:"
        echo "    $RC_MARKER"
        echo "    $RC_EXPORT_LINE"
        return 0
        ;;
    esac
  fi

  rc_append_export "$target"
  echo "  ✅ Appended to $target"
  echo "     Activate now:  source $target"
}

# ── Install strategies ─────────────────────────────────────────────────────────

install_venv() {
  local venv="$HOME/.termstory-venv"
  echo "  Trying venv install at $venv ..."

  # Back up any existing venv so we can roll back on failure.
  # backup_dir holds the moved venv at "$backup_dir/termstory-venv".
  # Register cleanup so an interrupted/aborted script doesn't strand it.
  local backup_dir=""
  if [ -d "$venv" ]; then
    backup_dir=$(mktemp -d)
    mv "$venv" "$backup_dir/termstory-venv"
    # Register with global EXIT trap — restores on abort/interrupt
    _BACKUP_DIR="$backup_dir"
  fi

  # Inline rollback helper — bash does not support true nested functions;
  # a "nested" definition leaks to global scope and loses access to locals.
  _rollback_venv() {
    local msg="$1" backup="$2"
    echo "  $msg"
    rm -rf "$venv"
    if [ -n "$backup" ]; then
      mv "$backup/termstory-venv" "$venv"
      rm -rf "$backup"
    fi
    return 1
  }

  if ! "$PYTHON" -m venv "$venv" 2>/dev/null; then
    _rollback_venv "venv creation failed (is the venv module installed?)" "$backup_dir" || return 1
  fi

  # Let pip write its own stderr to the terminal — don't redirect or silence errors.
  local pip_rc=0
  "$venv/bin/pip" install --quiet "$SRC_DIR" || pip_rc=$?
  if [ "$pip_rc" -ne 0 ]; then
    _rollback_venv "pip install failed (exit $pip_rc)." "$backup_dir" || return 1
  fi

  if ! "$venv/bin/python" -c "import termstory" 2>/dev/null; then
    _rollback_venv "Package not importable after install." "$backup_dir" || return 1
  fi

  # Success — discard backup
  [ -n "$backup_dir" ] && rm -rf "$backup_dir" && _BACKUP_DIR=""

  echo ""
  echo "  ✅ Installed in virtualenv."
  echo "  Run right now (no shell restart needed):"
  echo "    $venv/bin/termstory today"
  echo ""

  offer_path_export
}

install_user() {
  echo "  Trying --user install..."

  local pip_ver pip_rc=0
  pip_ver=$(pip_major_version)

  # --break-system-packages required on Debian/Ubuntu with pip >= 23.
  # Let pip write its own stderr to the terminal on failure.
  if [ "$pip_ver" -ge 23 ]; then
    "$PYTHON" -m pip install --quiet --user --break-system-packages "$SRC_DIR" || pip_rc=$?
  else
    "$PYTHON" -m pip install --quiet --user "$SRC_DIR" || pip_rc=$?
  fi

  if [ "$pip_rc" -ne 0 ]; then
    echo "  pip install failed (exit $pip_rc)."
    return 1
  fi

  # Verify the imported module is actually from this install target,
  # not a stale system or site-packages copy.
  if ! "$PYTHON" -c "
import sys, os, site
user_site = site.getusersitepackages()
import termstory
fp = os.path.realpath(termstory.__file__)
if not fp.startswith(os.path.realpath(user_site)):
    sys.exit(1)
" 2>/dev/null; then
    echo "  Package not importable from user site-packages after install."
    return 1
  fi

  # Locate the installed binary (Linux ~/.local/bin or macOS Library path).
  local bin_path
  bin_path=$(find \
    "$HOME/.local/bin" \
    "$HOME/Library/Python" \
    -name "termstory" -type f 2>/dev/null | head -1) || true

  echo ""
  echo "  ✅ Installed."
  if [ -n "$bin_path" ]; then
    echo "  Binary: $bin_path"
    echo "  Run:    termstory today"
    echo "  (Ensure $(dirname "$bin_path") is in your PATH)"
  else
    echo "  Binary not found in standard locations."
    echo "  Run:    $PYTHON -m termstory.cli today"
  fi
}

# ── Try venv first, then user, then give up ────────────────────────────────────
if ! install_venv && ! install_user; then
  echo ""
  echo "❌ All install strategies failed."
  echo "   Manual fallback:"
  echo "     git clone https://github.com/bitflicker64/Termstory.git"
  echo "     cd Termstory && pip install ."
  exit 1
fi
