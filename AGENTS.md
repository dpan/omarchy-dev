# Omarchy Dev

This meta repository manages local Omarchy checkouts and disposable VM tests. Use [README.md](README.md) for user commands. Run from the checkout root through mise; `mise.toml` owns tool requirements, packages, and task declarations.

## Scope and ownership

- Keep shared ISO, base, and session policy in `bin/_lib.sh`. Wrappers orchestrate commands; mise forwards raw arguments and supplies completion metadata. Keep parsing in the wrappers.
- Make product changes in `worktrees/<name>/`, following that repository's instructions. `omarchy/` is the base checkout and worktree hub. Nested repositories have separate commits and may contain unrelated edits; preserve them.
- Honor `OMARCHY_DEV_*` path overrides. Keep nested checkouts as real directories, without external symlinks or shell-rc PATH changes. Do not hardcode machine-specific paths.
- Keep local review notes and logs in `.audit/`, which is ignored. Permanent regression checks belong in `tests/`.

## Behavior contracts

- Resolve the requested ISO once and pass that path through setup/rebase. Path-returning helpers print only the path to stdout; diagnostics go to stderr.
- `test-runs/CURRENT` is authoritative when present and stores the canonical ISO path; legacy release basenames remain readable. Discovery, health, and status never write selection. Successful base preparation publishes selection atomically.
- Base reuse requires matching `base.complete` evidence for the ISO and disk, a standalone qcow2 image, and readable, nonempty firmware/SSH files. Invalidate completion before rebuilding; publish it only after installation and image consistency checks succeed. Do not infer installation success from file size. Leave mutable firmware out of fingerprints.
- Ignore filesystem device numbers when comparing completion fingerprints; remounts can change them. Keep checking the recorded paths, inodes, sizes, and timestamps, and preserve existing completion records during read-only checks.
- Worktree sync preserves exact working files, including deletions, additions, modes, and symlinks, with independent Git metadata. Desktop activation and acceptance must use the same guest path. Resolve caller-relative paths before changing directory; clean temporary snapshots on exit.
- Required startup, sync, activation, and transfer failures must propagate explicitly. Bash suppresses `errexit` through functions invoked in conditionals; use explicit failure returns and `pipefail` for transfer pipelines.
- Keep host-config copies in disposable overlays and disabled during automated checks. Preserve monitor-file exclusions. QEMU extra arguments are single-line whitespace-delimited values; never eval them or expand wildcards.
- Host-config restore includes portable Omarchy widget settings and the selected theme/background. Rebuild background references with guest-local paths. Apply appearance through the guest session environment using its active checkout. Required transfer, apply, and shell-restart failures must prevent readiness.
- Activated worktree runs apply pending migrations as the guest user against the active checkout before readiness or tests. Copy optional host settings before migrations, then apply appearance and restart the shell. Preserve completion markers and keep migration authorization inside the disposable guest.

## Harness patch

`patches/omarchy-iso-test.patch` is the tracked implementation of changes to the ignored local harness. Recognize it with `git apply --reverse --check`; apply forward only after a clean check. If neither direction applies, preserve local edits and report the conflict.

After porting the required changes to the nested harness, regenerate the patch against its HEAD:

```bash
git -C omarchy-iso diff --src-prefix=a/ --dst-prefix=b/ HEAD -- bin/omarchy-iso-test > patches/omarchy-iso-test.patch
```

Include only intended harness edits. Regenerating from a clean harness would erase the patch. Retain unified-diff context prefixes, including the single space on blank context lines.

## Verification

For wrapper or patch changes, run `python -m unittest discover -s tests -v`, syntax-check affected shell files with `bash -n`, and run `git diff --check`. Report skipped cases; harness tests require the local `omarchy-iso` checkout. When editing the patch, compare it with the local harness diff and check reverse applicability.

Use temporary fixtures for routine checks. `mise setup` installs/downloads, `mise rebase` replaces a shared base, and interactive `mise test` grabs the keyboard and leaves a VM running. Use `--run-tests --ssh --no-grab-kbd` for automated headless guest checks.

## Change conventions

Use Conventional Commits for this meta repository. Update README usage and these contracts together when command behavior changes. Documentation-only changes need link, example, and consistency checks, without starting a VM.
