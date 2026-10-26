# Omarchy Dev Workspace

Umbrella for Omarchy feature work: checkouts, worktrees, shared preinstalled base image, thin wrappers. Clone this repo and use mise. Not the Omarchy product repo.

Humans: [`README.md`](README.md). Assume `cd` into the checkout; `OMARCHY_DEV_ROOT` comes from `mise.toml`.

## Keeping this file current

In the same change, update `AGENTS.md` and `README.md` when wrappers, `mise.toml`, or setup/test/rebase behavior change. No machine-specific absolute paths.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/#specification) for this meta repo.

## Layout

```text
. ← checkout root
  AGENTS.md / README.md / mise.toml
  bin/
    _lib.sh
    _omarchy-dev-fetch      ← internal (setup only)
    omarchy-dev-setup | -test | -rebase | -worktree
  omarchy/                  ← main checkout (gitignored)
  omarchy-iso/              ← harness, release/, test-runs/ (gitignored)
  worktrees/<name>/
```

| Path | Role |
|------|------|
| `omarchy/` | Base-branch hub for `git worktree` |
| `worktrees/<name>/` | Feature work on `feature/<name>` |

## Commands

Prefer mise tasks; `bin/` wrappers are what those tasks exec.

| Task | Role |
|------|------|
| `mise setup` | packages, clones, ISO, ensure base image |
| `mise worktree -- add\|list\|remove …` | feature worktrees under `worktrees/` |
| `mise test` | boot interactive stock VM (left running) |
| `mise test -- <tree>` | boot VM; worktree is live Omarchy |
| `mise test -- --run-tests [tree]` | run automated checks, then stop |
| `mise rebase` | rebuild base image from latest edge |
| `mise rebase list` | pick edge/stable/rc/local → rebuild |
| `mise rebase status` | show active ISO/base image |

```bash
mise setup
mise rebase status
mise worktree -- add my-feature quattro
mise test -- my-feature
mise test -- --run-tests my-feature
mise rebase
mise rebase list
```

`setup` → `omarchy-dev-setup` → `_omarchy-dev-fetch` → `omarchy_dev_ensure_base`. Flags: `--channel edge|stable`, `--no-iso`, `--no-base`, `--no-packages`.

### Base image

One shared `omarchy-iso/test-runs/<iso-stem>/base.qcow2` (preinstalled guest disk) for stock and all worktrees. Active ISO: `test-runs/CURRENT`. Install/download helpers live only in `_lib.sh`. `test` refuses an empty/incomplete base.

ISO sources: edge/stable `latest.json`; rc from GitHub prerelease links on `iso.omarchy.org` (`rebase list`).

### `test` session

Default: interactive desktop (QEMU window + keyboard grab; VM left running). Automated checks only with `--run-tests`. Flags:

| Flag | Effect |
|------|--------|
| `--run-tests` | Run automated checks, then stop the VM |
| `--ssh` | No window (grab still on unless disabled) |
| `--no-grab-kbd` | Do not grab the host keyboard |
| `--grab-kbd` / `--graphical` | Explicit defaults |

With a tree: materialize a standalone git clone (worktree `.git` pointers can’t ship), sync it **with** `.git` to `~/.local/share/omarchy`, write `/etc/omarchy.conf` (dev-link style), reboot — desktop uses the worktree. Interactive boots also run `pacman -Sy` (base image ships `offline.db` only). Grab: `grab_all=on`, `repeat=off`; Left+Right Ctrl toggles.

## Worktrees

| Goal | Command |
|------|---------|
| Add | `mise worktree -- add my-feature quattro` |
| List / remove | `mise worktree -- list` / `mise worktree -- remove my-feature` |

Host git only; does not start a VM. Use `mise test -- <name>` to exercise a tree. Commit inside the worktree to Omarchy remotes as usual.

## Wrappers

| Command | Role |
|---------|------|
| `omarchy-dev-setup` | Full prepare |
| `omarchy-dev-test` | Interactive VM (or `--run-tests` checks) |
| `omarchy-dev-rebase` | rebase / list / status |
| `omarchy-dev-worktree` | add / list / remove |
| `_omarchy-dev-fetch` | Internal ISO helper |

Overrides: `OMARCHY_DEV_OMARCHY`, `OMARCHY_DEV_ISO`, `OMARCHY_DEV_VMS`, `OMARCHY_DEV_WORKTREES`.

## Dependencies

`mise.toml` `[bootstrap.packages]`; applied by setup or `mise bootstrap packages apply`.

## What not to do

- Do not put `bin/` on `PATH` in shell rc; use mise.  
- Do not symlink `omarchy/` / `omarchy-iso/` outside this checkout.  
- Do not do feature day-to-day work on `omarchy/` when a worktree exists.  
- Do not document a fixed clone path here.

## Refreshing

```bash
git -C omarchy-iso pull
mise setup
mise rebase status
```
