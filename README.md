# Omarchy Dev

Local workspace for Omarchy feature work: git worktrees, one shared acceptance disk, mise commands.

Clone anywhere and `cd` into the checkout. Details for agents: [`AGENTS.md`](AGENTS.md).

## Happy path

```bash
mise trust                              # once per machine/path, if prompted
mise setup                              # packages, clones, ISO, acceptance base
omarchy-dev-worktree add my-feature quattro

mise rebase status                      # active ISO/base
mise test -- my-feature                 # boot VM with that worktree live (desktop)
mise test                               # boot VM on stock Omarchy
mise test -- --run-tests my-feature        # automated checks, then stop
```

| Command | Meaning |
|---------|---------|
| `mise setup` | Prepare workspace (ISO + ensure base) |
| `mise test` | Boot interactive VM (**stock** Omarchy), leave it running |
| `mise test -- <tree>` | Boot VM with that worktree as live Omarchy |
| `mise test -- --run-tests [tree]` | Run automated checks, then stop the VM |
| `mise rebase` | Rebuild base from latest **edge** ISO |
| `mise rebase list` | Pick edge / stable / rc / local ISO, then rebuild |
| `mise rebase status` | Show active ISO + base |

`test` defaults to a QEMU window **and** keyboard grab (Super in guest; Left+Right Ctrl toggles). Optional: `--ssh` (no window), `--no-grab-kbd`.

Setup variants: `mise setup -- --no-base`, `--no-iso`, `--no-packages`, `--channel stable`.

## Layout

| Path | What |
|------|------|
| `omarchy/` | Main checkout; worktree hub |
| `worktrees/<name>/` | Feature branches |
| `omarchy-iso/` | Harness, `release/` ISOs, `test-runs/` base |
| `bin/` | `omarchy-dev-setup`, `-test`, `-rebase`, `-worktree` |

Nested trees stay inside this checkout (no symlink-out). Do not commit `omarchy/`, `omarchy-iso/`, or large VM artifacts.
