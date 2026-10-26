# Omarchy Dev

A local dev and test environment for [Omarchy](https://omarchy.org).

This workspace contains checkouts of Omarchy and its ISO test scripts, and provides mise tasks to:

- keep feature work in git worktrees
- install Omarchy once into a shared VM base image
- boot that image with stock Omarchy, or with your feature tree as the live system
- optionally copy selected host desktop config into the guest for that run

Clone anywhere, then `cd` into the checkout. Omarchy already activates mise. Use mise 2026.10.19 or newer.

## Quick start

```bash
mise trust                              # once per machine/path, if prompted
mise setup                              # packages, clones, ISO, base image
mise health                             # host + workspace readiness
mise worktree -- add my-feature-worktree quattro

mise rebase list                        # ISOs you can rebase onto
mise rebase status                      # active ISO / base image
mise rebase                             # pick an ISO (gum), rebuild base
mise rebase stable                      # or: edge | rc | version | ISO name
mise test my-feature-worktree                    # desktop VM; worktree is live Omarchy
mise test                               # same VM, stock Omarchy
mise test --host-config                 # stock VM + selected host ~/.config
mise test --host-config my-feature-worktree      # worktree + host config
mise test --run-tests my-feature-worktree        # automated checks, then stop
mise test --run-tests my-feature-worktree -- --keep-running
mise test --ssh --no-grab-kbd -- --memory 4096   # stock guest, custom harness options
```

| Task | Meaning |
|------|---------|
| `mise setup` | Prepare workspace (ISO + base image if missing) |
| `mise health` | Check host tools, clones, harness patch, ISO, base image |
| `mise worktree -- add\|list\|remove …` | Feature worktrees under `worktrees/` |
| `mise test` | Boot interactive VM (stock Omarchy), leave it running |
| `mise test <tree>` | Boot VM with that worktree as live Omarchy |
| `mise test --host-config [tree]` | Also copy selected host `~/.config` dirs (this run only) |
| `mise test --run-tests [tree]` | Run automated checks, then stop |
| `mise rebase list` | Print available ISOs (edge / stable / rc / local) |
| `mise rebase` | Interactive pick, then rebuild the shared base image |
| `mise rebase <target>` | Rebuild from `edge`, `stable`, `rc`, a version, ISO name, or path |
| `mise rebase status` | Show active ISO + base image |

`test` opens a QEMU window and grabs the keyboard by default (Super in the guest; Left+Right Ctrl toggles grab). Also: `--ssh` (no window), `--no-grab-kbd`.

`--host-config` copies an allowlist from the host into the throwaway guest (default: `omarchy`, `hypr`, `alacritty`, `ghostty`, `kitty`, `foot`, `walker`; override with `OMARCHY_DEV_HOST_CONFIG_DIRS`). It does not persist on the base image — pass the flag each time. Ignored with `--run-tests`. Hypr monitor layout files are skipped.

`mise rebase edge|stable|rc` means that channel’s newest ISO (`rc` = newest prerelease in the list). A version or filename from `mise rebase list` selects exactly that image.

Mise [forwards raw arguments](https://mise.jdx.dev/tasks/task-configuration.html#raw-args) to the wrappers, which own argument parsing. Put harness options after `--`; values containing spaces stay one argument. `--help` displays the wrapper's help.

Mise consumes the first `--`. To pass a literal separator to the wrapper, use `mise test <tree> -- -- --keep-running`. This preserves the boundary when forwarding values that resemble wrapper flags.

Setup flags are `--no-base`, `--no-iso`, `--no-packages`, and `--channel stable`. Setup prepares the exact channel ISO it resolves. `--no-iso` uses an existing local selection; combine it with `--no-base` to prepare checkouts without an ISO.

## Base selection and completion

`omarchy-iso/test-runs/CURRENT` records the selected ISO's canonical path, including paths outside `release/`. Health and status only inspect selection. A missing selected ISO produces an error instead of silently selecting another image.

Base reuse requires a `base.complete` record beside `base.qcow2`, matching the ISO and disk fingerprints, a valid qcow2 image, and readable firmware variables and SSH key files. The record is published only after installation and image validation succeed. Setup reuses a verified base or creates a missing one. It refuses an existing unverified disk so that a routine setup cannot overwrite it.

Existing bases made before this completion check need one explicit rebuild with `mise rebase <target>`. `mise rebase list` shows available targets. No completion record is inferred from file size. Keep an external ISO at its selected path; moving or replacing it requires another explicit rebase.

## Harness updates

Setup and test verify that the tracked patch's changes are applied. A clean upstream harness receives the patch. A stale or conflicting local version causes an error and remains untouched. Reconcile the local harness diff with `patches/omarchy-iso-test.patch` before retrying. Regenerating a patch from an unmodified harness does not repair an apply failure.

Worktree sync preserves dirty additions, deletions, file modes, and symlinks with independent Git metadata. Both the desktop and acceptance suite use the activated guest checkout. Required startup, sync, and activation failures stop the run. Host-config transfer failures also stop the run.

## Layout

| Path | What |
|------|------|
| `omarchy/` | Main Omarchy checkout; hub for git worktrees |
| `worktrees/<name>/` | Feature branch checkouts (commit here as usual) |
| `omarchy-iso/` | Harness, downloaded ISOs, base image under `test-runs/` |
| `patches/` | Tracked patch applied to local `omarchy-iso-test` |
| `bin/` | Wrappers invoked by the mise tasks |

Nested trees stay inside this checkout (do not symlink them out).

## Wrapper regression checks

Run `python -m unittest discover -s tests -v`. The suite uses temporary repositories, tiny qcow2 images, and inert VM and network commands. It requires Python 3, Git, jq, qemu-img, and mise. Harness checks also require the local `omarchy-iso` checkout; those checks are skipped when it is absent. No VM is booted by this suite.

## License

MIT — see [LICENSE](LICENSE).
