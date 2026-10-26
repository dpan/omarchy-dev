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

# Copy source contents with independent Git metadata for guest sync.
omarchy_dev_materialize_sync_tree() (
  set -euo pipefail
  local src=$1 dest=${2:-} url

  [[ -d $src ]] || omarchy_dev_die "materialize: not a directory: $src"
  src=$(omarchy_dev_abs "$src") || return 1
  git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
    omarchy_dev_die "materialize: not a git checkout: $src"

  if [[ -z $dest ]]; then
    mkdir -p "$OMARCHY_DEV_ISO/test-runs" || return 1
    dest=$(mktemp -d "$OMARCHY_DEV_ISO/test-runs/.sync-tree.XXXXXX") || return 1
  else
    dest=$(realpath -m -- "$dest") || return 1
    [[ $src != "$dest" && $src != "$dest/"* && $dest != "$src/"* ]] ||
      omarchy_dev_die "materialize: destination overlaps source: $dest"
    rm -rf -- "$dest" || return 1
    mkdir -p "$(dirname "$dest")" || return 1
  fi
  trap 'rm -rf -- "$dest"' EXIT

  git clone --no-checkout --no-local "$src" "$dest" >&2 || return 1
  git -C "$dest" read-tree HEAD || return 1
  tar -C "$src" --exclude=./.git -cf - . | tar -C "$dest" -xf - || return 1

  if url=$(git -C "$src" remote get-url origin 2>/dev/null); then
    git -C "$dest" remote set-url origin "$url" || return 1
  fi
  git -C "$dest" branch --unset-upstream >/dev/null 2>&1 || true

  trap - EXIT
  printf '%s\n' "$dest"
)

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

# Default-options base disk path for an ISO (matches omarchy-iso-test BASE_NAME).
omarchy_dev_base_disk_for_iso() {
  local iso=$1
  printf '%s\n' "$OMARCHY_DEV_ISO/test-runs/$(basename "$iso" .iso)/base.qcow2"
}

omarchy_dev_base_fingerprint() {
  local iso=$1 disk=$2
  printf 'omarchy-dev-base-v1\n%s\n' "$iso"
  stat -c '%d:%i:%s:%y:%z' -- "$iso" "$disk"
}

omarchy_dev_base_files_are_valid() {
  local disk=$1 info
  [[ -r ${disk%/*}/OVMF_VARS.4m.fd && -s ${disk%/*}/OVMF_VARS.4m.fd &&
    -r ${disk%/*}/id_ed25519 && -s ${disk%/*}/id_ed25519 ]] || return 1
  info=$(qemu-img info --output=json "$disk" 2>/dev/null) || return 1
  jq -e '.format == "qcow2" and (has("backing-filename") | not)' <<<"$info" >/dev/null
}

omarchy_dev_base_is_usable() {
  local iso disk marker expected
  iso=$(realpath -e -- "$1") || return 1
  disk=$(omarchy_dev_base_disk_for_iso "$iso")
  marker=${disk%/*}/base.complete
  [[ -f $iso && -f $disk && -f $marker ]] || return 1
  expected=$(omarchy_dev_base_fingerprint "$iso" "$disk") || return 1
  [[ $(<"$marker") == "$expected" ]] || return 1
  omarchy_dev_base_files_are_valid "$disk"
}

omarchy_dev_set_current_iso() {
  local iso temp
  iso=$(realpath -e -- "$1") || return 1
  [[ -f $iso ]] || return 1
  mkdir -p "$(dirname "$OMARCHY_DEV_CURRENT_ISO_FILE")" || return 1
  temp=$(mktemp "$OMARCHY_DEV_CURRENT_ISO_FILE.XXXXXX") || return 1
  if ! printf '%s\n' "$iso" >"$temp" || ! mv -fT -- "$temp" "$OMARCHY_DEV_CURRENT_ISO_FILE"; then
    rm -f -- "$temp"
    return 1
  fi
}

# CURRENT is authoritative when present. Discovery never changes selection.
omarchy_dev_current_iso() {
  local selection iso disk latest_disk= latest_iso=

  if [[ -e $OMARCHY_DEV_CURRENT_ISO_FILE || -L $OMARCHY_DEV_CURRENT_ISO_FILE ]]; then
    [[ -f $OMARCHY_DEV_CURRENT_ISO_FILE && -r $OMARCHY_DEV_CURRENT_ISO_FILE ]] ||
      omarchy_dev_die "Invalid CURRENT selection at $OMARCHY_DEV_CURRENT_ISO_FILE"
    selection=$(<"$OMARCHY_DEV_CURRENT_ISO_FILE")
    case "$selection" in
      /*) iso=$selection ;;
      '' | */*) omarchy_dev_die "Invalid CURRENT selection at $OMARCHY_DEV_CURRENT_ISO_FILE" ;;
      *) iso=$OMARCHY_DEV_ISO/release/$selection ;;
    esac
    [[ -f $iso ]] || omarchy_dev_die "Selected ISO is missing: $iso (run: mise rebase <target>)"
    realpath -e -- "$iso"
    return
  fi

  for iso in "$OMARCHY_DEV_ISO"/release/*.iso; do
    [[ -f $iso ]] || continue
    omarchy_dev_base_is_usable "$iso" || continue
    disk=$(omarchy_dev_base_disk_for_iso "$iso")
    if [[ -z $latest_disk || $disk -nt $latest_disk ]]; then
      latest_disk=$disk
      latest_iso=$iso
    fi
  done
  if [[ -n $latest_iso ]]; then
    realpath -e -- "$latest_iso"
  else
    iso=$(omarchy_dev_find_latest_iso) || return 1
    realpath -e -- "$iso"
  fi
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
  if ! iso=$(omarchy_dev_current_iso); then
    [[ ! -e $OMARCHY_DEV_CURRENT_ISO_FILE && ! -L $OMARCHY_DEV_CURRENT_ISO_FILE ]] || return 1
    echo "No base image yet. Run: mise rebase"
    echo "Or: mise rebase list   # then: mise rebase <target>"
    return 0
  fi
  name=$(basename "$iso")
  disk=$(omarchy_dev_base_disk_for_iso "$iso")
  channel=$(omarchy_dev_guess_channel "$name")
  if omarchy_dev_base_is_usable "$iso"; then
    state=usable
  elif [[ -f $disk ]]; then
    state="incomplete ($(stat -c%s "$disk") bytes)"
  else
    state=missing
  fi
  bytes=$(stat -c%s "$disk" 2>/dev/null || echo 0)
  mtime=$(stat -c%y "$disk" 2>/dev/null | cut -d. -f1 || echo unknown)
  cat <<EOF
Base image (shared by stock + all worktrees)
  ISO:     $name
  Channel: $channel
  Path:    $iso
  Base:    $disk
  State:   $state
  Size:    $((bytes / 1024 / 1024)) MiB
  Mtime:   $mtime
EOF
}

# Verify downloads before replacing any existing ISO or checksum.
omarchy_dev_fetch_file() (
  set -euo pipefail
  local url=$1 dest=$2 sum_url=$3 partial sum_file expected actual
  mkdir -p "$(dirname "$dest")" || return 1
  partial=$(mktemp "$dest.partial.XXXXXX") || return 1
  sum_file=
  trap 'rm -f -- "$partial" "$sum_file"' EXIT
  sum_file=$(mktemp "$dest.sha256.XXXXXX") || return 1
  curl -fsSL -o "$sum_file" "$sum_url" || return 1
  read -r expected _ <"$sum_file" || [[ -n $expected ]] || return 1
  [[ $expected =~ ^[[:xdigit:]]{64}$ ]] || omarchy_dev_die "Invalid SHA256 checksum from $sum_url"
  expected=${expected,,}

  if [[ -f $dest ]]; then
    actual=$(sha256sum -- "$dest") || return 1
    if [[ ${actual%% *} == "$expected" ]]; then
      echo "Already present (checksum OK): $dest" >&2
      mv -fT -- "$sum_file" "$dest.sha256" || return 1
      return 0
    fi
    echo "Checksum mismatch for $dest; downloading replacement" >&2
  fi

  echo "Downloading $dest" >&2
  curl -fL --progress-bar -o "$partial" "$url" || return 1
  actual=$(sha256sum -- "$partial") || return 1
  [[ ${actual%% *} == "$expected" ]] || omarchy_dev_die "Checksum mismatch for download: $url"
  mv -fT -- "$partial" "$dest" || return 1
  mv -fT -- "$sum_file" "$dest.sha256" || return 1
)

omarchy_dev_channel_url() {
  case "${1:-edge}" in
    edge) printf '%s\n' "https://nightly.omarchy.org/edge/latest.json" ;;
    stable) printf '%s\n' "https://nightly.omarchy.org/stable/latest.json" ;;
    *) omarchy_dev_die "Unknown channel: $1 (use edge or stable; rc is versioned on iso.omarchy.org)" ;;
  esac
}

# Ensure channel ISO is in release/; print its path. Channels: edge | stable.
omarchy_dev_ensure_channel_iso() {
  local channel=${1:-edge} meta iso_url iso_name dest meta_url need avail
  command -v jq >/dev/null || omarchy_dev_die "jq is required"
  command -v curl >/dev/null || omarchy_dev_die "curl is required"
  omarchy_dev_require_iso_repo
  mkdir -p "$OMARCHY_DEV_ISO/release"
  meta_url=$(omarchy_dev_channel_url "$channel") || return 1
  meta=$(curl -fsSL "$meta_url") || return 1
  iso_url=$(jq -er '.url | select(type == "string" and length > 0)' <<<"$meta") || return 1
  iso_name=${iso_url##*/}
  dest=$OMARCHY_DEV_ISO/release/$iso_name
  need=$(jq -r '.size // 0' <<<"$meta") || return 1
  if [[ $need =~ ^[0-9]+$ ]] && ((need > 0)); then
    avail=$(df -B1 --output=avail "$OMARCHY_DEV_ISO/release" | tail -n1) || return 1
    [[ $avail =~ ^[[:space:]]*[0-9]+[[:space:]]*$ ]] || omarchy_dev_die "Could not read available disk space"
    ((avail >= need + 2 * 1024 * 1024 * 1024)) ||
      omarchy_dev_die "Not enough free disk for ISO download and 2 GiB headroom"
  fi
  echo "Channel $channel → $(jq -r .version <<<"$meta") ($iso_name)" >&2
  omarchy_dev_fetch_file "$iso_url" "$dest" "$iso_url.sha256" || return 1
  realpath -e -- "$dest"
}

# Fetch a release/RC ISO from iso.omarchy.org (or any URL) into release/.
omarchy_dev_ensure_iso_url() {
  local url=$1 dest name
  name=${url##*/}
  dest=$OMARCHY_DEV_ISO/release/$name
  omarchy_dev_require_iso_repo
  mkdir -p "$OMARCHY_DEV_ISO/release"
  omarchy_dev_fetch_file "$url" "$dest" "$url.sha256" || return 1
  realpath -e -- "$dest"
}

# Lines: kind<TAB>version<TAB>url<TAB>local_path_or_-
# kind is edge|stable|rc|local
omarchy_dev_list_iso_candidates() {
  local meta url name ver dest local_path seen="" f channel rels
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

# Publish completion and selection only after a successful, validated install.
omarchy_dev_build_base() {
  local iso disk marker temp
  [[ -n ${1:-} ]] || omarchy_dev_die "build_base requires an explicit ISO"
  iso=$(realpath -e -- "$1") || return 1
  [[ -f $iso ]] || omarchy_dev_die "ISO not found: $iso"
  omarchy_dev_require_iso_repo
  disk=$(omarchy_dev_base_disk_for_iso "$iso")
  marker=${disk%/*}/base.complete
  rm -f -- "$marker" || return 1

  echo "Building base image from $(basename "$iso")" >&2
  (builtin cd "$OMARCHY_DEV_ISO" && ./bin/omarchy-iso-test "$iso" --install-only --no-preview) || return
  omarchy_dev_base_files_are_valid "$disk" || {
    echo "Invalid base image or missing firmware/SSH key beside $disk" >&2
    return 1
  }
  qemu-img check -f qcow2 "$disk" >&2 || return 1
  temp=$(mktemp "$marker.XXXXXX") || return 1
  if ! omarchy_dev_base_fingerprint "$iso" "$disk" >"$temp" || ! mv -fT -- "$temp" "$marker"; then
    rm -f -- "$temp"
    return 1
  fi
  omarchy_dev_set_current_iso "$iso"
}

# Resolve a candidate row (kind, ver, url, local_path) to a local ISO path.
omarchy_dev_iso_from_candidate() {
  local kind=$1 ver=$2 url=$3 local_path=$4
  if [[ $local_path != - && -f $local_path ]]; then
    realpath -e -- "$local_path"
    return
  fi
  if [[ $url == file://* ]]; then
    realpath -e -- "${url#file://}"
    return
  fi
  if [[ -n $url && $url != - ]]; then
    omarchy_dev_ensure_iso_url "$url"
    return
  fi
  omarchy_dev_die "No path/URL for ISO candidate: $kind $ver"
}

# Print available ISOs (no rebuild). Columns: KIND VERSION WHERE NAME
omarchy_dev_rebase_list() {
  local kind ver url local_path where name
  local any=false

  printf '%-7s %-28s %-6s %s\n' KIND VERSION WHERE FILE
  while IFS=$'\t' read -r kind ver url local_path; do
    [[ -n $kind ]] || continue
    any=true
    if [[ $local_path != - ]]; then
      where=local
      name=$(basename "$local_path")
    else
      where=remote
      name=${url##*/}
    fi
    printf '%-7s %-28s %-6s %s\n' "$kind" "$ver" "$where" "$name"
  done < <(omarchy_dev_list_iso_candidates)

  $any || omarchy_dev_die "No ISOs found (network or omarchy-iso/release/)"
}

# Resolve target → ISO path: edge|stable, version, basename, or filesystem path.
omarchy_dev_resolve_rebase_target() {
  local target=$1 kind ver url local_path name row best=
  local -a matches=()
  [[ -n $target ]] || omarchy_dev_die "empty rebase target"
  if [[ -f $target ]]; then
    realpath -e -- "$target"
    return
  fi
  case "$target" in
    edge | stable)
      omarchy_dev_ensure_channel_iso "$target"
      return
      ;;
    rc)
      while IFS= read -r row; do
        [[ $row == rc$'\t'* ]] && matches+=("$row")
      done < <(omarchy_dev_list_iso_candidates)
      ((${#matches[@]})) || omarchy_dev_die "No rc ISOs found; try: mise rebase list"
      best=$(printf '%s\n' "${matches[@]}" | sort -t $'\t' -k2,2V | tail -n1)
      IFS=$'\t' read -r kind ver url local_path <<<"$best"
      omarchy_dev_iso_from_candidate "$kind" "$ver" "$url" "$local_path"
      return
      ;;
  esac

  name=${target##*/}
  name=${name%.iso}
  while IFS= read -r row; do
    IFS=$'\t' read -r kind ver url local_path <<<"$row"
    [[ -n $kind ]] || continue
    if [[ $ver == "$target" || $ver == "$name" || $kind/$ver == "$target" ||
      ($local_path != - && (${local_path##*/} == "$target" || ${local_path##*/} == "$name.iso")) ||
      ($local_path == - && (${url##*/} == "$target" || ${url##*/} == "$name.iso")) ]]; then
      matches+=("$row")
    fi
  done < <(omarchy_dev_list_iso_candidates)

  ((${#matches[@]})) || omarchy_dev_die "No ISO matches '$target'; try: mise rebase list"
  if ((${#matches[@]} > 1)); then
    echo "Ambiguous target '$target' (matches ${#matches[@]}):" >&2
    for row in "${matches[@]}"; do
      IFS=$'\t' read -r kind ver url local_path <<<"$row"
      echo "  $kind  $ver" >&2
    done
    omarchy_dev_die "Pick a unique version from: mise rebase list"
  fi
  IFS=$'\t' read -r kind ver url local_path <<<"${matches[0]}"
  omarchy_dev_iso_from_candidate "$kind" "$ver" "$url" "$local_path"
}

omarchy_dev_rebase_pick() {
  local -a labels=() rows=()
  local row kind ver url local_path label pick i iso
  while IFS= read -r row; do
    IFS=$'\t' read -r kind ver url local_path <<<"$row"
    [[ -n $kind ]] || continue
    if [[ $local_path != - ]]; then
      label=$(printf '%-7s %-28s local  %s' "$kind" "$ver" "${local_path##*/}")
    else
      label=$(printf '%-7s %-28s remote %s' "$kind" "$ver" "${url##*/}")
    fi
    labels+=("$label")
    rows+=("$row")
  done < <(omarchy_dev_list_iso_candidates)
  ((${#rows[@]})) || omarchy_dev_die "No ISOs found (network or omarchy-iso/release/)"
  command -v gum >/dev/null 2>&1 || omarchy_dev_die "Install gum, or: mise rebase <edge|stable|rc|version>"
  pick=$(printf '%s\n' "${labels[@]}" | gum choose --header "ISO to rebase onto (shared base for stock + all worktrees)") || return 1
  [[ -n $pick ]] || omarchy_dev_die "No ISO selected"
  for i in "${!labels[@]}"; do
    [[ ${labels[$i]} == "$pick" ]] || continue
    IFS=$'\t' read -r kind ver url local_path <<<"${rows[$i]}"
    iso=$(omarchy_dev_iso_from_candidate "$kind" "$ver" "$url" "$local_path") || return 1
    omarchy_dev_build_base "$iso"
    return
  done
  omarchy_dev_die "Internal select error"
}

# Rebuild base from an explicit target (or interactive pick if empty).
omarchy_dev_rebase() {
  local target=${1:-} iso
  if [[ -z $target ]]; then
    omarchy_dev_rebase_pick
    return
  fi
  iso=$(omarchy_dev_resolve_rebase_target "$target") || return 1
  omarchy_dev_build_base "$iso"
}

omarchy_dev_ensure_base() {
  local iso disk
  [[ -n ${1:-} ]] || omarchy_dev_die "ensure_base requires an explicit ISO"
  iso=$(realpath -e -- "$1") || return 1
  disk=$(omarchy_dev_base_disk_for_iso "$iso")
  if omarchy_dev_base_is_usable "$iso"; then
    omarchy_dev_set_current_iso "$iso" || return 1
    echo "Base image ready: $disk" >&2
    return 0
  fi
  if [[ -e $disk ]]; then
    omarchy_dev_die "Base image lacks valid completion evidence: $disk (run: mise rebase <target>)"
  fi
  omarchy_dev_build_base "$iso"
}

# Fail fast before --reuse-base boots an empty disk into the UEFI Boot Manager.
omarchy_dev_require_base() {
  local iso=${1:-} disk
  [[ -n $iso ]] || iso=$(omarchy_dev_current_iso) ||
    omarchy_dev_die "No base image — run: mise rebase"
  disk=$(omarchy_dev_base_disk_for_iso "$iso")
  if omarchy_dev_base_is_usable "$iso"; then
    printf '%s\n' "$iso"
    return 0
  fi
  omarchy_dev_die "No usable base image at $disk — run: mise rebase"
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
  if [[ -n ${OMARCHY_DEV_SESSION_SYNC_TREE:-} ]]; then
    rm -rf -- "$OMARCHY_DEV_SESSION_SYNC_TREE"
    OMARCHY_DEV_SESSION_SYNC_TREE=
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
    OMARCHY_DEV_SESSION_GRAB_PATH=$(omarchy_dev_resolve_grab_keyboard) || return 1
    echo "$prefix: grabbing $OMARCHY_DEV_SESSION_GRAB_PATH (starts ON → guest)." >&2
    echo "$prefix: toggle host/guest: hold Left-Ctrl + Right-Ctrl, then release." >&2
    omarchy_dev_acl_allow_kbd "$OMARCHY_DEV_SESSION_GRAB_PATH" || return 1
    # repeat=off: avoid fighting the guest's own key repeat.
    extra+=" -object input-linux,id=omarchy-dev-kbd,evdev=$OMARCHY_DEV_SESSION_GRAB_PATH,grab_all=on,repeat=off,grab-toggle=ctrl-ctrl"
  fi

  extra=${extra# }
  [[ -z $extra ]] && return 0

  export QEMU_EXTRA="${QEMU_EXTRA:+$QEMU_EXTRA }$extra"
  export OMARCHY_ISO_QEMU_EXTRA="${OMARCHY_ISO_QEMU_EXTRA:+$OMARCHY_ISO_QEMU_EXTRA }$extra"
}

# Tracked patch that extends local omarchy-iso-test (QEMU_EXTRA, activate sync,
# interactive skip-tests, host-config). Refresh with:
#   (cd omarchy-iso && git diff --src-prefix=a/ --dst-prefix=b/ HEAD -- bin/omarchy-iso-test) \
#     > patches/omarchy-iso-test.patch
OMARCHY_DEV_ISO_HARNESS_PATCH="${OMARCHY_DEV_ISO_HARNESS_PATCH:-$OMARCHY_DEV_ROOT/patches/omarchy-iso-test.patch}"

omarchy_dev_harness_is_patched() {
  [[ -f $OMARCHY_DEV_ISO_HARNESS_PATCH ]] || return 1
  (builtin cd "$OMARCHY_DEV_ISO" && git apply --reverse --check "$OMARCHY_DEV_ISO_HARNESS_PATCH") >/dev/null 2>&1
}

omarchy_dev_ensure_iso_harness_patch() {
  local patch=$OMARCHY_DEV_ISO_HARNESS_PATCH
  [[ -f $OMARCHY_DEV_ISO/bin/omarchy-iso-test ]] || omarchy_dev_die "missing ISO harness; run: mise setup"
  [[ -f $patch ]] || omarchy_dev_die "missing harness patch: $patch"
  omarchy_dev_harness_is_patched && return 0
  if ! (builtin cd "$OMARCHY_DEV_ISO" && git apply --check "$patch") >/dev/null 2>&1; then
    omarchy_dev_die "Harness patch conflicts with local contents; preserve local edits and refresh patches/omarchy-iso-test.patch"
  fi
  (builtin cd "$OMARCHY_DEV_ISO" && git apply "$patch") || return 1
  echo "omarchy-dev: applied $(basename "$patch") to ISO harness" >&2
}
