"""Exercise wrapper contracts without downloads, host changes, or virtual machines."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "bin/_lib.sh"


def executable(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    path.chmod(0o755)


class WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="omarchy-regression-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.iso = self.root / "omarchy-iso"
        (self.iso / "release").mkdir(parents=True)
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("OMARCHY_DEV_", "OMARCHY_ISO_"))
            and key not in ("OMARCHY_PATH", "QEMU_EXTRA")
        }
        self.env.update(OMARCHY_DEV_ROOT=str(self.root), TEST_LIB=str(LIB))
        executable(self.iso / "bin/omarchy-iso-boot", "#!/bin/bash\nexit 0\n")

    def run_command(self, args, cwd=None):
        return subprocess.run(args, cwd=cwd or self.root, env=self.env, text=True,
                              capture_output=True, timeout=30)

    def shell(self, body):
        return self.run_command(["bash", "-c", 'set -euo pipefail\nsource "$TEST_LIB"\n' + body])

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def image(self, name="omarchy-edge-fixture.iso", mtime=1000):
        path = self.iso / "release" / name
        path.write_text(name)
        os.utime(path, (mtime, mtime))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        Path(str(path) + ".sha256").write_text(f"{digest}  {name}\n")
        return path


    def channel_network(self, iso):
        self.env["TEST_METADATA"] = json.dumps({"size": 0, "version": "fixture", "url": "https://fixture.invalid/" + iso.name})
        self.env["TEST_ISO_SOURCE"] = str(iso)
        executable(self.root / "stubs/curl", r'''#!/bin/bash
set -euo pipefail
output=
url=
while (($#)); do
  case "$1" in
    -o) output=$2; shift ;;
    https://*) url=$1 ;;
  esac
  shift
done
case "$url" in
  *latest.json) printf '%s\n' "$TEST_METADATA" ;;
  *.sha256) cp "$TEST_ISO_SOURCE.sha256" "$output" ;;
  *.iso) cp "$TEST_ISO_SOURCE" "$output" ;;
  *) exit 2 ;;
esac
''')
        self.env["PATH"] = str(self.root / "stubs") + os.pathsep + self.env["PATH"]

    def inert_installer(self, existing_source=None):
        if not shutil.which("qemu-img"):
            self.skipTest("qemu-img is not installed")
        source = r'''if [[ " $* " != *" --install-only "* ]]; then
  printf 'ARG=<%s>\n' "$@"
  exit 0
fi
iso=$1
base_dir="$OMARCHY_DEV_ROOT/omarchy-iso/test-runs/$(basename "$iso" .iso)"
mkdir -p "$base_dir"
printf '%s\n' "$iso" >>"$OMARCHY_DEV_ROOT/installer.log"
if [[ ${TEST_INSTALL_STATUS:-0} != 0 ]]; then exit "$TEST_INSTALL_STATUS"; fi
rm -f "$base_dir/base.qcow2"
qemu-img create -q -f qcow2 "$base_dir/base.qcow2" 16M
printf 'fixture firmware vars\n' >"$base_dir/OVMF_VARS.4m.fd"
printf 'fixture private key\n' >"$base_dir/id_ed25519"
printf 'fixture public key\n' >"$base_dir/id_ed25519.pub"
exit 0
'''
        if existing_source:
            first, rest = existing_source.split("\n", 1)
            source = first + "\n" + source + rest
        else:
            source = "#!/bin/bash\nset -euo pipefail\n" + source
        executable(self.iso / "bin/omarchy-iso-test", source)

    def build_image(self, iso):
        self.env["TEST_IMAGE"] = str(iso)
        self.assert_success(self.shell('omarchy_dev_build_base "$TEST_IMAGE"'))
        self.assert_success(self.shell('omarchy_dev_base_is_usable "$TEST_IMAGE"'))
        return self.iso / "test-runs" / iso.stem / "base.qcow2"


class WorkspaceTests(WorkspaceFixture):
    def test_channel_download_refuses_insufficient_space_before_downloading(self):
        source = self.image("source.iso")
        self.channel_network(source)
        self.env["TEST_METADATA"] = json.dumps({"size": 10 * 1024 ** 3, "version": "fixture",
                                                "url": "https://fixture.invalid/target.iso"})
        result = self.shell('df() { printf "Avail\\n0\\n"; }; omarchy_dev_ensure_channel_iso edge')
        self.assertNotEqual(result.returncode, 0, "download started without enough free disk space")
        self.assertIn("Not enough free disk", result.stderr)
        self.assertFalse((self.iso / "release/target.iso").exists())

    def test_channel_resolver_stdout_is_exactly_one_path(self):
        path = self.image()
        self.channel_network(path)
        result = self.shell('omarchy_dev_resolve_rebase_target edge')
        self.assert_success(result)
        self.assertEqual(result.stdout, str(path) + "\n")

    def test_materialization_propagates_deletion_and_keeps_standalone_git(self):
        source = self.root / "source"
        for args in (["git", "init", "-q", str(source)],
                     ["git", "-C", str(source), "config", "user.name", "Fixture"],
                     ["git", "-C", str(source), "config", "user.email", "fixture@example.invalid"]):
            self.assert_success(self.run_command(args))
        (source / "deleted-command").write_text("old command\n")
        (source / "changed-command").write_text("original\n")
        self.assert_success(self.run_command(["git", "-C", str(source), "add", "."]))
        self.assert_success(self.run_command(["git", "-C", str(source), "commit", "-qm", "fixture"]))
        tree = self.root / "tree"
        self.assert_success(self.run_command(["git", "-C", str(source), "worktree", "add", "-qb", "feature/fixture", str(tree)]))
        (tree / "deleted-command").unlink()
        (tree / "changed-command").write_text("edited\n")
        (tree / "untracked-command").write_text("new\n")
        self.env.update(TEST_SOURCE=str(tree), TEST_DEST=str(self.root / "copy"))
        result = self.shell('omarchy_dev_materialize_sync_tree "$TEST_SOURCE" "$TEST_DEST"')
        self.assert_success(result)
        copy = self.root / "copy"
        self.assertFalse((copy / "deleted-command").exists(), "deleted command was restored")
        self.assertEqual((copy / "changed-command").read_text(), "edited\n")
        self.assertEqual((copy / "untracked-command").read_text(), "new\n")
        self.assertTrue((copy / ".git").is_dir())
        self.assert_success(self.run_command(["git", "-C", str(copy), "fsck", "--no-reflogs"]))

    def test_sparse_non_image_is_not_a_usable_base(self):
        self.inert_installer()
        iso = self.image()
        disk = self.build_image(iso)
        with disk.open("wb") as stream:
            stream.truncate(1024 ** 3)
        result = self.shell('omarchy_dev_base_is_usable "$TEST_IMAGE"')
        self.assertNotEqual(result.returncode, 0, "zero-filled sparse file was accepted as an installed base")

    def test_current_lookup_does_not_persist_fallback(self):
        self.inert_installer()
        path = self.image()
        self.build_image(path)
        current = self.iso / "test-runs/CURRENT"
        current.unlink()
        result = self.shell('omarchy_dev_current_iso')
        self.assert_success(result)
        self.assertEqual(result.stdout, str(path) + "\n")
        self.assertFalse(current.exists(), "lookup wrote CURRENT")

    def test_external_iso_current_roundtrip(self):
        self.inert_installer()
        external = self.root / "external image.iso"
        external.write_text("external fixture")
        self.build_image(external)
        result = self.shell('omarchy_dev_current_iso')
        self.assert_success(result)
        self.assertEqual(result.stdout, str(external.resolve()) + "\n")
        self.assert_success(self.shell('omarchy_dev_base_is_usable "$TEST_IMAGE"'))

    def test_successful_install_can_be_reused_without_reinstalling(self):
        self.inert_installer()
        path = self.image()
        self.build_image(path)
        self.assert_success(self.shell('omarchy_dev_base_is_usable "$TEST_IMAGE"; omarchy_dev_ensure_base "$TEST_IMAGE"'))
        self.assertEqual((self.root / "installer.log").read_text().splitlines(), [str(path)])

    def test_device_number_change_keeps_existing_completion_usable_without_writes(self):
        self.inert_installer()
        iso = self.image()
        disk = self.build_image(iso)
        marker = disk.parent / "base.complete"
        recorded = marker.read_text().splitlines()
        for index in (2, 3):
            device, rest = recorded[index].split(":", 1)
            recorded[index] = f"{int(device) + 1}:{rest}"
        marker.write_text("\n".join(recorded) + "\n")
        current = self.iso / "test-runs/CURRENT"
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in (marker, current)]
        result = self.shell('omarchy_dev_require_base')
        self.assert_success(result)
        self.assertEqual(result.stdout, str(iso) + "\n")
        self.assert_success(self.shell('omarchy_dev_rebase_status'))
        self.assertEqual([(path.read_bytes(), path.stat().st_mtime_ns) for path in (marker, current)], before)
        self.assertEqual((self.root / "installer.log").read_text().splitlines(), [str(iso)])

    def test_completion_still_rejects_other_fingerprint_changes(self):
        self.inert_installer()
        disk = self.build_image(self.image())
        marker = disk.parent / "base.complete"
        original = marker.read_text().splitlines()
        variants = [("version", 0, "unknown-base-version"), ("path", 1, original[1] + ".other")]
        for index in (2, 3):
            parts = original[index].split(":", 3)
            for field in (1, 2, 3):
                changed = parts.copy()
                changed[field] += "1"
                variants.append((f"record-{index}-field-{field}", index, ":".join(changed)))
        for name, index, value in variants:
            with self.subTest(field=name):
                recorded = original.copy()
                recorded[index] = value
                marker.write_text("\n".join(recorded) + "\n")
                self.assertNotEqual(self.shell('omarchy_dev_base_is_usable "$TEST_IMAGE"').returncode, 0)

    def test_missing_boot_files_make_completed_base_unusable(self):
        self.inert_installer()
        disk = self.build_image(self.image())
        for name in ("OVMF_VARS.4m.fd", "id_ed25519"):
            with self.subTest(file=name):
                path = disk.parent / name
                contents = path.read_bytes()
                path.unlink()
                try:
                    self.assertNotEqual(self.shell('omarchy_dev_base_is_usable "$TEST_IMAGE"').returncode, 0,
                                        "missing boot file was accepted")
                finally:
                    path.write_bytes(contents)

    def test_failed_rebuild_invalidates_previous_completion(self):
        self.inert_installer()
        self.build_image(self.image())
        self.env["TEST_INSTALL_STATUS"] = "7"
        result = self.shell('omarchy_dev_build_base "$TEST_IMAGE"')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(self.shell('omarchy_dev_base_is_usable "$TEST_IMAGE"').returncode, 0,
                            "failed rebuild left a reusable completion record")

    def test_replaced_iso_invalidates_completion(self):
        self.inert_installer()
        path = self.image()
        self.build_image(path)
        path.write_text("replacement ISO with different contents")
        self.assertNotEqual(self.shell('omarchy_dev_base_is_usable "$TEST_IMAGE"').returncode, 0)

    def test_unrecorded_qcow2_is_not_an_installed_base(self):
        self.inert_installer()
        path = self.image()
        self.assert_success(self.run_command([str(self.iso / "bin/omarchy-iso-test"), str(path), "--install-only"]))
        self.env["TEST_IMAGE"] = str(path)
        self.assertNotEqual(self.shell('omarchy_dev_base_is_usable "$TEST_IMAGE"').returncode, 0)

    def test_invalid_explicit_selection_does_not_fall_back(self):
        self.inert_installer()
        self.build_image(self.image())
        current = self.iso / "test-runs/CURRENT"
        current.write_text(str(self.root / "missing.iso") + "\n")
        result = self.shell('omarchy_dev_current_iso')
        self.assertNotEqual(result.returncode, 0, "invalid explicit selection silently fell back")
        self.assertEqual(current.read_text(), str(self.root / "missing.iso") + "\n")

    def test_legacy_basename_selection_remains_readable(self):
        self.inert_installer()
        iso = self.image()
        self.build_image(iso)
        current = self.iso / "test-runs/CURRENT"
        current.write_text(iso.name + "\n")
        result = self.shell('omarchy_dev_current_iso')
        self.assert_success(result)
        self.assertEqual(result.stdout, str(iso) + "\n")
        self.assertEqual(current.read_text(), iso.name + "\n")

    @unittest.skipUnless(shutil.which("mise"), "mise is not installed")
    def test_real_mise_forwards_harness_arguments(self):
        shutil.copyfile(REPO / "mise.toml", self.root / "mise.toml")
        executable(self.root / "bin/omarchy-dev-test", '#!/bin/bash\nprintf \'ARG=<%s>\\n\' "$@"\n')
        self.env.update(MISE_TRUSTED_CONFIG_PATHS=str(self.root),
                        MISE_STATE_DIR=str(self.root / ".mise-state"),
                        MISE_TASK_RUN_AUTO_INSTALL="false")
        cases = (["sample-tree", "--", "--keep-running"],
                 ["sample-tree", "--", "--", "--keep-running"],
                 ["--ssh", "--no-grab-kbd", "--", "--name", "VM with spaces"],
                 ["--", "--keep-running"])
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.run_command(["mise", "run", "test", *arguments])
                self.assert_success(result)
                expected = list(arguments)
                expected.remove("--")
                self.assertEqual(result.stdout.splitlines(), [f"ARG=<{argument}>" for argument in expected])



class HarnessTests(WorkspaceFixture):
    def setUp(self):
        super().setUp()
        checkout = REPO / "omarchy-iso"
        if not (checkout / ".git").exists():
            self.skipTest("local omarchy-iso checkout is not available")
        upstream = self.run_command(["git", "-C", str(checkout), "show", "HEAD:bin/omarchy-iso-test"])
        self.assert_success(upstream)
        self.harness = self.iso / "bin/omarchy-iso-test"
        executable(self.harness, upstream.stdout)
        self.assert_success(self.run_command(["git", "init", "-q", str(self.iso)]))
        self.env["OMARCHY_DEV_ISO_HARNESS_PATCH"] = str(REPO / "patches/omarchy-iso-test.patch")

    def apply_patch(self):
        self.assert_success(self.run_command(["git", "-C", str(self.iso), "apply", str(REPO / "patches/omarchy-iso-test.patch")]))

    def function(self, name):
        source = self.harness.read_text()
        start = source.index(name + "() ")
        closing = "}" if source[start + len(name) + 3] == "{" else ")"
        end = start
        while True:
            end = source.index("\n" + closing + "\n", end) + 3
            candidate = source[start:end]
            parsed = subprocess.run(["bash", "-n"], input=candidate, text=True,
                                    capture_output=True, timeout=5)
            if parsed.returncode == 0:
                return candidate

    def acceptance(self, overrides="", interactive=True, sync=True):
        run_dir = self.root / "run"
        run_dir.mkdir(exist_ok=True)
        self.env["TEST_RUN_DIR"] = str(run_dir)
        script = r'''qemu-img() { return 0; }
start_vm() { return 0; }
establish_session() { return 0; }
sync_omarchy() { return 0; }
ssh_guest() { printf '%s\n' "$*" >>"$TEST_RUN_DIR/ssh.log"; }
sleep() { return 0; }
log() { printf '%s\n' "$*"; }
shortcut_smoke_phase() { return 0; }
capture_console() { return 0; }
BASE_DISK="$TEST_RUN_DIR/base.qcow2"
RUN_DIR="$TEST_RUN_DIR"
GUEST_USER=omarchy
GUEST_PASSWORD=omarchy
KEEP_RUNNING=false
OMARCHY_DEV_SYNC_HOST_CONFIG=0
'''
        script += f"SYNC_ALL={str(sync).lower()}\nOMARCHY_ISO_ACTIVATE_SYNC={int(sync)}\nOMARCHY_ISO_SKIP_TESTS={int(interactive)}\n"
        script += self.function("prepare_guest") + "\n"
        script += self.function("run_worktree_migrations") + "\n"
        script += "ssh_session() { [[ $1 != *--pending* ]]; }\n"
        script += overrides + "\n" + self.function("acceptance_phase")
        script += "\nif acceptance_phase; then printf 'PHASE_SUCCESS\\n'; else printf 'PHASE_FAILURE\\n'; fi\n"
        return self.shell(script)

    def migration_fixture(self):
        host, guest = self.host_restore_fixture()
        active = guest / "active checkout"
        (active / "bin").mkdir(parents=True)
        (active / "migrations").mkdir()
        (guest / ".local/state/omarchy/migrations").mkdir(parents=True)
        self.env.update(TEST_ACTIVE=str(active), TEST_HOST=str(host),
                        TEST_CREDENTIAL=str(self.root / "credential"),
                        TEST_TRANSACTION_PID=str(self.root / "transaction.pid"),
                        TEST_REFRESH_PIDS=str(self.root / "refresh.pids"),
                        TEST_MIGRATION_PID=str(self.root / "migration.pid"),
                        TEST_EXPECTED_UID=str(os.geteuid()))
        executable(active / "bin/omarchy-migrate", r'''#!/bin/bash
set -euo pipefail
if [[ ${1:-} == --pending ]]; then
  case ${TEST_PENDING_PROTOCOL:-} in
    empty-success) exit 0 ;;
    names-failure) printf '001-fixture.sh\n'; exit 1 ;;
  esac
  [[ ${TEST_PENDING_ERROR:-0} == 0 ]] || exit "$TEST_PENDING_ERROR"
  for migration in "$OMARCHY_PATH"/migrations/*.sh; do
    [[ -f $migration ]] || continue
    marker="$HOME/.local/state/omarchy/migrations/${migration##*/}"
    [[ -e $marker ]] || printf '%s\n' "${migration##*/}"
  done | { output=$(cat); [[ -z $output ]] && exit 1; printf '%s\n' "$output"; }
  exit $?
fi
printf 'migrate\n' >>"$TEST_EVENTS"
[[ ${TEST_MIGRATE_REMAINING:-0} != 1 ]] || exit 0
for migration in "$OMARCHY_PATH"/migrations/*.sh; do
  [[ -f $migration ]] || continue
  marker="$HOME/.local/state/omarchy/migrations/${migration##*/}"
  [[ -e $marker ]] && continue
  bash "$migration" || exit $?
  touch "$marker"
done
''')
        executable(active / "migrations/001-fixture.sh", r'''#!/bin/bash
set -euo pipefail
[[ $OMARCHY_PATH == "$TEST_ACTIVE" ]]
[[ $(command -v omarchy-migrate) == "$TEST_ACTIVE/bin/omarchy-migrate" ]]
[[ ${TEST_AS_ROOT:-0} == 0 ]]
[[ $(id -u) == "$TEST_EXPECTED_UID" ]]
printf 'migration\n' >>"$TEST_EVENTS"
printf '%s' "$$" >"$TEST_MIGRATION_PID"
sudo true
if [[ ${TEST_REQUIRE_SETTINGS:-0} == 1 ]]; then
  cmp "$HOME/.local/state/omarchy/settings/weather.json" "$TEST_HOST/.local/state/omarchy/settings/weather.json"
fi
case ${TEST_MIGRATION_FAILURE:-} in
  script) printf 'fixture migration failure\n' >&2; exit 37 ;;
  lost-auth) rm -f "$TEST_CREDENTIAL"; sudo true ;;
  interrupted) kill -TERM "$(cat "$TEST_TRANSACTION_PID")"; sleep 20 ;;
  long) sleep 20; printf 'unexpected-completion\n' >>"$TEST_EVENTS" ;;
esac
''')
        executable(self.root / "guest-bin/sudo", r'''#!/bin/bash
set -euo pipefail
[[ -e /dev/tty ]] || exit 70
terminal=$(tty </dev/tty)
if [[ $* == '-k' ]]; then
  printf 'revoke\n' >>"$TEST_EVENTS"
  rm -f "$TEST_CREDENTIAL"
  exit 0
fi
if [[ ${1:-} == -S ]]; then
  IFS= read -r password
  [[ $password == omarchy ]] || exit 71
  printf 'authenticate\n' >>"$TEST_EVENTS"
  [[ ${TEST_AUTH_FAIL:-0} != 1 ]] || { printf 'fixture authentication failure\n' >&2; exit 72; }
  printf '%s' "$terminal" >"$TEST_CREDENTIAL"
  printf '%s' "$PPID" >"$TEST_TRANSACTION_PID"
  exit 0
fi
if [[ $* == '-n -v' ]]; then
  printf 'refresh\n' >>"$TEST_EVENTS"
  printf '%s\n' "$PPID" >>"$TEST_REFRESH_PIDS"
  [[ ${TEST_REFRESH_FAIL:-0} != 1 && -e $TEST_CREDENTIAL && $(cat "$TEST_CREDENTIAL") == "$terminal" ]] || exit 73
  exit 0
fi
[[ ${1:-} != -n ]] || shift
if [[ ! -e $TEST_CREDENTIAL || $(cat "$TEST_CREDENTIAL") != "$terminal" ]]; then
  printf 'terminal-prompt\n' >>"$TEST_EVENTS"
  IFS= read -r password </dev/tty || exit 74
  exit 75
fi
printf 'privileged:%s\n' "$*" >>"$TEST_EVENTS"
TEST_AS_ROOT=1 "$@"
''')
        executable(self.root / "guest-bin/pacman", r'''#!/bin/bash
printf 'packages:%s\n' "$*" >>"$TEST_EVENTS"
[[ ${TEST_PACKAGES_FAIL:-0} == 0 ]] || printf 'fixture package failure\n' >&2
exit "${TEST_PACKAGES_FAIL:-0}"
''')
        # Keep the real util-linux script and PTY; shorten credential refresh sleeps.
        executable(self.root / "guest-bin/sleep", r'''#!/bin/bash
if [[ $1 == 30 ]]; then exec /usr/bin/sleep 0.05; fi
exec /usr/bin/sleep "$@"
''')
        return host, guest, active

    def migration_acceptance(self, interactive=True, host_config=False, sync=True):
        return self.acceptance(r'''OMARCHY_DEV_SYNC_HOST_CONFIG=''' + str(int(host_config)) + r'''
ssh_session() {
  [[ ${2:-} == /home/omarchy/.local/share/omarchy ]] || return 78
  (cd "$TEST_GUEST" && export HOME="$TEST_GUEST" OMARCHY_PATH="$TEST_ACTIVE" PATH="$TEST_ACTIVE/bin:$PATH";
    bash -c "$1" </dev/null)
}
ssh_guest() {
  case "$*" in
    *'/etc/omarchy.conf'*|*'sudo -S reboot'*|*'install.log'*) return 0 ;;
    *'pacman -Sy'*) printf 'interactive-packages\n' >>"$TEST_EVENTS"; return "${TEST_INTERACTIVE_PACKAGES_FAIL:-0}" ;;
    *'OMARCHY_ACCEPTANCE_DIR='*|*'test/all'*) printf 'tests\n' >>"$TEST_EVENTS"; return 0 ;;
    *'tar -C /tmp'*) return 0 ;;
  esac
  (cd "$TEST_GUEST" && HOME="$TEST_GUEST" bash -c "$*")
}
''', interactive=interactive, sync=sync)

    def migration_events(self):
        path = Path(self.env["TEST_EVENTS"])
        return path.read_text().splitlines() if path.exists() else []

    def assert_migration_processes_stopped(self):
        paths = (Path(self.env["TEST_MIGRATION_PID"]), Path(self.env["TEST_REFRESH_PIDS"]))
        for path in paths:
            if not path.exists():
                continue
            for pid in path.read_text().splitlines():
                status = Path("/proc") / pid / "stat"
                if status.exists():
                    self.assertEqual(status.read_text().rsplit(") ", 1)[1].split()[0], "Z",
                                     f"migration preparation left process {pid} running")

    def test_ssh_session_uses_active_tree_and_desktop_environment_with_stock_default(self):
        self.apply_patch()
        self.env["TEST_ACTIVE"] = str(self.root / "active checkout")
        script = r'''ssh_guest() {
  bash -c 'systemctl() { printf "LANG=en_US.UTF-8\n"; }
ls() { printf "fixture-hypr\n"; }
find() { printf "wayland-fixture\n"; }
'"$1"
}
'''
        script += self.function("ssh_session") + r'''
for active in "$TEST_ACTIVE" /usr/share/omarchy; do
  command='printf "%s\n" "$OMARCHY_PATH" "${PATH%%:*}" "$XDG_RUNTIME_DIR" "$DBUS_SESSION_BUS_ADDRESS" "$LANG" "$HYPRLAND_INSTANCE_SIGNATURE" "$WAYLAND_DISPLAY"'
  if [[ $active == /usr/share/omarchy ]]; then
    ssh_session "$command"
  else
    ssh_session "$command" "$active"
  fi
done
'''
        result = self.shell(script)
        self.assert_success(result)
        runtime = f"/run/user/{os.geteuid()}"
        expected = []
        for active in (self.env["TEST_ACTIVE"], "/usr/share/omarchy"):
            expected.extend([active, active + "/bin", runtime, "unix:path=" + runtime + "/bus",
                             "en_US.UTF-8", "fixture-hypr", "wayland-fixture"])
        self.assertEqual(result.stdout.splitlines(), expected)

    def test_worktree_migrations_run_before_readiness_and_automated_tests(self):
        self.apply_patch()
        _, guest, _ = self.migration_fixture()
        marker = guest / ".local/state/omarchy/migrations/001-fixture.sh"
        existing = marker.parent / "previous-migration.sh"
        existing.write_bytes(b"completed earlier\n")
        for interactive in (True, False):
            with self.subTest(interactive=interactive):
                marker.unlink(missing_ok=True)
                Path(self.env["TEST_EVENTS"]).unlink(missing_ok=True)
                result = self.migration_acceptance(interactive=interactive)
                self.assert_success(result)
                self.assertIn("PHASE_SUCCESS", result.stdout)
                self.assertTrue(marker.exists(), result.stdout + result.stderr)
                self.assertIn("Pending guest migrations completed", result.stdout)
                events = self.migration_events()
                self.assertLess(events.index("packages:-Sy"), events.index("migration"))
                self.assertLess(events.index("migration"), events.index("omarchy-restart-shell"))
                if not interactive:
                    self.assertLess(events.index("omarchy-restart-shell"), events.index("tests"))
                self.assertIn("revoke", events)
                self.assertFalse(Path(self.env["TEST_CREDENTIAL"]).exists())
                self.assertEqual(existing.read_bytes(), b"completed earlier\n")
                self.assert_migration_processes_stopped()

    def test_worktree_migration_reads_copied_settings_before_host_appearance(self):
        self.apply_patch()
        host, guest, active = self.migration_fixture()
        self.env["TEST_REQUIRE_APPEARANCE_PATH"] = str(active)
        settings = host / ".local/state/omarchy/settings"
        settings.mkdir(parents=True)
        (settings / "weather.json").write_bytes(b'{"name":"Athens","unit":"metric"}\n')
        self.host_background(host)
        self.env["TEST_REQUIRE_SETTINGS"] = "1"
        result = self.migration_acceptance(host_config=True)
        self.assert_success(result)
        self.assertIn("PHASE_SUCCESS", result.stdout)
        events = self.migration_events()
        self.assertIn("migration", events, result.stdout + result.stderr)
        self.assertLess(events.index("migration"), events.index("omarchy-theme-set"))
        self.assertLess(events.index("omarchy-theme-set"), events.index("omarchy-theme-bg-set"))
        self.assertEqual((guest / ".local/state/omarchy/settings/weather.json").read_bytes(),
                         (settings / "weather.json").read_bytes())

    def test_automated_worktree_migrates_without_copying_host_settings(self):
        self.apply_patch()
        host, guest, _ = self.migration_fixture()
        settings = host / ".local/state/omarchy/settings"
        settings.mkdir(parents=True)
        (settings / "weather.json").write_bytes(b"private host settings\n")
        self.host_background(host)
        result = self.migration_acceptance(interactive=False, host_config=True)
        self.assert_success(result)
        self.assertIn("PHASE_SUCCESS", result.stdout)
        self.assertIn("migration", self.migration_events())
        self.assertFalse((guest / ".local/state/omarchy/settings").exists())
        self.assertNotIn("omarchy-theme-set", self.migration_events())

    def test_no_pending_preserves_interactive_refresh_and_skips_automated_network(self):
        self.apply_patch()
        _, guest, _ = self.migration_fixture()
        (guest / ".local/state/omarchy/migrations/001-fixture.sh").touch()
        for interactive in (True, False):
            with self.subTest(interactive=interactive):
                Path(self.env["TEST_EVENTS"]).unlink(missing_ok=True)
                self.env["TEST_INTERACTIVE_PACKAGES_FAIL"] = "38"
                result = self.migration_acceptance(interactive=interactive)
                self.assert_success(result)
                self.assertIn("PHASE_SUCCESS", result.stdout)
                self.assertIn("No pending migrations", result.stdout)
                expected = ["interactive-packages", "omarchy-restart-shell"] if interactive else ["omarchy-restart-shell", "tests", "tests"]
                self.assertEqual(self.migration_events(), expected)
                if interactive:
                    self.assertIn("WARNING: pacman -Sy failed", result.stdout)

    def test_stock_run_does_not_migrate_or_restart_without_host_config(self):
        self.apply_patch()
        self.migration_fixture()
        result = self.migration_acceptance(sync=False)
        self.assert_success(result)
        self.assertIn("PHASE_SUCCESS", result.stdout)
        self.assertIn("Migrations skipped for stock base", result.stdout)
        self.assertEqual(self.migration_events(), ["interactive-packages"])

    def test_migration_prerequisite_runner_and_pending_failures_prevent_tests(self):
        self.apply_patch()
        self.migration_fixture()
        for variable, value in (("TEST_AUTH_FAIL", "1"), ("TEST_PACKAGES_FAIL", "36"),
                                ("TEST_MIGRATION_FAILURE", "script"),
                                ("TEST_MIGRATE_REMAINING", "1"), ("TEST_PENDING_ERROR", "2"),
                                ("TEST_PENDING_PROTOCOL", "empty-success"),
                                ("TEST_PENDING_PROTOCOL", "names-failure")):
            with self.subTest(failure=variable):
                Path(self.env["TEST_EVENTS"]).unlink(missing_ok=True)
                self.env[variable] = value
                result = self.migration_acceptance(interactive=False)
                self.env.pop(variable)
                self.assert_restore_failed(result)
                events = self.migration_events()
                self.assertNotIn("tests", events)
                self.assertNotIn("omarchy-restart-shell", events)
                self.assertFalse(Path(self.env["TEST_CREDENTIAL"]).exists())
                if variable.startswith("TEST_PENDING_"):
                    self.assertEqual(events, [])
                else:
                    self.assertIn("revoke", events)
                expected_message = {"TEST_AUTH_FAIL": "fixture authentication failure",
                                    "TEST_PACKAGES_FAIL": "fixture package failure",
                                    "TEST_MIGRATION_FAILURE": "fixture migration failure"}.get(variable)
                if expected_message:
                    self.assertIn(expected_message, result.stdout + result.stderr)

    def test_migration_nested_sudo_prompt_fails_with_closed_real_pty_input(self):
        self.apply_patch()
        self.migration_fixture()
        self.env["TEST_MIGRATION_FAILURE"] = "lost-auth"
        result = self.migration_acceptance(interactive=False)
        self.assert_restore_failed(result)
        self.assertIn("terminal-prompt", self.migration_events())
        self.assertIn("revoke", self.migration_events())
        self.assertNotIn("tests", self.migration_events())

    def test_migration_refresh_failure_interrupts_wait_and_revokes_credentials(self):
        self.apply_patch()
        self.migration_fixture()
        self.env.update(TEST_MIGRATION_FAILURE="long", TEST_REFRESH_FAIL="1")
        import time
        started = time.monotonic()
        result = self.migration_acceptance(interactive=False)
        self.assertLess(time.monotonic() - started, 5, result.stdout + result.stderr)
        self.assert_restore_failed(result)
        self.assertIn("refresh", self.migration_events())
        self.assertIn("revoke", self.migration_events())
        self.assertNotIn("unexpected-completion", self.migration_events())
        self.assertFalse(Path(self.env["TEST_CREDENTIAL"]).exists())
        self.assert_migration_processes_stopped()

    def test_migration_signal_interrupt_stops_children_and_revokes_credentials(self):
        self.apply_patch()
        self.migration_fixture()
        self.env["TEST_MIGRATION_FAILURE"] = "interrupted"
        result = self.migration_acceptance(interactive=False)
        self.assert_restore_failed(result)
        self.assertIn("revoke", self.migration_events())
        self.assertNotIn("tests", self.migration_events())
        self.assertFalse(Path(self.env["TEST_CREDENTIAL"]).exists())
        self.assert_migration_processes_stopped()

    def test_direct_test_wrapper_resolves_relative_tree_from_caller(self):
        self.apply_patch()
        self.inert_installer(self.harness.read_text())
        iso = self.image()
        self.build_image(iso)
        caller = self.root / "invocation"
        tree = caller / "custom-tree"
        self.assert_success(self.run_command(["git", "init", "-q", str(tree)]))
        self.assert_success(self.run_command(["git", "-C", str(tree), "-c", "user.name=Fixture",
                                              "-c", "user.email=fixture@example.invalid", "commit", "-q",
                                              "--allow-empty", "-m", "fixture"]))
        result = self.run_command([str(REPO / "bin/omarchy-dev-test"), "--ssh", "--no-grab-kbd", "./custom-tree"], cwd=caller)
        self.assert_success(result)
        self.assertIn("ARG=<--sync-all>", result.stdout)
        self.assertIn(f"ARG=<{iso}>", result.stdout)

    @unittest.skipUnless(shutil.which("mise"), "mise is not installed")
    def test_real_mise_forwards_no_tree_harness_option_value_to_actual_wrapper(self):
        self.apply_patch()
        self.inert_installer(self.harness.read_text())
        self.build_image(self.image())
        shutil.copyfile(REPO / "mise.toml", self.root / "mise.toml")
        (self.root / "bin").mkdir()
        shutil.copy2(REPO / "bin/omarchy-dev-test", self.root / "bin/omarchy-dev-test")
        shutil.copy2(LIB, self.root / "bin/_lib.sh")
        self.env.update(MISE_TRUSTED_CONFIG_PATHS=str(self.root),
                        MISE_STATE_DIR=str(self.root / ".mise-state"),
                        MISE_TASK_RUN_AUTO_INSTALL="false")
        result = self.run_command(["mise", "run", "test", "--ssh", "--no-grab-kbd", "--", "--memory", "2048"])
        self.assert_success(result)
        self.assertIn("ARG=<--memory>\nARG=<2048>", result.stdout)
        self.assertNotIn("ARG=<--sync-all>", result.stdout)

    def test_setup_explicit_stable_wins_over_newer_edge(self):
        self.apply_patch()
        self.inert_installer(self.harness.read_text())
        stable = self.image("omarchy-stable-fixture.iso", 1000)
        self.image("omarchy-edge-fixture.iso", 2000)
        (self.root / "omarchy/.git").mkdir(parents=True)
        executable(self.root / "stubs/mise", "#!/bin/bash\nexit 0\n")
        self.channel_network(stable)
        result = self.run_command([str(REPO / "bin/omarchy-dev-setup"), "--no-packages", "--channel", "stable"])
        self.assert_success(result)
        selected = self.shell('omarchy_dev_current_iso')
        self.assert_success(selected)
        self.assertEqual(selected.stdout, str(stable) + "\n")
        self.assertEqual((self.root / "installer.log").read_text().splitlines(), [str(stable)])

    def test_required_acceptance_stages_propagate_failure_inside_if(self):
        self.apply_patch()
        for name, override in (
            ("overlay", "qemu-img() { return 8; }"),
            ("boot", "start_vm() { return 8; }"),
            ("session", "establish_session() { return 9; }"),
            ("sync", "sync_omarchy() { return 7; }"),
            ("activation", "ssh_guest() { return 255; }"),
            ("rebooted session", "session_count=0; establish_session() { ((session_count+=1)); ((session_count < 2)); }"),
        ):
            with self.subTest(stage=name):
                result = self.acceptance(override)
                self.assert_success(result)
                self.assertIn("PHASE_FAILURE", result.stdout, result.stdout + result.stderr)
                self.assertNotIn("Interactive session ready", result.stdout)

    def test_host_config_transfer_failure_is_not_reported_ready(self):
        self.apply_patch()
        host = self.root / "host"
        (host / ".config/kitty").mkdir(parents=True)
        (host / ".config/kitty/kitty.conf").write_text("font_size 12\n")
        self.env["OMARCHY_DEV_HOST_HOME"] = str(host)
        result = self.acceptance('''OMARCHY_DEV_SYNC_HOST_CONFIG=1
OMARCHY_DEV_HOST_CONFIG_DIRS=kitty
ssh_guest() { [[ $* != *"mkdir -p .config"* ]]; }
''')
        self.assert_success(result)
        self.assertIn("PHASE_FAILURE", result.stdout)
        self.assertNotIn("Interactive session ready", result.stdout)

    def host_restore_fixture(self):
        host = self.root / "host"
        guest = self.root / "guest"
        host.mkdir(exist_ok=True)
        guest.mkdir(exist_ok=True)
        self.env.update(OMARCHY_DEV_HOST_HOME=str(host), TEST_GUEST=str(guest),
                        TEST_EVENTS=str(self.root / "events"))
        for command in ("omarchy-theme-set", "omarchy-theme-bg-set", "omarchy-restart-shell"):
            executable(self.root / "guest-bin" / command, r'''#!/bin/bash
set -euo pipefail
command=${0##*/}
printf '%s\n' "$command" >>"$TEST_EVENTS"
if [[ -n ${TEST_REQUIRE_APPEARANCE_PATH:-} && ${OMARCHY_PATH:-} != "$TEST_REQUIRE_APPEARANCE_PATH" ]]; then
  printf 'Appearance command is missing its active Omarchy environment\n' >&2
  exit 35
fi
[[ ${TEST_FAIL_COMMAND:-} != "$command" ]] || exit 31
if [[ $command == omarchy-theme-bg-set ]]; then
  [[ -f $1 ]] || exit 32
  printf '%s' "$1" >"$HOME/background-argument"
fi
''')
        self.env["PATH"] = str(self.root / "guest-bin") + os.pathsep + self.env["PATH"]
        return host, guest

    def restore_acceptance(self, interactive=True):
        return self.acceptance(r'''OMARCHY_DEV_SYNC_HOST_CONFIG=1
set +o pipefail
ssh_session() {
  (cd "$TEST_GUEST" && HOME="$TEST_GUEST" OMARCHY_PATH="${2:-/usr/share/omarchy}" bash -c "$1")
}
tar() {
  command tar "$@" || return $?
  [[ ${TEST_FAIL_TRANSFER:-} != settings-source || $* != *'/settings'* ]]
}
ssh_guest() {
  case "$*" in
    *pacman*|*OMARCHY_ACCEPTANCE_DIR=*|*omarchy-acceptance*|*install.log*) return 0 ;;
  esac
  if [[ ${TEST_FAIL_TRANSFER:-} == settings && $* == *'.local/state/omarchy/settings'* ]]; then
    cat >/dev/null
    return 33
  fi
  if [[ ${TEST_FAIL_TRANSFER:-} == background && $* == *'cat >'* ]]; then
    cat >/dev/null
    return 34
  fi
  (cd "$TEST_GUEST" && HOME="$TEST_GUEST" bash -c "$*")
}
''', interactive=interactive, sync=False)

    def assert_restore_failed(self, result):
        self.assert_success(result)
        self.assertIn("PHASE_FAILURE", result.stdout, result.stdout + result.stderr)
        self.assertNotIn("Interactive session ready", result.stdout)

    def test_stock_host_appearance_uses_guest_omarchy_environment(self):
        self.apply_patch()
        host, _ = self.host_restore_fixture()
        self.host_background(host)
        self.env["TEST_REQUIRE_APPEARANCE_PATH"] = "/usr/share/omarchy"
        result = self.restore_acceptance()
        self.assert_success(result)
        self.assertIn("PHASE_SUCCESS", result.stdout, result.stdout + result.stderr)
        self.assertEqual(Path(self.env["TEST_EVENTS"]).read_text().splitlines(),
                         ["omarchy-theme-set", "omarchy-theme-bg-set", "omarchy-restart-shell"])

    def test_host_config_restores_settings_configs_symlinks_and_excludes_monitors(self):
        self.apply_patch()
        host, guest = self.host_restore_fixture()
        files = {
            ".local/state/omarchy/settings/weather.json": b'{"name":"Athens","latitude":37.98,"longitude":23.72}\n',
            ".config/omarchy/shell.json": b'{"version":1,"plugins":[]}\n',
            ".config/omarchy/plugins/weather/main.qml": b"weather plugin\n",
            ".config/hypr/hyprland.conf": b"source = local.conf\n",
            ".config/kitty/kitty.conf": b"font_size 12\n",
        }
        for name, content in files.items():
            path = host / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (host / ".config/kitty/linked.conf").symlink_to("kitty.conf")
        (guest / ".config/hypr").mkdir(parents=True)
        for name in ("monitors.conf", "monitors.lua"):
            (host / ".config/hypr" / name).write_text("host hardware\n")
            (guest / ".config/hypr" / name).write_bytes(b"guest monitor layout\n")
        for name in ("history", "cache", "hardware"):
            (host / ".local/state/omarchy" / name).write_text("private runtime state\n")
        result = self.restore_acceptance()
        self.assert_success(result)
        self.assertIn("PHASE_SUCCESS", result.stdout)
        for name, content in files.items():
            self.assertTrue((guest / name).is_file(), f"portable file was not restored: {name}")
            self.assertEqual((guest / name).read_bytes(), content)
        self.assertTrue((guest / ".config/kitty/linked.conf").is_symlink())
        self.assertEqual(os.readlink(guest / ".config/kitty/linked.conf"), "kitty.conf")
        for name in ("monitors.conf", "monitors.lua"):
            self.assertEqual((guest / ".config/hypr" / name).read_bytes(), b"guest monitor layout\n")
        for name in ("history", "cache", "hardware"):
            self.assertFalse((guest / ".local/state/omarchy" / name).exists())

    def host_background(self, host, filename="selected.png"):
        current = host / ".local/state/omarchy/current"
        current.mkdir(parents=True, exist_ok=True)
        (current / "theme.name").write_text("Fixture Theme\n")
        asset = self.root / "external media" / filename
        asset.parent.mkdir(exist_ok=True)
        asset.write_bytes(b"selected background content\x00\xff")
        (current / "background").symlink_to(asset)
        return asset

    def test_host_config_preserves_external_selected_background_bytes_and_apply_order(self):
        self.apply_patch()
        host, guest = self.host_restore_fixture()
        for filename in ("selected image.png", "-a 'quoted' $(touch INJECTED).mp4"):
            with self.subTest(filename=filename):
                (host / ".local/state/omarchy/current/background").unlink(missing_ok=True)
                Path(self.env["TEST_EVENTS"]).unlink(missing_ok=True)
                asset = self.host_background(host, filename)
                result = self.restore_acceptance()
                self.assert_success(result)
                self.assertIn("PHASE_SUCCESS", result.stdout)
                argument = guest / "background-argument"
                self.assertTrue(argument.exists(), "selected background was not applied")
                restored = Path(argument.read_text())
                self.assertTrue(restored.is_relative_to(guest), "background uses a host path")
                self.assertFalse(restored.is_relative_to(guest / ".local/state/omarchy/current/theme"))
                self.assertFalse(restored.is_symlink(), "background retains a host symlink")
                self.assertEqual(restored.suffix, asset.suffix)
                self.assertEqual(restored.read_bytes(), asset.read_bytes())
                self.assertEqual(Path(self.env["TEST_EVENTS"]).read_text().splitlines(),
                                 ["omarchy-theme-set", "omarchy-theme-bg-set", "omarchy-restart-shell"])
                self.assertFalse((guest / "INJECTED").exists())
                self.assertFalse((self.root / "INJECTED").exists())

    def test_host_config_missing_optional_state_succeeds_and_custom_config_dirs_work(self):
        self.apply_patch()
        host, guest = self.host_restore_fixture()
        (host / ".config/custom").mkdir(parents=True)
        (host / ".config/custom/custom.conf").write_bytes(b"custom config\n")
        self.env["OMARCHY_DEV_HOST_CONFIG_DIRS"] = "custom"
        result = self.restore_acceptance()
        self.assert_success(result)
        self.assertIn("PHASE_SUCCESS", result.stdout)
        self.assertEqual((guest / ".config/custom/custom.conf").read_bytes(), b"custom config\n")
        self.assertEqual(Path(self.env["TEST_EVENTS"]).read_text().splitlines(), ["omarchy-restart-shell"])
        self.assertFalse((guest / ".local/state/omarchy/settings").exists())

    def test_host_config_broken_or_nonregular_selected_background_prevents_readiness(self):
        self.apply_patch()
        host, _ = self.host_restore_fixture()
        current = host / ".local/state/omarchy/current"
        current.mkdir(parents=True)
        background = current / "background"
        for target in (self.root / "missing.png", self.root / "directory.png"):
            with self.subTest(target=target.name):
                if target.name == "directory.png":
                    target.mkdir()
                background.unlink(missing_ok=True)
                background.symlink_to(target)
                result = self.restore_acceptance()
                self.assert_restore_failed(result)
                self.assertIn("background", result.stdout + result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root can read files without read permission")
    def test_host_config_unreadable_selected_background_prevents_readiness(self):
        self.apply_patch()
        host, _ = self.host_restore_fixture()
        asset = self.host_background(host)
        asset.chmod(0)
        result = self.restore_acceptance()
        self.assert_restore_failed(result)
        self.assertIn("readable regular file", result.stdout + result.stderr)

    def test_host_config_apply_and_transfer_failures_prevent_readiness(self):
        self.apply_patch()
        host, _ = self.host_restore_fixture()
        self.host_background(host)
        settings = host / ".local/state/omarchy/settings"
        settings.mkdir()
        (settings / "weather.json").write_bytes(b'{"name":"Athens","latitude":37.98,"longitude":23.72}\n')
        for variable, failure in (("TEST_FAIL_COMMAND", "omarchy-theme-set"),
                                  ("TEST_FAIL_COMMAND", "omarchy-theme-bg-set"),
                                  ("TEST_FAIL_COMMAND", "omarchy-restart-shell"),
                                  ("TEST_FAIL_TRANSFER", "settings"),
                                  ("TEST_FAIL_TRANSFER", "settings-source"),
                                  ("TEST_FAIL_TRANSFER", "background")):
            with self.subTest(failure=failure):
                self.env.pop("TEST_FAIL_COMMAND", None)
                self.env.pop("TEST_FAIL_TRANSFER", None)
                self.env[variable] = failure
                result = self.restore_acceptance()
                self.assert_restore_failed(result)

    def test_automated_acceptance_never_restores_host_config(self):
        self.apply_patch()
        host, guest = self.host_restore_fixture()
        self.host_background(host)
        result = self.restore_acceptance(interactive=False)
        self.assert_success(result)
        self.assertIn("PHASE_SUCCESS", result.stdout)
        self.assertFalse(Path(self.env["TEST_EVENTS"]).exists())
        self.assertFalse(any(guest.iterdir()))

    def test_acceptance_uses_the_active_tree(self):
        self.apply_patch()
        for sync, expected in ((False, "/usr/share/omarchy"), (True, "/home/omarchy/.local/share/omarchy")):
            with self.subTest(worktree=sync):
                log = self.root / "run/ssh.log"
                log.unlink(missing_ok=True)
                result = self.acceptance(interactive=False, sync=sync)
                self.assert_success(result)
                self.assertIn("PHASE_SUCCESS", result.stdout)
                commands = log.read_text().splitlines()
                acceptance = next(command for command in commands if "OMARCHY_ACCEPTANCE_DIR=" in command)
                self.assertIn("OMARCHY_PATH=" + expected + " ", acceptance)

    def test_qemu_extra_wildcard_remains_literal(self):
        self.apply_patch()
        (self.root / "glob-expanded").write_text("fixture")
        script = r'''log() { :; }
qemu-system-x86_64() { printf '<%s>\n' "$@"; }
FAKE_PCI_PIDS=()
FAKE_PCI=()
SMBIOS=()
ACPI_HIDS=()
BATTERY=false
CPU_ID=
NO_NETWORK=false
SSH_PORT=2222
MEMORY=512
OVMF_CODE=code.fd
BASE_OVMF=vars.fd
QMP_SOCK=qmp.sock
PIDFILE=qemu.pid
OMARCHY_ISO_QEMU_EXTRA='-name *'
'''
        result = self.shell(script + self.function("start_vm") + "\nstart_vm disk.qcow2 serial.log\n")
        self.assert_success(result)
        self.assertEqual(result.stdout.splitlines()[-2:], ["<-name>", "<*>"])

    def test_activated_sync_removes_stale_guest_files_and_copies_git(self):
        self.apply_patch()
        tree = self.root / "sync-source"
        (tree / ".git").mkdir(parents=True)
        (tree / ".git/config").write_text("fixture metadata\n")
        (tree / "new-command").write_text("new command\n")
        guest = self.root / "guest"
        target = guest / ".local/share/omarchy"
        target.mkdir(parents=True)
        (target / "deleted-command").write_text("stale command\n")
        self.env.update(TEST_GUEST=str(guest), TEST_SOURCE=str(tree))
        script = r'''log() { :; }
ssh_guest() { (cd "$TEST_GUEST" && bash -c "$*"); }
SYNC_DIR="$TEST_SOURCE"
SYNC_ALL=true
OMARCHY_ISO_ACTIVATE_SYNC=1
'''
        result = self.shell(script + self.function("sync_omarchy") + "\nsync_omarchy\n")
        self.assert_success(result)
        self.assertFalse((target / "deleted-command").exists(), "stale guest command survived sync")
        self.assertEqual((target / "new-command").read_text(), "new command\n")
        self.assertEqual((target / ".git/config").read_text(), "fixture metadata\n")

    def test_conflicting_harness_patch_refuses_without_modifying_file(self):
        source = self.harness.read_text()
        self.harness.write_text(source.replace('  establish_session\n  sync_omarchy\n',
                                               '  establish_session_custom\n  sync_omarchy\n'))
        before = self.harness.read_bytes()
        result = self.shell('omarchy_dev_ensure_iso_harness_patch')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.harness.read_bytes(), before)

    def test_markers_alone_do_not_identify_a_current_patch(self):
        with self.harness.open("a") as stream:
            stream.write("\n# OMARCHY_ISO_QEMU_EXTRA OMARCHY_ISO_ACTIVATE_SYNC OMARCHY_ISO_SKIP_TESTS OMARCHY_DEV_SYNC_HOST_CONFIG\n")
        result = self.shell('omarchy_dev_harness_is_patched')
        self.assertNotEqual(result.returncode, 0, "stale marker-only harness was accepted")

    def test_applying_harness_patch_is_idempotent(self):
        self.assert_success(self.shell('omarchy_dev_ensure_iso_harness_patch'))
        first = self.harness.read_bytes()
        self.assert_success(self.shell('omarchy_dev_ensure_iso_harness_patch'))
        self.assertEqual(self.harness.read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
