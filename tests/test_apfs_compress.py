"""Tests on a small tree in a temporary directory (must be on APFS; macOS's TMPDIR is).

uv run python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import apfs_compress
from apfs_compress import codesign, verify

CLI = [sys.executable, "-m", "apfs_compress"]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*CLI, *args], capture_output=True, text=True, check=False)


class ApfsCompressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.src = root / "tree"
        (self.src / "sub").mkdir(parents=True)
        (self.src / "text.txt").write_text("compressible line of text\n" * 40000)
        (self.src / "random.bin").write_bytes(os.urandom(200_000))
        tool = self.src / "sub" / "tool.sh"
        tool.write_text("#!/bin/sh\necho hi\n" * 2000)
        tool.chmod(0o755)
        (self.src / "link").symlink_to("sub/tool.sh")
        (self.src / "outside").symlink_to("../elsewhere")  # leaves the tree, like a shared SDK link
        subprocess.run(["xattr", "-w", "org.example.note", "kept", str(self.src / "text.txt")], check=True)
        empty = self.src / "sub" / "empty-attr.a"
        empty.write_bytes(b"!<arch>\n" + b"x" * 5000)
        subprocess.run(["codesign", "-s", "-", str(empty)], check=True, capture_output=True)
        subprocess.run(["xattr", "-w", "org.example.empty", "", str(empty)], check=True)
        shutil.copy("/bin/ls", self.src / "sub" / "ls")  # a signed Mach-O (copy2 would copy SF_ flags)
        self.copy = root / ".apfscmp-tree"
        self.orig = root / ".apfscmp-orig-tree"
        self.report = root / ".apfscmp-tree.json"

    def copy_ok(self) -> dict:
        r = run("copy", str(self.src))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(self.report.read_text())

    def test_copy_verifies_and_compresses(self) -> None:
        before = verify.sha256(str(self.src / "text.txt"))
        data = self.copy_ok()
        self.assertEqual(data["result"], "PASS")
        self.assertEqual(data["verify"]["mismatches"], [])
        compressed = {k: v for k, v in data["copy_stats"]["types"].items() if k != "not compressed"}
        self.assertTrue(compressed, data["copy_stats"]["types"])
        self.assertIsNotNone(verify.compression_type(str(self.copy / "text.txt")))
        self.assertIsNone(verify.compression_type(str(self.src / "text.txt")))
        self.assertEqual(verify.sha256(str(self.src / "text.txt")), before)
        self.assertEqual(os.readlink(self.copy / "outside"), "../elsewhere")
        self.assertEqual(verify.xattrs(str(self.copy / "text.txt")).get("org.example.note"), b"kept")

    def test_empty_xattrs_and_xattr_signatures_survive(self) -> None:
        data = self.copy_ok()
        self.assertGreaterEqual(data["empty_xattrs_restored"], 1)
        names = verify.xattrs(str(self.copy / "sub" / "empty-attr.a"))
        self.assertEqual(names.get("org.example.empty"), b"")
        self.assertIn("com.apple.cs.CodeDirectory", names)
        _, signable = codesign.find(str(self.src))
        self.assertIn(os.path.join("sub", "empty-attr.a"), signable)
        self.assertEqual(data["codesign"]["different"], [])

    def test_tree_that_is_an_app_bundle_is_deep_verified(self) -> None:
        app = Path(self.tmp.name) / "Tool.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        shutil.copy("/bin/ls", app / "Contents" / "MacOS" / "tool")
        info = app / "Contents" / "Info.plist"
        info.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
            "<key>CFBundleExecutable</key><string>tool</string>"
            "<key>CFBundleIdentifier</key><string>org.example.tool</string></dict></plist>\n"
        )
        subprocess.run(["codesign", "-f", "-s", "-", str(app)], check=True, capture_output=True)
        apps, _ = codesign.find(str(app))
        self.assertEqual(apps, ["."])
        other = Path(self.tmp.name) / "Other.app"
        subprocess.run(["ditto", str(app), str(other)], check=True)
        self.assertEqual(codesign.compare(str(app), str(other), log=lambda _: None).different, [])
        info.write_text(info.read_text().replace("org.example.tool", "org.example.changed"))
        self.assertIn(".", codesign.compare(str(app), str(other), log=lambda _: None).different)

    def test_quarantine_value_survives(self) -> None:
        value = "0181;669c2fe5;Homebrew Cask;BC0084AB-2C1E-4BD6-AD0E-5C2CEF83B675"
        subprocess.run(["xattr", "-w", "com.apple.quarantine", value, str(self.src / "text.txt")], check=True)
        data = self.copy_ok()
        self.assertEqual(data["quarantine_values_restored"], 1)
        names = verify.xattrs(str(self.copy / "text.txt"))
        self.assertEqual(names.get("com.apple.quarantine"), value.encode())

    def test_appledouble_files_survive(self) -> None:
        header = struct.pack(">II16sH", 0x00051607, 0x00020000, b"Mac OS X        ", 0)
        (self.src / "._text.txt").write_bytes(header)  # beside its file
        (self.src / "sub" / "._orphan").write_bytes(header)  # no file of that name
        data = self.copy_ok()
        self.assertEqual(data["appledouble_files_restored"], 2)
        self.assertEqual((self.copy / "._text.txt").read_bytes(), header)
        self.assertTrue((self.copy / "sub" / "._orphan").exists())

    def test_hard_links(self) -> None:
        os.link(self.src / "text.txt", self.src / "sub" / "text-link.txt")
        outside = Path(self.tmp.name) / "outside-link.bin"
        os.link(self.src / "random.bin", outside)
        data = self.copy_ok()
        self.assertEqual(data["verify"]["hard_link_groups"], 1)
        self.assertEqual(data["verify"]["external_hard_links"], 1)
        a, b = self.copy / "text.txt", self.copy / "sub" / "text-link.txt"
        self.assertEqual(os.stat(a).st_ino, os.stat(b).st_ino)
        self.assertNotEqual(os.stat(self.copy / "random.bin").st_ino, os.stat(outside).st_ino)

    def test_compare_finds_changes(self) -> None:
        self.copy_ok()
        (self.copy / "random.bin").write_bytes(os.urandom(200_000))
        (self.copy / "sub" / "tool.sh").chmod(0o644)
        subprocess.run(["xattr", "-d", "org.example.note", str(self.copy / "text.txt")], check=True)
        (self.copy / "extra").write_text("x")
        result = verify.compare(str(self.src), str(self.copy), log=lambda _: None)
        text = "\n".join(result.mismatches)
        self.assertFalse(result.passed)
        for needle in (
            "sha256: random.bin",
            "st_mode",
            "extended attributes: text.txt",
            "only in copy: extra",
        ):
            self.assertIn(needle, text)

    def test_unreadable_tree_fails_loud(self) -> None:
        # os.walk would skip a directory it cannot read; a tree with an unread subtree must not
        # pass, and a missing tree is exit 4, not "0 files".
        self.assertEqual(run("status", str(self.src / "missing")).returncode, 4)
        locked = self.src / "sub" / "locked"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        with self.assertRaises(PermissionError):
            verify.walk(str(self.src))

    def test_process_check_escapes_the_path(self) -> None:
        # pgrep -f takes a regular expression; "(1)" would match "1" and "[a]" would match "a".
        tree = Path(self.tmp.name) / "tree (1) [a]"
        tree.mkdir()
        user = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(tree)])
        self.addCleanup(user.wait)
        self.addCleanup(user.kill)
        users = apfs_compress.users_of(tree)
        self.assertEqual(len(users), 1, users)
        self.assertTrue(users[0].startswith(f"{user.pid} "), users)
        self.assertEqual(apfs_compress.users_of(Path(self.tmp.name) / "nobody"), [])

    def test_kernel_managed_xattr_values_may_differ(self) -> None:
        a = {"com.apple.provenance": b"\x01\x02\x00" + b"A" * 8, "org.example.note": b"kept"}
        b = {"com.apple.provenance": b"\x01\x02\x00" + b"B" * 8, "org.example.note": b"kept"}
        self.assertEqual(verify.compare_xattrs(a, b), (True, True))
        self.assertEqual(verify.compare_xattrs(a, a), (True, False))
        self.assertEqual(verify.compare_xattrs(a, {**b, "org.example.note": b"changed"}), (False, True))
        self.assertEqual(verify.compare_xattrs(a, {"org.example.note": b"kept"}), (False, False))

    def test_copy_may_gain_provenance_only(self) -> None:
        plain = {"org.example.note": b"kept"}
        stamped = {**plain, "com.apple.provenance": b"\x01\x02\x00" + b"B" * 8}
        self.assertEqual(verify.compare_xattrs(plain, stamped), (True, True))
        self.assertEqual(verify.compare_xattrs(stamped, plain), (False, False))
        self.assertEqual(verify.compare_xattrs(plain, {**plain, "com.apple.macl": b"\0"}), (False, False))
        self.assertEqual(verify.compare_xattrs(plain, {**stamped, "org.example.new": b""}), (False, False))

    def test_swap_rollback_finish(self) -> None:
        self.copy_ok()
        self.assertEqual(run("finish", str(self.src), "--yes").returncode, 2)  # not swapped yet
        self.assertEqual(run("swap", str(self.src)).returncode, 0)
        self.assertTrue(self.orig.is_dir())
        self.assertIsNotNone(verify.compression_type(str(self.src / "text.txt")))
        self.assertEqual(run("rollback", str(self.src)).returncode, 0)
        self.assertIsNone(verify.compression_type(str(self.src / "text.txt")))
        self.assertTrue(self.copy.is_dir())
        self.assertEqual(run("swap", str(self.src)).returncode, 0)
        self.assertEqual(run("finish", str(self.src)).returncode, 2)  # needs --yes
        self.assertTrue(self.orig.is_dir())
        self.assertEqual(run("finish", str(self.src), "--yes").returncode, 0)
        self.assertFalse(self.orig.exists())
        self.assertEqual(json.loads(self.report.read_text())["state"], "finished")
        # an updater rewrote the tree after finish: a new copy replaces the finished report
        (self.src / "text.txt").write_text("updated line of text\n" * 40000)
        self.assertEqual(self.copy_ok()["state"], "copied")

    def test_interrupted_swap_rollback_and_finish_recover(self) -> None:
        data = self.copy_ok()
        # a kill between the two renames of swap: SRC is missing, the original is moved aside
        self.src.rename(self.orig)
        self.assertEqual(run("swap", str(self.src)).returncode, 2)
        self.assertEqual(run("discard", str(self.src)).returncode, 2)  # would delete the only copy
        self.assertEqual(run("rollback", str(self.src)).returncode, 0)
        self.assertIsNone(verify.compression_type(str(self.src / "text.txt")))
        self.assertTrue(self.copy.is_dir())
        self.assertEqual(run("swap", str(self.src)).returncode, 0)
        # a kill between the renames of rollback and its report: the report still says swapped
        self.src.rename(self.copy)
        self.orig.rename(self.src)
        self.assertEqual(run("finish", str(self.src), "--yes").returncode, 2)  # the copy is still there
        self.assertEqual(run("rollback", str(self.src)).returncode, 0)
        self.assertEqual(json.loads(self.report.read_text())["state"], "copied")
        self.assertEqual(run("swap", str(self.src)).returncode, 0)
        # a kill between finish's delete and its report: nothing is left to delete
        shutil.rmtree(self.orig)
        self.assertEqual(run("finish", str(self.src), "--yes").returncode, 0)
        self.assertEqual(json.loads(self.report.read_text())["state"], "finished")
        self.assertEqual(data["verify"]["mismatches"], [])

    def test_swap_refuses_a_changed_original(self) -> None:
        # an app updated itself after copy: finish would delete the newer tree
        self.copy_ok()
        text = self.src / "text.txt"
        text.write_text("updated line of text\n" * 40000)
        os.utime(text, (1_700_000_000, 1_700_000_000))
        r = run("swap", str(self.src))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("the original changed", r.stderr)
        self.assertFalse(self.orig.exists())
        self.assertEqual(run("discard", str(self.src)).returncode, 0)
        self.copy_ok()
        (self.src / "added").write_text("x")
        self.assertEqual(run("swap", str(self.src)).returncode, 2)

    def test_swap_refuses_a_report_without_fingerprints(self) -> None:
        data = self.copy_ok()
        del data["verify"]["original_fingerprint"]
        self.report.write_text(json.dumps(data))
        r = run("swap", str(self.src))
        self.assertEqual(r.returncode, 2)
        self.assertIn("older version", r.stderr)

    def test_discard_cleans_up_a_copy_that_failed_after_finish(self) -> None:
        self.copy_ok()
        self.assertEqual(run("swap", str(self.src)).returncode, 0)
        self.assertEqual(run("finish", str(self.src), "--yes").returncode, 0)
        self.copy.mkdir()  # what a copy that died during ditto or verification leaves behind
        self.assertEqual(run("copy", str(self.src)).returncode, 2)
        self.assertEqual(run("discard", str(self.src)).returncode, 0)
        self.assertFalse(self.copy.exists())
        self.assertEqual(json.loads(self.report.read_text())["state"], "finished")  # that record stays
        self.assertEqual(run("discard", str(self.src)).returncode, 2)  # nothing left to discard
        # a copy deleted by hand leaves a report that blocked the next copy
        self.copy_ok()
        shutil.rmtree(self.copy)
        self.assertEqual(run("copy", str(self.src)).returncode, 2)
        self.assertEqual(run("discard", str(self.src)).returncode, 0)
        self.assertFalse(self.report.exists())
        self.assertEqual(self.copy_ok()["state"], "copied")

    def test_refusals(self) -> None:
        self.copy_ok()
        self.assertEqual(run("copy", str(self.src)).returncode, 2)  # copy exists
        (self.copy / "late").write_text("x")
        self.assertEqual(run("swap", str(self.src)).returncode, 2)  # copy changed after verification
        self.assertEqual(run("discard", str(self.src)).returncode, 0)
        self.assertFalse(self.copy.exists())
        self.assertFalse(self.report.exists())
        self.assertTrue((self.src / "text.txt").exists())

    def test_failed_report_blocks_swap(self) -> None:
        data = self.copy_ok()
        data["result"] = "FAIL"
        self.report.write_text(json.dumps(data))
        self.assertEqual(run("swap", str(self.src)).returncode, 2)
        self.assertFalse(self.orig.exists())


if __name__ == "__main__":
    unittest.main()
