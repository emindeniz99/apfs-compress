"""Compare an original tree with its compressed copy, and read decmpfs compression types.

Everything here is read-only. For every entry (symlinks are not followed) the two trees must
have the same relative paths and types; the same mode, uid, gid and BSD flags except
UF_COMPRESSED; the same mtime for files and directories; the same symlink targets; the same
size and SHA-256 for regular files; the same extended attributes (listxattr does not list the
hidden compression attributes); and the same extended ACL. Two known differences are counted,
not failed: ditto does not keep a symlink's own mtime, and macOS writes com.apple.provenance and
com.apple.macl values that no copy can keep (only their presence is compared).
"""

from __future__ import annotations

import collections
import ctypes
import ctypes.util
import hashlib
import os
import stat
import struct
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

XATTR_NOFOLLOW = 0x0001
XATTR_SHOWCOMPRESSION = 0x0020
UF_COMPRESSED = 0x20
DEFAULT_THREADS = os.cpu_count() or 8
# Written by macOS itself, so a copy cannot keep their values and only their presence can match:
# com.apple.provenance (macOS 13+): an 11-byte ID of the app whose process created the file;
# com.apple.macl: the TCC/sandbox list of apps granted access to the file, which gains an entry
# for the copying process.
KERNEL_MANAGED_XATTRS = frozenset({"com.apple.provenance", "com.apple.macl"})
ACL_TYPE_EXTENDED = 0x00000100
DECMPFS_MAGIC = 0x636D7066  # "fpmc"
# decmpfs compression types (xnu bsd/sys/decmpfs.h and AppleFSCompression): xattr = data in
# the com.apple.decmpfs attribute, rsrc = data in the resource fork.
TYPE_NAMES = {
    3: "ZLIB xattr",
    4: "ZLIB rsrc",
    7: "LZVN xattr",
    8: "LZVN rsrc",
    9: "raw xattr",
    10: "raw rsrc",
    11: "LZFSE xattr",
    12: "LZFSE rsrc",
    13: "LZBITMAP xattr",
    14: "LZBITMAP rsrc",
}

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_libc.listxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
_libc.listxattr.restype = ctypes.c_ssize_t
_libc.getxattr.argtypes = [
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_uint32,
    ctypes.c_int,
]
_libc.getxattr.restype = ctypes.c_ssize_t
_libc.setxattr.argtypes = [
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_uint32,
    ctypes.c_int,
]
_libc.setxattr.restype = ctypes.c_int
_libc.acl_get_link_np.argtypes = [ctypes.c_char_p, ctypes.c_int]
_libc.acl_get_link_np.restype = ctypes.c_void_p
_libc.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
_libc.acl_to_text.restype = ctypes.c_void_p
_libc.acl_free.argtypes = [ctypes.c_void_p]


def _getxattr(path: bytes, name: bytes, opts: int) -> bytes | None:
    n = _libc.getxattr(path, name, None, 0, 0, opts)
    if n < 0:
        return None
    buf = ctypes.create_string_buffer(max(n, 1))
    n = _libc.getxattr(path, name, buf, n, 0, opts)
    return buf.raw[: max(n, 0)]


def has_xattr(path: str, name: str) -> bool:
    return _getxattr(path.encode(), name.encode(), XATTR_NOFOLLOW) is not None


def xattrs(path: str) -> dict[str, bytes]:
    """Visible extended attributes of path itself (a symlink is not followed)."""
    b = path.encode()
    n = _libc.listxattr(b, None, 0, XATTR_NOFOLLOW)
    if n <= 0:
        return {}
    buf = ctypes.create_string_buffer(n)
    n = _libc.listxattr(b, buf, n, XATTR_NOFOLLOW)
    out: dict[str, bytes] = {}
    for name in buf.raw[: max(n, 0)].split(b"\0"):
        if name:
            out[name.decode(errors="replace")] = _getxattr(b, name, XATTR_NOFOLLOW) or b""
    return out


def restore_empty_xattrs(original: str, copy: str) -> int:
    """Write into the copy the zero-length extended attributes that ditto leaves out.

    ditto does not copy an attribute whose value is empty, such as the empty
    com.apple.cs.CodeSignature of an ad-hoc signed static library. Returns how many it wrote.
    """
    restored = 0
    for rel in walk(original):
        pa, pb = os.path.join(original, rel), os.path.join(copy, rel)
        if not os.path.lexists(pb):
            continue
        have = xattrs(pb)
        for name, value in xattrs(pa).items():
            if value == b"" and name not in have:
                if _libc.setxattr(pb.encode(), name.encode(), None, 0, 0, XATTR_NOFOLLOW) != 0:
                    raise OSError(ctypes.get_errno(), f"setxattr {name} on {pb}")
                restored += 1
    return restored


def compare_xattrs(a: dict[str, bytes], b: dict[str, bytes]) -> tuple[bool, bool]:
    """(equal, kernel_value_differs): the same names, and the same values except for
    kernel-managed attributes, whose values may differ."""
    if set(a) != set(b):
        return False, False
    differs = [k for k in a if a[k] != b[k]]
    return all(k in KERNEL_MANAGED_XATTRS for k in differs), bool(differs)


def acl_text(path: str) -> str | None:
    """The extended ACL of path itself as text, or None when it has none."""
    acl = _libc.acl_get_link_np(path.encode(), ACL_TYPE_EXTENDED)
    if not acl:
        return None
    try:
        text = _libc.acl_to_text(acl, None)
        if not text:
            return ""
        try:
            return ctypes.string_at(text).decode()
        finally:
            _libc.acl_free(text)
    finally:
        _libc.acl_free(acl)


def compression_type(path: str) -> int | None:
    """The decmpfs compression type of a regular file, or None when it is not compressed."""
    header = _getxattr(path.encode(), b"com.apple.decmpfs", XATTR_NOFOLLOW | XATTR_SHOWCOMPRESSION)
    if not header or len(header) < 16:
        return None
    magic, ctype = struct.unpack("<II", header[:8])
    return ctype if magic == DECMPFS_MAGIC else None


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(root: str) -> dict[str, os.stat_result]:
    """lstat of every entry under root, keyed by relative path."""
    out: dict[str, os.stat_result] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            out[os.path.relpath(p, root)] = os.lstat(p)
    return out


@dataclass
class TreeStats:
    files: int = 0
    logical: int = 0
    allocated: int = 0
    types: dict[str, int] = field(default_factory=dict)


def tree_stats(root: str) -> TreeStats:
    """Logical and allocated bytes and decmpfs types of a tree. Allocated counts st_blocks,
    which for a compressed file is its compressed storage."""
    st = TreeStats()
    types: collections.Counter[str] = collections.Counter()
    for rel, s in walk(root).items():
        st.allocated += s.st_blocks * 512
        if stat.S_ISREG(s.st_mode):
            st.files += 1
            st.logical += s.st_size
            t = compression_type(os.path.join(root, rel))
            types["not compressed" if t is None else TYPE_NAMES.get(t, f"type {t}")] += 1
    st.types = dict(types.most_common())
    return st


@dataclass
class VerifyResult:
    entries: int
    files: int
    mismatches: list[str]
    symlink_mtimes: int
    macos_xattr_values: int

    @property
    def passed(self) -> bool:
        return not self.mismatches


def compare(
    original: str, copy: str, log: Callable[[str], None] = print, threads: int = DEFAULT_THREADS
) -> VerifyResult:
    a, b = walk(original), walk(copy)
    bad: list[str] = []

    def mismatch(msg: str) -> None:
        bad.append(msg)
        if len(bad) <= 40:
            log(f"MISMATCH {msg}")

    for rel in sorted(set(a) ^ set(b)):
        mismatch(f"only in {'original' if rel in a else 'copy'}: {rel}")
    files: list[str] = []
    symlink_mtimes = macos_xattr_values = 0
    for rel in sorted(set(a) & set(b)):
        sa, sb = a[rel], b[rel]
        pa, pb = os.path.join(original, rel), os.path.join(copy, rel)
        if stat.S_IFMT(sa.st_mode) != stat.S_IFMT(sb.st_mode):
            mismatch(f"type: {rel}")
            continue
        for key in ("st_mode", "st_uid", "st_gid"):
            if getattr(sa, key) != getattr(sb, key):
                mismatch(f"{key} {getattr(sa, key):o} != {getattr(sb, key):o}: {rel}")
        if not stat.S_ISDIR(sa.st_mode) and int(sa.st_mtime) != int(sb.st_mtime):
            if stat.S_ISLNK(sa.st_mode):
                symlink_mtimes += 1
            else:
                mismatch(f"mtime: {rel}")
        if (sa.st_flags & ~UF_COMPRESSED) != (sb.st_flags & ~UF_COMPRESSED):
            mismatch(f"flags {sa.st_flags:#x} != {sb.st_flags:#x}: {rel}")
        if stat.S_ISLNK(sa.st_mode) and os.readlink(pa) != os.readlink(pb):
            mismatch(f"symlink target: {rel}")
        equal, kernel_differs = compare_xattrs(xattrs(pa), xattrs(pb))
        if not equal:
            mismatch(f"extended attributes: {rel}")
        macos_xattr_values += kernel_differs
        if acl_text(pa) != acl_text(pb):
            mismatch(f"acl: {rel}")
        if stat.S_ISREG(sa.st_mode):
            if sa.st_size != sb.st_size:
                mismatch(f"size: {rel}")
            else:
                files.append(rel)
    log(f"entries {len(a)} / {len(b)}; hashing {len(files)} regular files in both trees, {threads} threads")

    def same(rel: str) -> tuple[str, bool]:
        return rel, sha256(os.path.join(original, rel)) == sha256(os.path.join(copy, rel))

    step = max(len(files) // 10, 1)
    with ThreadPoolExecutor(threads) as pool:
        for i, (rel, ok) in enumerate(pool.map(same, files), 1):
            if not ok:
                mismatch(f"sha256: {rel}")
            if i % step == 0 or i == len(files):
                log(f"  hashed {i}/{len(files)}")
    return VerifyResult(len(a), len(files), bad, symlink_mtimes, macos_xattr_values)
