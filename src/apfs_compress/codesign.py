"""Check that compression changed no code signature. Read-only.

`codesign --verify --deep --strict` runs on every top-level .app bundle of the original (the
tree itself when it is one) and on its counterpart in the copy, then `codesign --verify
--strict` on every Mach-O file and every other file signed through extended attributes
(com.apple.cs.CodeDirectory, such as an ad-hoc signed static library). A file that verifies in
one tree must verify in the other. Files unsigned in both are expected: build
tools and libraries that were never signed.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .verify import has_xattr

DEFAULT_THREADS = os.cpu_count() or 8
FAT_MAGIC = b"\xca\xfe\xba\xbe"
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", FAT_MAGIC, b"\xbe\xba\xfe\xca"}


def is_macho(header: bytes) -> bool:
    """Whether the first 8 bytes of a file are a Mach-O or fat (universal) header. A Java class
    file also starts with CA FE BA BE; its next four bytes are its version, 45 (JDK 1.1) or more,
    where a fat binary has its architecture count."""
    if header[:4] == FAT_MAGIC:
        return len(header) >= 8 and int.from_bytes(header[4:8], "big") < 45
    return header[:4] in MACHO_MAGIC


def verify(path: str, deep: bool = False) -> tuple[bool, str]:
    cmd = ["codesign", "--verify", "--strict", *(["--deep"] if deep else []), path]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1]


def find(root: str) -> tuple[list[str], list[str]]:
    """Top-level .app bundles (not ones nested in another bundle; "." when root is one) and
    signable files: Mach-O files and files signed through extended attributes. Paths are
    relative."""
    root_is_app = os.path.normpath(root).endswith(".app")
    apps: list[str] = ["."] if root_is_app else []
    machos: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if not root_is_app and not any(part.endswith(".app") for part in rel.split(os.sep)):
            apps += [os.path.normpath(os.path.join(rel, d)) for d in dirnames if d.endswith(".app")]
        for name in filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            with open(p, "rb") as f:
                macho = is_macho(f.read(8))
            if macho or has_xattr(p, "com.apple.cs.CodeDirectory"):
                machos.append(os.path.relpath(p, root))
    return apps, machos


@dataclass
class CodesignResult:
    bundles: int
    machos: int
    valid: int
    unsigned: int
    different: list[str]

    @property
    def passed(self) -> bool:
        return not self.different


def compare(
    original: str, copy: str, log: Callable[[str], None] = print, threads: int = DEFAULT_THREADS
) -> CodesignResult:
    apps, machos = find(original)
    different: list[str] = []
    for app in apps:
        ok_a, _ = verify(os.path.join(original, app), deep=True)
        ok_b, err = verify(os.path.join(copy, app), deep=True)
        copy_state = "valid" if ok_b else f"invalid: {err}"
        log(f"bundle {app}: original {'valid' if ok_a else 'invalid'}, copy {copy_state}")
        if ok_a != ok_b:
            different.append(app)

    def both(rel: str) -> tuple[str, bool, bool, str]:
        ok_a, _ = verify(os.path.join(original, rel))
        ok_b, err = verify(os.path.join(copy, rel))
        return rel, ok_a, ok_b, err

    valid = unsigned = 0
    step = max(len(machos) // 10, 1)
    log(f"checking {len(machos)} Mach-O files in both trees, {threads} threads")
    with ThreadPoolExecutor(threads) as pool:
        for i, (rel, ok_a, ok_b, err) in enumerate(pool.map(both, machos), 1):
            if i % step == 0 or i == len(machos):
                log(f"  checked {i}/{len(machos)}")
            if ok_a and ok_b:
                valid += 1
            elif not ok_a and not ok_b:
                unsigned += 1
            else:
                different.append(rel)
                copy_state = "valid" if ok_b else f"invalid: {err}"
                log(f"DIFFERENT {rel}: original {'valid' if ok_a else 'invalid'}, copy {copy_state}")
    log(f"Mach-O files {len(machos)}: valid in both {valid}, unsigned or invalid in both {unsigned}")
    return CodesignResult(len(apps), len(machos), valid, unsigned, different)
