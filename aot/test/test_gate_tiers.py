#!/usr/bin/env python3
"""Gate driver tests: local tiny Git repositories and Bash stub tiers.

No compiler, EigenScript VM, real suite, or benchmark is invoked. An executable
fake VM is present solely for the driver's existence check and must never run.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

DRIVER = Path(__file__).resolve().parents[1] / "gate_tiers.sh"
TIERS = [
    ("core", "aot/core_check.sh", "core check"),
    ("aot", "aot/test/run.sh", "AOT suite"),
    ("selfhost", "test/run.sh", "self-host suite"),
    ("canary", "aot/canary_dmg.sh", "DMG canary"),
    ("stdlib", "aot/stdlib_sweep.sh", "stdlib sweep"),
    ("corpus", "aot/corpus_diff.sh", "corpus run-differential"),
    ("leak", "aot/test/leak.sh", "leak tier"),
]
STUB = '''#!/usr/bin/env bash
set -eu
[[ "$EIGS" == "$EXPECT_RUNTIME/src/eigenscript" ]] || exit 91
[[ "$EIGS_DIR" == "$EXPECT_RUNTIME" ]] || exit 92
[[ "$EIGS_ROOT" == "$EXPECT_RUNTIME" ]] || exit 93
[[ "$LIB_DIR" == "$EXPECT_RUNTIME/lib" ]] || exit 94
[[ "$DMG_DIR" == "$EXPECT_DMG" ]] || exit 95
[[ "$AOT_REPO" == "$EXPECT_AOT" ]] || exit 96
[[ "$PWD" == "$EXPECT_AOT/{cwd}" ]] || exit 97
printf '%s\\n' '{name}' >> "$TRACE"
printf '%s\\n' 'COUNTS {name}: passed={count} failed=0'
if [[ "${{FAIL_TIER:-}}" == '{name}' ]]; then exit "${{FAIL_CODE:-23}}"; fi
exit 0
'''


def verdict(codes=None):
    codes = codes or {}
    fields = " ".join(f"{name}={codes.get(name, 0)}" for name in
                      ("aot", "selfhost", "canary", "stdlib", "corpus", "leak", "core"))
    return "=== verdict " + fields + " ==="


class GateTests(unittest.TestCase):
    def git(self, *args):
        return subprocess.check_output(["git", "-c", "user.name=Scratch gate test",
                                        "-c", "user.email=gate-test@example.invalid",
                                        "-C", str(self.runtime), *args], stderr=subprocess.STDOUT).decode().strip()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gate-driver-selftest-")
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime with spaces"
        self.aot = self.root / "ouro repo"
        self.dmg = self.root / "DMG"
        self.trace = self.root / "executed"
        for path in (self.runtime / "src", self.runtime / "lib", self.aot / ".devcontainer", self.dmg):
            path.mkdir(parents=True)
        (self.runtime / ".gitignore").write_text("src/eigenscript\n")
        (self.runtime / "src/vm.c").write_text("/* synthetic oracle source */\n")
        (self.runtime / "lib/test.eigs").write_text("# synthetic, never executed\n")
        (self.dmg / "dmg.eigs").write_text("# synthetic, never executed\n")
        self.vm = self.runtime / "src/eigenscript"
        self.vm.write_text("#!/bin/bash\nprintf 'UNEXPECTED_VM_EXECUTION\\n' >&2\nexit 99\n")
        self.vm.chmod(0o700)
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "Synthetic pinned runtime")
        self.commit = self.git("rev-parse", "HEAD")
        self.git("tag", "v0.43.0")
        self.dockerfile = self.aot / ".devcontainer/Dockerfile"
        self.pin("v0.43.0")
        for index, (name, relative, _) in enumerate(TIERS, 1):
            path = self.aot / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            # The self-host tier runs from repo root; others from aot/.
            cwd = "" if name == "selfhost" else "aot"
            content = STUB.format(cwd=cwd, name=name, count=index)
            if name == "selfhost":
                content = content.replace('$EXPECT_AOT/"', '$EXPECT_AOT"')
            path.write_text(content)
        self.baseline = self.aot / "aot/test/canary/dmg_cpu_instrs.out"
        self.baseline.parent.mkdir(parents=True)
        self.baseline.write_text("synthetic nonempty canary reference\n")
        self.env = dict(os.environ, AOT_REPO=str(self.aot), EIGS_CO=str(self.runtime), DMG_DIR=str(self.dmg),
                        EXPECT_AOT=str(self.aot), EXPECT_RUNTIME=str(self.runtime), EXPECT_DMG=str(self.dmg),
                        TRACE=str(self.trace), GATE_LOG="", FAIL_TIER="", FAIL_CODE="23",
                        EIGS="/wrong/vm", EIGS_DIR="/wrong/runtime", EIGS_ROOT="/wrong/corpus", LIB_DIR="/wrong/lib")

    def tearDown(self):
        self.temporary.cleanup()

    def pin(self, value):
        self.dockerfile.write_text(f"FROM scratch\nARG EIGS_REF={value}\n")

    def run_gate(self, **overrides):
        self.trace.unlink(missing_ok=True)
        result = subprocess.run(["bash", str(DRIVER)], env=dict(self.env, **overrides),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=10, cwd=self.root)
        output = result.stdout
        log = overrides.get("GATE_LOG", "")
        if log and Path(log).is_file():
            output += Path(log).read_text()
        self.assertNotIn("UNEXPECTED_VM_EXECUTION", output)
        return result.returncode, output

    def executed(self):
        return self.trace.read_text().splitlines() if self.trace.exists() else []

    def completed(self, rc, output, expected_codes=None, missing=()):
        self.assertEqual(rc, 1 if expected_codes else 0, output)
        self.assertEqual(self.executed(), [name for name, _, _ in TIERS if name not in missing])
        self.assertEqual([line for line in output.splitlines() if line.startswith("=== verdict ")], [verdict(expected_codes)])
        self.assertEqual(output.splitlines().count("TIERS_DONE"), 1)
        self.assertEqual(output.splitlines()[-2:], [verdict(expected_codes), "TIERS_DONE"])
        self.assertEqual([line for line in output.splitlines() if line.startswith("COUNTS ")],
                         [f"COUNTS {name}: passed={index} failed=0" for index, (name, _, _) in enumerate(TIERS, 1)
                          if name not in missing])
        self.assertEqual([line for line in output.splitlines() if line.startswith("=== ") and not line.startswith("=== verdict")],
                         ["=== " + heading + " ===" for _, _, heading in TIERS])

    def refused(self, evidence, **overrides):
        rc, output = self.run_gate(**overrides)
        self.assertEqual(rc, 2, output)
        self.assertIn("GATE REFUSED:", output)
        self.assertIn(evidence, output)
        self.assertEqual(self.executed(), [])
        self.assertNotIn("=== verdict ", output)
        self.assertNotIn("TIERS_DONE", output)
        return output

    def test_tag_pin_paths_counts_and_order(self):
        rc, output = self.run_gate()
        self.completed(rc, output)
        self.assertIn(f"source={self.dockerfile}", output)
        self.assertIn(f"ref=v0.43.0 resolved_commit={self.commit} actual_commit={self.commit}", output)

    def test_commit_pin(self):
        self.pin(self.commit)
        self.completed(*self.run_gate())

    def test_missing_canary_baseline_is_refused(self):
        self.baseline.unlink()
        self.refused(f"canary baseline must be a readable nonempty regular file: {self.baseline}")

    def test_empty_canary_baseline_is_refused(self):
        self.baseline.write_bytes(b"")
        self.refused(f"canary baseline must be a readable nonempty regular file: {self.baseline}")

    def test_directory_canary_baseline_is_refused(self):
        self.baseline.unlink()
        self.baseline.mkdir()
        self.refused(f"canary baseline must be a readable nonempty regular file: {self.baseline}")

    def test_annotated_tag_pin(self):
        self.git("tag", "-a", "annotated-pin", "-m", "Synthetic annotated tag")
        self.pin("annotated-pin")
        self.completed(*self.run_gate())

    def test_quoted_pin_with_comment(self):
        self.pin('"v0.43.0" # version pin')
        self.completed(*self.run_gate())

    def test_checkout_alias_and_dmg_sibling(self):
        self.completed(*self.run_gate(EIGS_CO="", EIGS_DIR=str(self.runtime), DMG_DIR=""))

    def test_every_tier_failure_preserves_rc_and_later_tiers(self):
        for (name, _, _), code in zip(TIERS, (1, 2, 7, 23, 124, 137, 42)):
            with self.subTest(tier=name, code=code):
                self.completed(*self.run_gate(FAIL_TIER=name, FAIL_CODE=str(code)), {name: code})

    def test_every_missing_tier_is_nonzero(self):
        for name, relative, _ in TIERS:
            with self.subTest(tier=name):
                path = self.aot / relative
                original = path.read_bytes()
                path.unlink()
                try:
                    rc, output = self.run_gate()
                    self.completed(rc, output, {name: 127}, missing=(name,))
                    self.assertIn(str(path), output)
                finally:
                    path.write_bytes(original)

    def test_every_empty_tier_is_nonzero(self):
        for name, relative, _ in TIERS:
            with self.subTest(tier=name):
                path = self.aot / relative
                original = path.read_bytes()
                path.write_bytes(b"")
                try:
                    self.completed(*self.run_gate(), {name: 127}, missing=(name,))
                finally:
                    path.write_bytes(original)

    def test_dirty_tracked_oracle_is_refused(self):
        (self.runtime / "src/vm.c").write_text("/* changed */\n")
        self.refused("uncommitted changes")

    def test_dirty_untracked_oracle_is_refused(self):
        (self.runtime / "untracked.eigs").write_text("# would otherwise enter a corpus\n")
        self.refused("uncommitted changes")

    def test_clean_wrong_commit_is_refused(self):
        (self.runtime / "src/vm.c").write_text("/* newer committed source */\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Synthetic wrong HEAD")
        actual = self.git("rev-parse", "HEAD")
        output = self.refused(f"Dockerfile pin v0.43.0 resolves to {self.commit}")
        self.assertIn(f"actual_commit={actual}", output)

    def test_unresolvable_pin_is_refused(self):
        self.pin("missing-tag")
        self.refused("cannot resolve Dockerfile pin to a commit")

    def test_missing_pin_is_refused(self):
        self.dockerfile.write_text("FROM scratch\n")
        self.refused("exactly one nonempty ARG EIGS_REF")

    def test_duplicate_pin_is_refused(self):
        self.dockerfile.write_text("ARG EIGS_REF=v0.43.0\nARG EIGS_REF=v0.43.0\n")
        self.refused("exactly one nonempty ARG EIGS_REF")

    def test_missing_binary_is_refused(self):
        self.vm.unlink()
        self.refused("oracle binary is not executable")

    def test_refusal_replaces_a_previous_success_log(self):
        log = str(self.root / "logs/gate.log")
        self.completed(*self.run_gate(GATE_LOG=log))
        (self.runtime / "untracked.eigs").write_text("# dirty\n")
        self.refused("uncommitted changes", GATE_LOG=log)

    def test_logging_cannot_dirty_the_oracle(self):
        log = self.runtime / "new-gate.log"
        self.refused("GATE_LOG must be outside the oracle checkout", GATE_LOG=str(log))
        self.assertFalse(log.exists())
        self.assertEqual(self.git("status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
