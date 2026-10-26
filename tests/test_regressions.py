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
        end = source.index("\n" + closing + "\n", start) + 3
        return source[start:end]

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
        script += overrides + "\n" + self.function("acceptance_phase")
        script += "\nif acceptance_phase; then printf 'PHASE_SUCCESS\\n'; else printf 'PHASE_FAILURE\\n'; fi\n"
        return self.shell(script)

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
