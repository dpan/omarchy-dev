# Omarchy Dev

Local workspace for Omarchy feature work: git worktrees and mise tasks that boot a VM from a shared preinstalled base image (install once from an ISO; reuse for stock and every feature tree).

Clone anywhere, then `cd` into the checkout. Omarchy already activates mise.

## Quick start

```bash
mise trust                              # once per machine/path, if prompted
mise setup                              # packages, clones, ISO, base image
mise worktree -- add my-feature quattro

mise rebase status                      # active ISO / base image
mise test -- my-feature                 # desktop VM; worktree is live Omarchy
mise test                               # same VM, stock Omarchy
mise test -- --run-tests my-feature     # automated checks, then stop
```

| Task | Meaning |
|------|---------|
| `mise setup` | Prepare workspace (ISO + base image if missing) |
| `mise worktree -- add\|list\|remove …` | Feature worktrees under `worktrees/` |
| `mise test` | Boot interactive VM (**stock** Omarchy), leave it running |
| `mise test -- <tree>` | Boot VM with that worktree as live Omarchy |
| `mise test -- --run-tests [tree]` | Run automated checks, then stop |
| `mise rebase` | Rebuild base image from latest **edge** ISO |
| `mise rebase list` | Pick edge / stable / rc / local ISO, then rebuild |
| `mise rebase status` | Show active ISO + base image |

`test` opens a QEMU window and grabs the keyboard by default (Super in the guest; Left+Right Ctrl toggles grab). Optional: `--ssh` (no window), `--no-grab-kbd`.

Setup flags: `mise setup -- --no-base`, `--no-iso`, `--no-packages`, `--channel stable`.

## Layout

| Path | What |
|------|------|
| `omarchy/` | Main Omarchy checkout; hub for git worktrees |
| `worktrees/<name>/` | Feature branch checkouts (commit here as usual) |
| `omarchy-iso/` | Harness, downloaded ISOs, base image under `test-runs/` |
| `bin/` | Wrappers invoked by the mise tasks |

Nested trees stay inside this checkout (do not symlink them out).
