"""Check that compression changed no code signature. Read-only.

`codesign --verify --deep --strict` runs on every top-level .app bundle of the original and
on its counterpart in the copy, then `codesign --verify --strict` on every Mach-O file. A file
that verifies in one tree must verify in the other. Files unsigned in both are expected: build
tools and libraries that were never signed.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def verify(path: str, deep: bool = False) -> tuple[bool, str]:
    cmd = ["codesign", "--verify", "--strict", *(["--deep"] if deep else []), path]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1]


def find(root: str) -> tuple[list[str], list[str]]:
    """Top-level .app bundles (not ones nested in another bundle) and Mach-O files, relative."""
    apps: list[str] = []
    machos: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if not any(part.endswith(".app") for part in rel.split(os.sep)):
            apps += [os.path.normpath(os.path.join(rel, d)) for d in dirnames if d.endswith(".app")]
        for name in filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            with open(p, "rb") as f:
                if f.read(4) in MACHO_MAGIC:
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


def compare(original: str, copy: str, log: Callable[[str], None] = print, threads: int = 8) -> CodesignResult:
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
    with ThreadPoolExecutor(threads) as pool:
        for rel, ok_a, ok_b, err in pool.map(both, machos):
            if ok_a and ok_b:
                valid += 1
            elif not ok_a and not ok_b:
                unsigned += 1
            else:
                different.append(rel)
                log(f"DIFFERENT {rel}: original {'valid' if ok_a else 'invalid'}, copy {err or 'valid'}")
    log(f"Mach-O files {len(machos)}: valid in both {valid}, unsigned or invalid in both {unsigned}")
    return CodesignResult(len(apps), len(machos), valid, unsigned, different)
