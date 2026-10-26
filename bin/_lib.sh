# Shared helpers for omarchy-dev wrappers. Sourced, not executed.

# Prefer mise-provided root; fall back with builtin cd (shell may alias cd→zoxide).
if [[ -z ${OMARCHY_DEV_ROOT:-} ]]; then
  OMARCHY_DEV_ROOT="$(CDPATH= builtin cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && builtin pwd)"
fi
OMARCHY_DEV_OMARCHY="${OMARCHY_DEV_OMARCHY:-$OMARCHY_DEV_ROOT/omarchy}"
OMARCHY_DEV_ISO="${OMARCHY_DEV_ISO:-$OMARCHY_DEV_ROOT/omarchy-iso}"
OMARCHY_DEV_VMS="${OMARCHY_DEV_VMS:-$OMARCHY_DEV_ROOT/vms}"
OMARCHY_DEV_WORKTREES="${OMARCHY_DEV_WORKTREES:-$OMARCHY_DEV_ROOT/worktrees}"

omarchy_dev_die() {
  printf '%s\n' "$*" >&2
  exit 1
}

omarchy_dev_abs() {
  CDPATH= builtin cd -- "$1" && builtin pwd
}

# Resolve a tree argument to an absolute path.
# Empty → $OMARCHY_DEV_OMARCHY
# Absolute/relative path that exists → that path
# Short name → $OMARCHY_DEV_WORKTREES/<name> or $OMARCHY_DEV_ROOT/<name>
omarchy_dev_resolve_tree() {
  local arg="${1:-}"
  local candidate

  if [[ -z $arg ]]; then
    candidate=$OMARCHY_DEV_OMARCHY
  elif [[ -d $arg ]]; then
    candidate=$arg
  elif [[ -d $OMARCHY_DEV_WORKTREES/$arg ]]; then
    candidate=$OMARCHY_DEV_WORKTREES/$arg
  elif [[ -d $OMARCHY_DEV_ROOT/$arg ]]; then
    candidate=$OMARCHY_DEV_ROOT/$arg
  else
    omarchy_dev_die "Unknown tree: $arg (tried path, worktrees/$arg, and $arg under omarchy-dev)"
  fi

  omarchy_dev_abs "$candidate"
}

omarchy_dev_require_iso_repo() {
  [[ -x $OMARCHY_DEV_ISO/bin/omarchy-iso-boot ]] ||
    omarchy_dev_die "omarchy-iso missing or incomplete at $OMARCHY_DEV_ISO — run: mise setup"
}

# Build a standalone git checkout of a tree (incl. worktrees) for guest sync.
# iso-test's --sync-all strips .git; we pass this dir instead so the guest gets
# a real repo (update-dev, etc.). Overlays the working tree so dirty files win.
omarchy_dev_materialize_sync_tree() {
  local src=$1
  local dest=${2:-$OMARCHY_DEV_ISO/test-runs/.sync-tree}
  local url branch

  [[ -d $src ]] || omarchy_dev_die "materialize: not a directory: $src"
  git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
    omarchy_dev_die "materialize: not a git checkout: $src"

  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"

  # Full object copy (worktree .git is a gitdir pointer — useless inside the guest).
  git clone --no-hardlinks "$src" "$dest" ||
    omarchy_dev_die "materialize: git clone failed from $src"

  # Match the worktree's dirty/uncommitted files on top of the clone.
  tar -C "$src" --exclude .git -cf - . | tar -C "$dest" -xf -

  branch=$(git -C "$src" branch --show-current 2>/dev/null || true)
  if [[ -n $branch ]]; then
    git -C "$dest" checkout -B "$branch" >/dev/null 2>&1 || true
  fi

  if url=$(git -C "$src" remote get-url origin 2>/dev/null); then
    git -C "$dest" remote set-url origin "$url" 2>/dev/null ||
      git -C "$dest" remote add origin "$url"
  fi

  # Drop upstream tracking if it points at something the guest can't fetch yet;
  # update-dev skips cleanly when there is no upstream (still a valid checkout).
  git -C "$dest" branch --unset-upstream >/dev/null 2>&1 || true

  printf '%s\n' "$dest"
}

# Pointer to the ISO basename (e.g. omarchy-edge-….iso) whose base sync/test use.
OMARCHY_DEV_CURRENT_ISO_FILE="${OMARCHY_DEV_CURRENT_ISO_FILE:-$OMARCHY_DEV_ISO/test-runs/CURRENT}"

# Newest ISO under omarchy-iso/release/ (mtime). Prints path; returns 1 if none.
omarchy_dev_find_latest_iso() {
  local release=$OMARCHY_DEV_ISO/release
  local latest="" f

  # Prefer mtime order without relying on ls (may be aliased to eza in interactive shells).
  for f in "$release"/*.iso; do
    [[ -f $f ]] || continue
    if [[ -z $latest || $f -nt $latest ]]; then
      latest=$f
    fi
  done
  [[ -n $latest ]] || return 1
  printf '%s\n' "$latest"
}

omarchy_dev_latest_iso() {
  local latest
  latest=$(omarchy_dev_find_latest_iso) ||
    omarchy_dev_die "No ISO in $OMARCHY_DEV_ISO/release — run: mise setup"
  printf '%s\n' "$latest"
}

# Default-options base disk path for an ISO (matches omarchy-iso-test BASE_NAME).
omarchy_dev_base_disk_for_iso() {
  local iso=$1
  printf '%s\n' "$OMARCHY_DEV_ISO/test-runs/$(basename "$iso" .iso)/base.qcow2"
}

# True if disk looks like a finished install (empty qemu-img create is ~200KiB).
omarchy_dev_base_is_usable() {
  local disk=$1 bytes
  [[ -f $disk ]] || return 1
  bytes=$(stat -c%s "$disk" 2>/dev/null) || return 1
  ((bytes >= 1024 * 1024 * 1024))
}

omarchy_dev_set_current_iso() {
  local iso=$1 name
  name=$(basename "$iso")
  mkdir -p "$(dirname "$OMARCHY_DEV_CURRENT_ISO_FILE")"
  printf '%s\n' "$name" >"$OMARCHY_DEV_CURRENT_ISO_FILE"
}

# ISO path for the active acceptance base (CURRENT file, else newest usable, else latest file).
omarchy_dev_current_iso() {
  local name iso disk dir base latest_usable="" f

  if [[ -f $OMARCHY_DEV_CURRENT_ISO_FILE ]]; then
    name=$(<"$OMARCHY_DEV_CURRENT_ISO_FILE")
    iso=$OMARCHY_DEV_ISO/release/$name
    disk=$(omarchy_dev_base_disk_for_iso "$iso")
    if [[ -f $iso ]] && omarchy_dev_base_is_usable "$disk"; then
      printf '%s\n' "$iso"
      return 0
    fi
  fi

  shopt -s nullglob
  for disk in "$OMARCHY_DEV_ISO"/test-runs/*/base.qcow2; do
    omarchy_dev_base_is_usable "$disk" || continue
    dir=$(dirname "$disk")
    base=$(basename "$dir")
    iso=$OMARCHY_DEV_ISO/release/$base.iso
    [[ -f $iso ]] || continue
    if [[ -z $latest_usable || $disk -nt $latest_usable ]]; then
      latest_usable=$disk
      f=$iso
    fi
  done
  shopt -u nullglob

  if [[ -n ${f:-} ]]; then
    omarchy_dev_set_current_iso "$f"
    printf '%s\n' "$f"
    return 0
  fi

  omarchy_dev_find_latest_iso
}

omarchy_dev_guess_channel() {
  local name=$1
  case "$name" in
    *edge*) printf '%s\n' edge ;;
    *rc*) printf '%s\n' rc ;;
    omarchy-[0-9]*) printf '%s\n' stable ;;
    *) printf '%s\n' unknown ;;
  esac
}

# Print active ISO + base (for humans / mise rebase status).
omarchy_dev_rebase_status() {
  local iso name disk bytes mtime channel state
  if ! iso=$(omarchy_dev_current_iso 2>/dev/null); then
    echo "No acceptance base yet. Run: mise rebase    # edge"
    echo "Or pick one:              mise rebase list"
    return 0
  fi
  name=$(basename "$iso")
  disk=$(omarchy_dev_base_disk_for_iso "$iso")
  channel=$(omarchy_dev_guess_channel "$name")
  if omarchy_dev_base_is_usable "$disk"; then
    state=usable
  elif [[ -f $disk ]]; then
    state="incomplete ($(stat -c%s "$disk") bytes)"
  else
    state=missing
  fi
  bytes=$(stat -c%s "$disk" 2>/dev/null || echo 0)
  mtime=$(stat -c%y "$disk" 2>/dev/null | cut -d. -f1 || echo unknown)
  cat <<EOF
Acceptance base (shared by stock + all worktrees)
  ISO:     $name
  Channel: $channel
  Path:    $iso
  Base:    $disk
  State:   $state
  Size:    $((bytes / 1024 / 1024)) MiB
  Mtime:   $mtime
EOF
}

# Download url → dest atomically (*.partial then rename). Re-verify if dest exists.
omarchy_dev_fetch_file() {
  local url=$1 dest=$2
  local sum_url=$3
  local dir base partial

  dir=$(dirname "$dest")
  base=$(basename "$dest")
  partial=$dest.partial
  mkdir -p "$dir"

  if [[ -f $dest ]]; then
    if [[ -f $dest.sha256 ]] || curl -fsSL -o "$dest.sha256" "$sum_url"; then
      if (CDPATH= builtin cd -- "$dir" && sha256sum -c "$base.sha256") >/dev/null 2>&1; then
        echo "Already present (checksum OK): $dest"
        return 0
      fi
      echo "Checksum mismatch for $dest — re-downloading"
      rm -f "$dest"
    fi
  fi

  echo "Downloading → $dest"
  rm -f "$partial"
  curl -fL --progress-bar -o "$partial" "$url"
  curl -fsSL -o "$dest.sha256" "$sum_url"
  mv -f "$partial" "$dest"
  (CDPATH= builtin cd -- "$dir" && sha256sum -c "$base.sha256")
}

omarchy_dev_channel_url() {
  case "${1:-edge}" in
    edge) printf '%s\n' "https://nightly.omarchy.org/edge/latest.json" ;;
    stable) printf '%s\n' "https://nightly.omarchy.org/stable/latest.json" ;;
    *) omarchy_dev_die "Unknown channel: $1 (use edge or stable; rc is versioned on iso.omarchy.org)" ;;
  esac
}

# Ensure channel ISO is in release/; print its path. Channels: edge | stable.
omarchy_dev_ensure_channel_iso() {
  local channel=${1:-edge} meta iso_url iso_name dest
  command -v jq >/dev/null || omarchy_dev_die "jq is required"
  command -v curl >/dev/null || omarchy_dev_die "curl is required"
  omarchy_dev_require_iso_repo
  mkdir -p "$OMARCHY_DEV_ISO/release"
  meta=$(curl -fsSL "$(omarchy_dev_channel_url "$channel")")
  iso_url=$(jq -r .url <<<"$meta")
  iso_name=${iso_url##*/}
  dest=$OMARCHY_DEV_ISO/release/$iso_name
  echo "Channel $channel → $(jq -r .version <<<"$meta") ($iso_name)" >&2
  omarchy_dev_fetch_file "$iso_url" "$dest" "$iso_url.sha256"
  printf '%s\n' "$dest"
}

# Fetch a release/RC ISO from iso.omarchy.org (or any URL) into release/.
omarchy_dev_ensure_iso_url() {
  local url=$1 dest name
  name=${url##*/}
  dest=$OMARCHY_DEV_ISO/release/$name
  omarchy_dev_require_iso_repo
  mkdir -p "$OMARCHY_DEV_ISO/release"
  omarchy_dev_fetch_file "$url" "$dest" "$url.sha256"
  printf '%s\n' "$dest"
}

# Lines: kind<TAB>version<TAB>url<TAB>local_path_or_-
# kind is edge|stable|rc|local
omarchy_dev_list_iso_candidates() {
  local meta url name ver dest local_path seen="" f
  command -v jq >/dev/null || omarchy_dev_die "jq is required"
  command -v curl >/dev/null || omarchy_dev_die "curl is required"

  for channel in edge stable; do
    if meta=$(curl -fsSL "$(omarchy_dev_channel_url "$channel")" 2>/dev/null); then
      ver=$(jq -r .version <<<"$meta")
      url=$(jq -r .url <<<"$meta")
      name=${url##*/}
      dest=$OMARCHY_DEV_ISO/release/$name
      local_path=-
      [[ -f $dest ]] && local_path=$dest
      printf '%s\t%s\t%s\t%s\n' "$channel" "$ver" "$url" "$local_path"
      seen+=" $name "
    else
      echo "warn: could not fetch $channel latest.json" >&2
    fi
  done

  # RC / prerelease ISOs linked from GitHub release bodies (iso.omarchy.org).
  local host_arch
  host_arch=$(uname -m)
  if rels=$(curl -fsSL https://api.github.com/repos/omacom/omarchy/releases 2>/dev/null); then
    while IFS= read -r url; do
      [[ -n $url ]] || continue
      name=${url##*/}
      [[ $seen == *" $name "* ]] && continue
      # Skip the other arch's ISO (names are …-aarch64.iso vs plain x86_64).
      if [[ $host_arch == aarch64 || $host_arch == arm64 ]]; then
        [[ $name == *aarch64* ]] || continue
      else
        [[ $name == *aarch64* ]] && continue
      fi
      ver=${name#omarchy-}
      ver=${ver%.iso}
      dest=$OMARCHY_DEV_ISO/release/$name
      local_path=-
      [[ -f $dest ]] && local_path=$dest
      printf '%s\t%s\t%s\t%s\n' rc "$ver" "$url" "$local_path"
      seen+=" $name "
    done < <(jq -r '.[] | select(.prerelease==true) | .body // empty' <<<"$rels" |
      grep -oE 'https://iso\.omarchy\.org/omarchy-[A-Za-z0-9._-]+\.iso' | sort -u)
  fi

  shopt -s nullglob
  for f in "$OMARCHY_DEV_ISO"/release/*.iso; do
    name=$(basename "$f")
    [[ $seen == *" $name "* ]] && continue
    printf '%s\t%s\t%s\t%s\n' local "${name%.iso}" "-" "$f"
  done
  shopt -u nullglob
}

# Always (re)build the acceptance base via omarchy-iso-test --install-only.
omarchy_dev_build_base() {
  local iso=${1:-}
  omarchy_dev_require_iso_repo
  [[ -n $iso ]] || iso=$(omarchy_dev_latest_iso)
  [[ -f $iso ]] || omarchy_dev_die "ISO not found: $iso"

  echo "Building acceptance base from $(basename "$iso")" >&2
  builtin cd "$OMARCHY_DEV_ISO"
  ./bin/omarchy-iso-test "$iso" --install-only --no-preview
  omarchy_dev_set_current_iso "$iso"
}

# Fetch latest edge ISO and force-rebuild base (default `mise rebase`).
omarchy_dev_rebase_edge() {
  local iso
  iso=$(omarchy_dev_ensure_channel_iso edge)
  omarchy_dev_build_base "$iso"
}

# Interactive pick from omarchy_dev_list_iso_candidates, then fetch + rebuild.
omarchy_dev_rebase_list() {
  local -a labels=() urls=()
  local kind ver url local_path label pick idx iso line

  while IFS=$'\t' read -r kind ver url local_path; do
    [[ -n $kind ]] || continue
    if [[ $local_path != - ]]; then
      label=$(printf '%-7s %-22s local' "$kind" "$ver")
    else
      label=$(printf '%-7s %-22s remote' "$kind" "$ver")
    fi
    labels+=("$label")
    if [[ $url != - ]]; then
      urls+=("$url")
    else
      urls+=("file://$local_path")
    fi
  done < <(omarchy_dev_list_iso_candidates)

  ((${#labels[@]})) || omarchy_dev_die "No ISOs found (network or omarchy-iso/release/)"

  if ! command -v gum >/dev/null 2>&1; then
    echo "Available ISOs (install gum for interactive select):"
    printf '  %s\n' "${labels[@]}"
    omarchy_dev_die "Re-run with gum installed, or: mise rebase   # edge"
  fi

  pick=$(printf '%s\n' "${labels[@]}" | gum choose --header "ISO to rebase onto (shared base for stock + all worktrees)")
  [[ -n $pick ]] || omarchy_dev_die "No ISO selected"

  idx=-1
  for i in "${!labels[@]}"; do
    if [[ ${labels[$i]} == "$pick" ]]; then
      idx=$i
      break
    fi
  done
  ((idx >= 0)) || omarchy_dev_die "Internal select error"

  url=${urls[$idx]}
  if [[ $url == file://* ]]; then
    iso=${url#file://}
  else
    iso=$(omarchy_dev_ensure_iso_url "$url")
  fi
  omarchy_dev_build_base "$iso"
}

# Build base only when missing or unusable (used by init). No-op if no ISO yet.
omarchy_dev_ensure_base() {
  local iso disk
  omarchy_dev_require_iso_repo
  if ! iso=$(omarchy_dev_find_latest_iso); then
    echo "Skipping acceptance base (no ISO in omarchy-iso/release/)" >&2
    return 0
  fi
  disk=$(omarchy_dev_base_disk_for_iso "$iso")
  if omarchy_dev_base_is_usable "$disk"; then
    omarchy_dev_set_current_iso "$iso"
    echo "Acceptance base ready: $disk" >&2
    return 0
  fi
  omarchy_dev_build_base "$iso"
}

# Fail fast before --reuse-base boots an empty disk into the UEFI Boot Manager.
omarchy_dev_require_base() {
  local iso=${1:-} disk
  [[ -n $iso ]] || iso=$(omarchy_dev_current_iso) ||
    omarchy_dev_die "No acceptance ISO/base — run: mise rebase    # or: mise rebase list"
  disk=$(omarchy_dev_base_disk_for_iso "$iso")
  if omarchy_dev_base_is_usable "$disk"; then
    printf '%s\n' "$iso"
    return 0
  fi
  omarchy_dev_die "No usable acceptance base at $disk — run: mise rebase    # or: mise rebase list"
}

# Remembered keyboard for --grab-kbd (gitignored). Override with OMARCHY_DEV_GRAB_KBD.
OMARCHY_DEV_GRAB_KBD_FILE="${OMARCHY_DEV_GRAB_KBD_FILE:-$OMARCHY_DEV_ROOT/.grab-kbd}"

# List host keyboards under /dev/input/by-id (stable names, no hardcoded models).
# Skips *-ifN-event-kbd siblings (extra interfaces on the same physical board).
omarchy_dev_list_keyboards() {
  local p
  shopt -s nullglob
  for p in /dev/input/by-id/*-event-kbd; do
    [[ $p == *-if[0-9]*-event-kbd ]] && continue
    printf '%s\n' "$p"
  done
  shopt -u nullglob
}

# Resolve which keyboard to grab: env → remembered file → sole device → gum choose.
omarchy_dev_resolve_grab_keyboard() {
  local kbd remembered
  local -a candidates=()

  if [[ -n ${OMARCHY_DEV_GRAB_KBD:-} ]]; then
    kbd=$OMARCHY_DEV_GRAB_KBD
    [[ -e $kbd ]] || omarchy_dev_die "OMARCHY_DEV_GRAB_KBD not found: $kbd"
    printf '%s\n' "$kbd"
    return 0
  fi

  if [[ -f $OMARCHY_DEV_GRAB_KBD_FILE ]]; then
    remembered=$(<"$OMARCHY_DEV_GRAB_KBD_FILE")
    if [[ -n $remembered && -e $remembered ]]; then
      printf '%s\n' "$remembered"
      return 0
    fi
  fi

  mapfile -t candidates < <(omarchy_dev_list_keyboards)
  ((${#candidates[@]})) || omarchy_dev_die "No keyboards in /dev/input/by-id/*-event-kbd"

  if ((${#candidates[@]} == 1)); then
    kbd=${candidates[0]}
  elif command -v gum >/dev/null 2>&1; then
    kbd=$(printf '%s\n' "${candidates[@]}" | gum choose --header "Keyboard to grab for the VM (Left+Right Ctrl toggles)")
    [[ -n $kbd ]] || omarchy_dev_die "No keyboard selected"
  else
    omarchy_dev_die "Several keyboards found; set OMARCHY_DEV_GRAB_KBD or install gum to pick one:"$'\n'"$(printf '  %s\n' "${candidates[@]}")"
  fi

  printf '%s\n' "$kbd" >"$OMARCHY_DEV_GRAB_KBD_FILE"
  printf '%s\n' "$kbd"
}

# Temporary ACL so this user can open the evdev node without a reboot / input group.
omarchy_dev_acl_allow_kbd() {
  local kbd=$1
  sudo setfacl -m "u:${USER}:rw" "$kbd"
}

omarchy_dev_acl_revoke_kbd() {
  local kbd=$1
  sudo setfacl -x "u:${USER}" "$kbd" 2>/dev/null || true
}

# --- Shared VM session (test): display mode + optional keyboard grab ------

OMARCHY_DEV_SESSION_MODE=graphical
OMARCHY_DEV_SESSION_GRAB=true
OMARCHY_DEV_SESSION_GRAB_PATH=
OMARCHY_DEV_SESSION_REST=()

omarchy_dev_session_flags_help() {
  cat <<'EOF'
  (default)       QEMU window + exclusive keyboard grab (Super in guest).
  --ssh           Headless (no window); grab still on unless --no-grab-kbd.
  --graphical     Same as default window mode (explicit).
  --no-grab-kbd   Do not grab the host keyboard.
  --grab-kbd      Grab on (default; explicit).
  --run-tests        Run automated checks (default is interactive desktop only).
                  Keyboard: OMARCHY_DEV_GRAB_KBD, else .grab-kbd, else sole
                  *-event-kbd, else gum. Temporary ACL. Starts ON.
                  Toggle host/guest: Left+Right Ctrl. Release grab for host Super.
EOF
}

# Pull session flags from anywhere in "$@".
# Remaining args (order preserved) → OMARCHY_DEV_SESSION_REST.
omarchy_dev_session_parse_flags() {
  OMARCHY_DEV_SESSION_MODE=graphical
  OMARCHY_DEV_SESSION_GRAB=true
  OMARCHY_DEV_SESSION_REST=()

  while (($#)); do
    case "$1" in
      --graphical)
        OMARCHY_DEV_SESSION_MODE=graphical
        ;;
      --ssh)
        OMARCHY_DEV_SESSION_MODE=ssh
        ;;
      --grab-kbd)
        OMARCHY_DEV_SESSION_GRAB=true
        ;;
      --no-grab-kbd)
        OMARCHY_DEV_SESSION_GRAB=false
        ;;
      --run-tests)
        # Handled by omarchy-dev-test; keep in REST so it still sees it.
        OMARCHY_DEV_SESSION_REST+=("$1")
        ;;
      --)
        shift
        # Keep the separator so callers (test → iso-test) still see it.
        OMARCHY_DEV_SESSION_REST+=(-- "$@")
        break
        ;;
      *)
        OMARCHY_DEV_SESSION_REST+=("$1")
        ;;
    esac
    shift
  done
}

omarchy_dev_session_cleanup() {
  if [[ -n ${OMARCHY_DEV_SESSION_GRAB_PATH:-} ]]; then
    omarchy_dev_acl_revoke_kbd "$OMARCHY_DEV_SESSION_GRAB_PATH"
    OMARCHY_DEV_SESSION_GRAB_PATH=
  fi
}

# Apply display + grab to OMARCHY_ISO_QEMU_EXTRA (iso-test).
omarchy_dev_session_prepare() {
  local extra="" prefix=${1:-omarchy-dev}

  OMARCHY_DEV_SESSION_GRAB_PATH=
  trap omarchy_dev_session_cleanup EXIT

  case "$OMARCHY_DEV_SESSION_MODE" in
    graphical)
      # Video only. start_vm already adds one usb-tablet; a second copy plus
      # input-linux made the guest see duplicate/weird key and pointer events.
      extra+=" -display gtk"
      echo "$prefix: graphical mode (QEMU window)." >&2
      ;;
    ssh)
      echo "$prefix: SSH/headless mode." >&2
      ;;
    *)
      omarchy_dev_die "Unknown session mode: $OMARCHY_DEV_SESSION_MODE"
      ;;
  esac

  if $OMARCHY_DEV_SESSION_GRAB; then
    OMARCHY_DEV_SESSION_GRAB_PATH=$(omarchy_dev_resolve_grab_keyboard)
    echo "$prefix: grabbing $OMARCHY_DEV_SESSION_GRAB_PATH (starts ON → guest)." >&2
    echo "$prefix: toggle host/guest: hold Left-Ctrl + Right-Ctrl, then release." >&2
    omarchy_dev_acl_allow_kbd "$OMARCHY_DEV_SESSION_GRAB_PATH"
    # grab_all=on: no second host keyboard/mouse sneaking into GTK → guest.
    # repeat=off: avoid fighting the guest's own key repeat.
    extra+=" -object input-linux,id=omarchy-dev-kbd,evdev=$OMARCHY_DEV_SESSION_GRAB_PATH,grab_all=on,repeat=off,grab-toggle=ctrl-ctrl"
  fi

  extra=${extra# }
  [[ -z $extra ]] && return 0

  export QEMU_EXTRA="${QEMU_EXTRA:+$QEMU_EXTRA }$extra"
  export OMARCHY_ISO_QEMU_EXTRA="${OMARCHY_ISO_QEMU_EXTRA:+$OMARCHY_ISO_QEMU_EXTRA }$extra"
}

# Teach local omarchy-iso-test: QEMU_EXTRA + activate synced tree (idempotent).
omarchy_dev_ensure_iso_qemu_extra_hook() {
  local f=$OMARCHY_DEV_ISO/bin/omarchy-iso-test
  [[ -f $f ]] || omarchy_dev_die "missing $f"

  if ! grep -qF 'OMARCHY_ISO_QEMU_EXTRA' "$f"; then
    python3 - "$f" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
old_start = "  qemu-system-x86_64 \\\n"
new_start = """  local omarchy_dev_qemu=()
  # shellcheck disable=SC2206
  [[ -n ${OMARCHY_ISO_QEMU_EXTRA:-} ]] && omarchy_dev_qemu=($OMARCHY_ISO_QEMU_EXTRA)

  qemu-system-x86_64 \\
"""
old_end = """    -daemonize \\
    \"$@\"
}"""
new_end = """    -daemonize \\
    \"${omarchy_dev_qemu[@]}\" \\
    \"$@\"
}"""
if old_start not in text or old_end not in text:
    sys.stderr.write(f"omarchy-dev: cannot hook QEMU_EXTRA in {path}: markers not found\n")
    sys.exit(1)
path.write_text(text.replace(old_start, new_start, 1).replace(old_end, new_end, 1))
print(f"omarchy-dev: hooked OMARCHY_ISO_QEMU_EXTRA into {path}", file=sys.stderr)
PY
  fi

  if ! grep -qF 'OMARCHY_ISO_ACTIVATE_SYNC' "$f"; then
    python3 - "$f" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
old = """  establish_session
  sync_omarchy

  local status=0
"""
new = r"""  establish_session
  sync_omarchy

  # omarchy-dev: when testing a worktree, make that tree the live Omarchy
  # (same idea as omarchy-dev-link). Desktop/menu then use the synced code.
  if $SYNC_ALL && [[ ${OMARCHY_ISO_ACTIVATE_SYNC:-0} == 1 ]]; then
    local sync_abs="/home/$GUEST_USER/.local/share/omarchy"
    log "Activating synced tree as live Omarchy → $sync_abs"
    ssh_guest "echo $GUEST_PASSWORD | sudo -S -p '' sh -c \"
      printf '%s\\n' 'export OMARCHY_PATH=$sync_abs' > /etc/omarchy.conf &&
      printf '%s\\n' 'Defaults secure_path=\\\"$sync_abs/bin:/usr/local/sbin:/usr/local/bin:/usr/bin\\\"' > /etc/sudoers.d/omarchy-dev-path &&
      chmod 440 /etc/sudoers.d/omarchy-dev-path
    \""
    log "Rebooting guest so the session picks up OMARCHY_PATH"
    ssh_guest "echo $GUEST_PASSWORD | sudo -S reboot" >/dev/null 2>&1 || true
    sleep 8
    establish_session
  fi

  # omarchy-dev: interactive default — desktop ready, no automated checks.
  if [[ ${OMARCHY_ISO_SKIP_TESTS:-0} == 1 ]]; then
    # Acceptance base ships with offline.db only; installs need a real -Sy.
    log "Refreshing pacman databases for interactive use"
    if ! ssh_guest "echo $GUEST_PASSWORD | sudo -S -p '' pacman -Sy"; then
      log "WARNING: pacman -Sy failed — package installs (e.g. grok-bot) will not work until it succeeds"
    fi
    log "Interactive session ready (automated checks skipped). Leaving VM running."
    KEEP_RUNNING=true
    return 0
  fi

  local status=0
"""
if old not in text:
    sys.stderr.write(f"omarchy-dev: cannot hook ACTIVATE_SYNC in {path}: markers not found\n")
    sys.exit(1)
path.write_text(text.replace(old, new, 1))
print(f"omarchy-dev: hooked OMARCHY_ISO_ACTIVATE_SYNC into {path}", file=sys.stderr)
PY
  fi

  if ! grep -qF 'keep .git when activating' "$f"; then
    python3 - "$f" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
old = """  if $SYNC_ALL; then
    log \"Syncing $SYNC_DIR into the guest\"
    tar -C \"$SYNC_DIR\" --exclude .git -cf - . | ssh_guest \"mkdir -p $target && tar -C $target -xf -\"
  else
"""
new = """  if $SYNC_ALL; then
    log \"Syncing $SYNC_DIR into the guest\"
    # omarchy-dev: keep .git when activating a materialized checkout (update-dev).
    if [[ ${OMARCHY_ISO_ACTIVATE_SYNC:-0} == 1 ]]; then
      tar -C \"$SYNC_DIR\" -cf - . | ssh_guest \"mkdir -p $target && tar -C $target -xf -\"
    else
      tar -C \"$SYNC_DIR\" --exclude .git -cf - . | ssh_guest \"mkdir -p $target && tar -C $target -xf -\"
    fi
  else
"""
if old not in text:
    sys.stderr.write(f"omarchy-dev: cannot hook sync .git keep in {path}: markers not found\n")
    sys.exit(1)
path.write_text(text.replace(old, new, 1))
print(f"omarchy-dev: hooked sync keep-.git into {path}", file=sys.stderr)
PY
  fi

  if ! grep -qF 'OMARCHY_ISO_SKIP_TESTS' "$f"; then
    python3 - "$f" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
# Prefer the activate-aware closing fi right before shortcut smoke.
if "    establish_session\n  fi\n\n  local status=0\n  shortcut_smoke_phase" in text:
    old = "    establish_session\n  fi\n\n  local status=0\n  shortcut_smoke_phase"
    new = """    establish_session
  fi

  # omarchy-dev: interactive default — desktop ready, no automated checks.
  if [[ ${OMARCHY_ISO_SKIP_TESTS:-0} == 1 ]]; then
    # Acceptance base ships with offline.db only; installs need a real -Sy.
    log "Refreshing pacman databases for interactive use"
    if ! ssh_guest "echo $GUEST_PASSWORD | sudo -S -p '' pacman -Sy"; then
      log "WARNING: pacman -Sy failed — package installs (e.g. grok-bot) will not work until it succeeds"
    fi
    log "Interactive session ready (automated checks skipped). Leaving VM running."
    KEEP_RUNNING=true
    return 0
  fi

  local status=0
  shortcut_smoke_phase"""
elif "  sync_omarchy\n\n  local status=0\n  shortcut_smoke_phase" in text:
    old = "  sync_omarchy\n\n  local status=0\n  shortcut_smoke_phase"
    new = """  sync_omarchy

  # omarchy-dev: interactive default — desktop ready, no automated checks.
  if [[ ${OMARCHY_ISO_SKIP_TESTS:-0} == 1 ]]; then
    # Acceptance base ships with offline.db only; installs need a real -Sy.
    log "Refreshing pacman databases for interactive use"
    if ! ssh_guest "echo $GUEST_PASSWORD | sudo -S -p '' pacman -Sy"; then
      log "WARNING: pacman -Sy failed — package installs (e.g. grok-bot) will not work until it succeeds"
    fi
    log "Interactive session ready (automated checks skipped). Leaving VM running."
    KEEP_RUNNING=true
    return 0
  fi

  local status=0
  shortcut_smoke_phase"""
else:
    sys.stderr.write(f"omarchy-dev: cannot hook SKIP_TESTS in {path}: markers not found\n")
    sys.exit(1)
path.write_text(text.replace(old, new, 1))
print(f"omarchy-dev: hooked OMARCHY_ISO_SKIP_TESTS into {path}", file=sys.stderr)
PY
  fi
}
