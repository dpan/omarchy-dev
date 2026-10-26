# Omarchy Dev Workspace

Local Omarchy dev/test environment: checkouts, worktrees, shared preinstalled base image, mise wrappers. Not the Omarchy product repo.

Humans: [`README.md`](README.md). Assume `cd` into the checkout; `OMARCHY_DEV_ROOT` comes from `mise.toml`.

## Keeping this file current

In the same change, update `AGENTS.md` and `README.md` when wrappers, `mise.toml`, or setup/test/rebase behavior change. No machine-specific absolute paths.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/#specification) for this meta repo.

## Layout

```text
. ← checkout root
  AGENTS.md / README.md / mise.toml / LICENSE
  patches/omarchy-iso-test.patch
  bin/
    _lib.sh
    omarchy-dev-setup | -doctor | -test | -rebase | -worktree
  omarchy/                  ← main checkout (gitignored)
  omarchy-iso/              ← harness, release/, test-runs/ (gitignored)
  worktrees/<name>/
  tests/test_regressions.py
```

| Path | Role |
|------|------|
| `omarchy/` | Base-branch hub for `git worktree` |
| `worktrees/<name>/` | Feature work on `feature/<name>` |

## Commands

Prefer mise tasks; `bin/` wrappers are what those tasks exec. The minimum supported mise version is the verified 2026.10.19. Tasks forward raw arguments to wrappers, including help. Usage declarations provide completion metadata; wrappers own parsing.

Mise consumes its first `--`. The wrapper forwards the suffix from the first harness option. Use two consecutive separators to pass a literal wrapper boundary, for example `mise test <tree> -- -- --keep-running`.

| Task | Role |
|------|------|
| `mise setup` | packages, clones, ISO, ensure base image |
| `mise health` | host tools, clones, harness patch, ISO, base image (`mise doctor` is a mise builtin) |
| `mise worktree -- add\|list\|remove …` | feature worktrees under `worktrees/` |
| `mise test` | boot interactive stock VM (left running) |
| `mise test <tree>` | boot VM; worktree is live Omarchy |
| `mise test --host-config [tree]` | also sync selected host `~/.config` (this overlay only) |
| `mise test --run-tests [tree]` | run automated checks, then stop |
| `mise rebase list` | print available ISOs (no rebuild) |
| `mise rebase` | gum pick → rebuild shared base |
| `mise rebase <target>` | `edge` / `stable` / `rc` / version / ISO name / path → rebuild |
| `mise rebase status` | show active ISO/base image |

```bash
mise setup
mise rebase list
mise rebase status
mise worktree -- add my-feature-worktree quattro
mise test my-feature-worktree
mise test --run-tests my-feature-worktree
mise test --run-tests my-feature-worktree -- --keep-running
mise test --ssh --no-grab-kbd -- --memory 4096
mise rebase
mise rebase stable
```

`setup` prepares clones, verifies the harness patch, resolves the channel ISO once, and passes that exact path to base preparation. Flags: `--channel edge|stable`, `--no-iso`, `--no-base`, `--no-packages`. With `--no-iso`, base preparation uses a local selection and requires one to exist. Combine `--no-iso --no-base` for checkouts only. `_lib.sh` owns the shared ISO and base invariants.

### Harness patch

`patches/omarchy-iso-test.patch` extends local `omarchy-iso/bin/omarchy-iso-test` with QEMU arguments, activation, interactive sessions, and host-config sync. Readiness uses `git apply --reverse --check` against the tracked patch. Apply forward only when clean. If neither direction applies, stop and preserve local edits. Health checks readiness without applying anything.

After upstream updates, port the required changes onto the new harness and reconcile local edits before regenerating the tracked patch:

```bash
(cd omarchy-iso && git diff --src-prefix=a/ --dst-prefix=b/ HEAD -- bin/omarchy-iso-test) \
  > patches/omarchy-iso-test.patch
```

Do not regenerate from a clean harness to resolve an apply failure; that would discard the required patch. `OMARCHY_ISO_QEMU_EXTRA` is a single line of whitespace-delimited arguments. Shell quoting and wildcard expansion are not part of that format.

### Base image

One shared `omarchy-iso/test-runs/<iso-stem>/base.qcow2` for stock and all worktrees. `test-runs/CURRENT` stores the canonical ISO path; legacy basenames under `release/` are still readable. A present selection is authoritative. Missing selections may be discovered without writing CURRENT. Only successful base preparation persists selection, atomically.

`base.complete` beside the disk records the canonical ISO path and ISO/disk stat fingerprints. The wrapper invalidates completion before rebuilding, verifies a standalone qcow2 image and checks consistency after a successful installer exit, then atomically records completion and selection. Readiness requires matching evidence, a valid qcow2 header, and readable nonempty `OVMF_VARS.4m.fd` and `id_ed25519` files beside the disk. Firmware variables are mutable and are not fingerprinted. Existing unverified disks require `mise rebase <target>`; setup and test refuse them rather than guessing from size. Replacing an ISO or disk invalidates its completion evidence. External ISO paths are supported but must remain available.

ISO sources: edge/stable `latest.json`; rc from GitHub prerelease links on `iso.omarchy.org` (`rebase list`).

### `test` session

Default: interactive desktop (QEMU window + keyboard grab; VM left running). Automated checks only with `--run-tests`. Flags:

| Flag | Effect |
|------|--------|
| `--run-tests` | Run automated checks, then stop the VM |
| `--host-config` | Tar allowlisted host `~/.config/*` into guest (interactive only; not persisted) |
| `--ssh` | No window (grab still on unless disabled) |
| `--no-grab-kbd` | Do not grab the host keyboard |
| `--grab-kbd` / `--graphical` | Explicit defaults |

`--host-config`: dirs from `OMARCHY_DEV_HOST_CONFIG_DIRS` (default `omarchy hypr alacritty ghostty kitty foot walker`); skips `hypr/monitors.conf` and `hypr/monitors.lua`. Ignored with `--run-tests`. Enabled by the harness patch (`OMARCHY_DEV_SYNC_HOST_CONFIG`).

With a tree: resolve the source before changing directory, materialize an exact snapshot with standalone Git metadata, sync it **with** `.git` to `~/.local/share/omarchy`, write `/etc/omarchy.conf`, and reboot. Git worktree pointers cannot be shipped, so the snapshot has its own object storage. Dirty deletions, additions, symlinks, and modes are preserved; the temporary snapshot is removed on wrapper exit. Activated sync clears old guest contents first. Both the desktop and acceptance suite use that guest path. Required preparation failures return nonzero even inside the harness's conditional phase call.

Interactive boots also run `pacman -Sy` because the base ships `offline.db` only. Refresh failure is a warning. Requested host-config transfer failure stops the run. Grab uses `grab_all=on`, `repeat=off`; Left+Right Ctrl toggles. The wrapper does not promise isolation from other GTK input devices.

## Worktrees

| Goal | Command |
|------|---------|
| Add | `mise worktree -- add my-feature-worktree quattro` |
| List / remove | `mise worktree -- list` / `mise worktree -- remove my-feature-worktree` |

Host git only; does not start a VM. Use `mise test -- <name>` to exercise a tree. Commit inside the worktree to Omarchy remotes as usual.

## Wrappers

| Command | Role |
|---------|------|
| `omarchy-dev-setup` | Full prepare |
| `omarchy-dev-doctor` | Readiness checks |
| `omarchy-dev-test` | Interactive VM (or `--run-tests` checks) |
| `omarchy-dev-rebase` | rebase / list / status |
| `omarchy-dev-worktree` | add / list / remove |

Overrides: `OMARCHY_DEV_OMARCHY`, `OMARCHY_DEV_ISO`, `OMARCHY_DEV_VMS`, `OMARCHY_DEV_WORKTREES`.

## Dependencies

`mise.toml` `[bootstrap.packages]`; applied by setup or `mise bootstrap packages apply`.

## Regression checks

Run `python -m unittest discover -s tests -v`. Tests use temporary Git repositories, tiny real qcow2 files, and inert VM/network boundaries. Dependencies are Python 3, Git, jq, qemu-img, and mise. Harness cases apply the tracked patch to a temporary copy of the local `omarchy-iso` HEAD and skip when that checkout is absent. This suite does not boot a VM or alter host configuration. Use `mise test --run-tests <tree>` separately for live guest checks.

## What not to do

- Do not put `bin/` on `PATH` in shell rc; use mise.  
- Do not symlink `omarchy/` / `omarchy-iso/` outside this checkout.  
- Do not do feature day-to-day work on `omarchy/` when a worktree exists.  
- Do not document a fixed clone path here.

## Refreshing

```bash
git -C omarchy-iso pull
mise setup --no-iso --no-base --no-packages
mise rebase status
```

If the patch check reports stale or conflicting contents, reconcile the harness diff before retrying setup. Rebuild explicitly when completion evidence is absent or invalid.
