"""Tests on a small tree in a temporary directory (must be on APFS; macOS's TMPDIR is).

uv run python -m unittest discover -s tests -v   (from projects/apfs-compress)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_kernel_managed_xattr_values_may_differ(self) -> None:
        a = {"com.apple.provenance": b"\x01\x02\x00" + b"A" * 8, "org.example.note": b"kept"}
        b = {"com.apple.provenance": b"\x01\x02\x00" + b"B" * 8, "org.example.note": b"kept"}
        self.assertEqual(verify.compare_xattrs(a, b), (True, True))
        self.assertEqual(verify.compare_xattrs(a, a), (True, False))
        self.assertEqual(verify.compare_xattrs(a, {**b, "org.example.note": b"changed"}), (False, True))
        self.assertEqual(verify.compare_xattrs(a, {"org.example.note": b"kept"}), (False, False))

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
