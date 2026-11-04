"""apfs-compress: APFS transparent compression of a read-mostly tree, through a verified copy.

`copy` makes a compressed copy next to the original with Apple's own `ditto --noclone
--hfsCompression`, then checks every file's content, metadata and code signature against the
original and writes a report. The original is not touched. `swap` exchanges the two directory
names, but only after a passing report; `rollback` exchanges them back; `finish` deletes the
original, and only when told to. The copy sits beside the original, at the same depth, so
relative symlinks that leave the tree keep resolving. Python 3.9+, standard library only, macOS.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import codesign, verify

COPY_PREFIX = ".apfscmp-"
ORIG_PREFIX = ".apfscmp-orig-"


class Usage(Exception):
    """A precondition failed: nothing was changed."""


def paths(src: str) -> tuple[Path, Path, Path, Path]:
    s = Path(os.path.abspath(src))
    parent, name = s.parent, s.name
    return (
        s,
        parent / f"{COPY_PREFIX}{name}",
        parent / f"{ORIG_PREFIX}{name}",
        parent / f"{COPY_PREFIX}{name}.json",
    )


def log(msg: str) -> None:
    print(msg, flush=True)


def free_bytes(path: Path) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def filesystem(path: Path) -> str:
    """The filesystem type of the volume holding path ("apfs", "hfs", ...)."""
    df = subprocess.run(["df", "-P", str(path)], capture_output=True, text=True, check=False)
    lines = df.stdout.splitlines()
    if df.returncode != 0 or len(lines) < 2:
        return "unknown"
    mount_point = lines[-1].split(None, 5)[-1]  # df -P: the mount point is the sixth column
    r = subprocess.run(["diskutil", "info", "-plist", mount_point], capture_output=True, check=False)
    if r.returncode != 0:
        return "unknown"
    return str(plistlib.loads(r.stdout).get("FilesystemType", "unknown"))


def users_of(path: Path) -> list[str]:
    """Processes whose command line names the tree (a cheap check; lsof +D is too slow)."""
    # pgrep -f takes an extended regular expression; the path is literal text, and "(2)" or
    # "[beta]" in a name would otherwise match something else or make pgrep fail.
    r = subprocess.run(["pgrep", "-fl", re.escape(str(path))], capture_output=True, text=True, check=False)
    if r.returncode not in (0, 1):  # 1: no process matched
        raise Usage(f"pgrep failed ({r.returncode}): {r.stderr.strip()}")
    me = {str(os.getpid()), str(os.getppid())}
    return [line for line in r.stdout.splitlines() if line.split(" ", 1)[0] not in me]


def macos_version() -> str:
    r = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True, check=False)
    return r.stdout.strip()


def read_report(report: Path) -> dict[str, Any]:
    if not report.exists():
        raise Usage(f"no report at {report}; run `copy` first")
    return json.loads(report.read_text())


def write_report(report: Path, data: dict[str, Any]) -> None:
    tmp = report.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(report)


def known_differences(v: verify.VerifyResult) -> str:
    return (
        f"known differences: {v.symlink_mtimes} symlink mtimes, "
        f"{v.macos_xattr_values} macOS-managed attribute values or additions; "
        f"hard links: {v.hard_link_groups} groups kept, {v.external_hard_links} files linked "
        "from outside the tree (copied as separate files)"
    )


def cmd_copy(args: argparse.Namespace) -> int:
    src, copy, orig, report = paths(args.src)
    if not src.is_dir() or src.is_symlink():
        raise Usage(f"{src} is not a directory")
    # A finished report only records an earlier run; an app may have updated itself since.
    finished = report.exists() and read_report(report).get("state") == "finished"
    for p in (copy, orig) if finished else (copy, orig, report):
        if p.exists():
            raise Usage(f"{p} already exists (`discard`, `rollback` or `finish` first)")
    fs = filesystem(src)
    if fs != "apfs":
        raise Usage(f"{src} is on {fs}, not APFS")
    users = users_of(src)
    if users and not args.force:
        raise Usage("processes use the tree (close them, or pass --force):\n  " + "\n  ".join(users))
    before = verify.tree_stats(str(src))
    if free_bytes(src.parent) < before.allocated * 1.05 + (2 << 30):
        raise Usage(f"not enough free space for a full copy ({before.allocated / 2**30:.1f} GiB)")

    log(f"ditto --noclone --hfsCompression {src} {copy}")
    free_before, t0 = free_bytes(src.parent), time.time()
    subprocess.run(["ditto", "--noclone", "--hfsCompression", str(src), str(copy)], check=True)
    elapsed, free_after = time.time() - t0, free_bytes(src.parent)
    appledouble = verify.restore_appledouble(str(src), str(copy))
    restored = verify.restore_empty_xattrs(str(src), str(copy))
    quarantine = verify.restore_quarantine(str(src), str(copy))
    log(
        f"restored {appledouble} AppleDouble files and {restored} empty extended attributes "
        f"that ditto leaves out, and {quarantine} quarantine values it rewrites"
    )
    log(
        f"copied in {elapsed:.1f} s; the copy took {(free_before - free_after) / 2**30:.2f} GiB of free space"
    )

    phases = {"ditto_s": round(elapsed, 1)}
    t = time.time()
    v = verify.compare(str(src), str(copy), log=log, threads=args.threads)
    phases["verify_s"] = round(time.time() - t, 1)
    log(f"verify took {phases['verify_s']} s")
    t = time.time()
    c = codesign.compare(str(src), str(copy), log=log, threads=args.threads)
    phases["codesign_s"] = round(time.time() - t, 1)
    log(f"codesign took {phases['codesign_s']} s")
    after = verify.tree_stats(str(copy))
    passed = v.passed and c.passed
    data = {
        "result": "PASS" if passed else "FAIL",
        "state": "copied",
        "original": str(src),
        "copy": str(copy),
        "macos": macos_version(),
        "command": "ditto --noclone --hfsCompression",
        "elapsed_s": round(elapsed, 1),
        "phases": phases,
        "empty_xattrs_restored": restored,
        "appledouble_files_restored": appledouble,
        "quarantine_values_restored": quarantine,
        "threads": args.threads,
        "free_space_used_by_copy": free_before - free_after,
        "original_stats": asdict(before),
        "copy_stats": asdict(after),
        "verify": {
            "entries": v.entries,
            "files": v.files,
            "original_fingerprint": v.original_fingerprint,
            "copy_fingerprint": v.copy_fingerprint,
            "mismatches": v.mismatches,
            "symlink_mtimes_not_kept": v.symlink_mtimes,
            "macos_xattr_values_not_kept": v.macos_xattr_values,
            "hard_link_groups": v.hard_link_groups,
            "external_hard_links": v.external_hard_links,
        },
        "codesign": asdict(c),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_report(report, data)
    ratio = after.allocated / before.allocated if before.allocated else 1
    log(f"types in the copy: {after.types}")
    log(known_differences(v))
    log(
        f"allocated {before.allocated / 2**30:.2f} GiB -> {after.allocated / 2**30:.2f} GiB ({ratio:.3f}), "
        f"saves {(before.allocated - after.allocated) / 2**30:.2f} GiB"
    )
    log(f"{data['result']}: report {report}")
    if not passed:
        log(f"the original is untouched; inspect {copy}, then `apfs-compress discard {src}`")
    return 0 if passed else 1


def cmd_swap(args: argparse.Namespace) -> int:
    src, copy, orig, report = paths(args.src)
    data = read_report(report)
    if data.get("result") != "PASS" or data.get("state") != "copied":
        raise Usage(f"report is {data.get('result')}/{data.get('state')}, not PASS/copied")
    if not copy.is_dir() or orig.exists():
        raise Usage(f"need {copy} and no {orig} (`rollback` puts a moved-aside original back)")
    # Both trees must still be what the report describes: the copy since it was verified, and
    # the original since it was copied (an app may have updated itself in the meantime).
    fingerprints = data["verify"].get("original_fingerprint"), data["verify"].get("copy_fingerprint")
    if None in fingerprints:
        raise Usage("the report is from an older version; `discard` the copy and run `copy` again")
    if verify.fingerprint(verify.walk(str(copy))) != fingerprints[1]:
        raise Usage("the copy changed since it was verified; `discard` it and run `copy` again")
    if verify.fingerprint(verify.walk(str(src))) != fingerprints[0]:
        raise Usage("the original changed since it was copied; `discard` the copy and run `copy` again")
    users = users_of(src)
    if users and not args.force:
        raise Usage("processes use the tree (close them, or pass --force):\n  " + "\n  ".join(users))
    src.rename(orig)
    copy.rename(src)
    data["state"] = "swapped"
    write_report(report, data)
    log(f"{src} is now the compressed copy; the original is at {orig}")
    log(f"test it, then `apfs-compress finish {src} --yes`, or `apfs-compress rollback {src}`")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """Put the original back. `swap` and `rollback` are two renames each, and the report is
    written after them, so a kill can leave SRC missing (the original at orig, the copy at copy)
    or the report a step behind the directories; both are finished here."""
    src, copy, orig, report = paths(args.src)
    data = read_report(report) if report.exists() else None
    if orig.is_dir():
        if os.path.lexists(src):
            if os.path.lexists(copy):
                raise Usage(f"need no {copy}")
            src.rename(copy)
        orig.rename(src)
    elif not (data and data.get("state") == "swapped" and copy.is_dir() and src.is_dir()):
        raise Usage(f"no {orig}; nothing to roll back")
    if data:
        data["state"] = "copied"
        write_report(report, data)
    log(f"{src} is the original again; the compressed copy is at {copy}")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    src, copy, orig, report = paths(args.src)
    data = read_report(report)
    if data.get("state") != "swapped":
        raise Usage(f"report state is {data.get('state')}; nothing to finish")
    if orig.is_dir():
        if not args.yes:
            raise Usage(f"this deletes {orig}; pass --yes")
        shutil.rmtree(orig)
        log(f"deleted {orig}")
    elif copy.exists():
        raise Usage(f"{copy} exists and {orig} does not: a `rollback` was interrupted; run it again")
    else:
        log(f"{orig} is already gone")  # a kill between the delete and the report
    data["state"] = "finished"
    write_report(report, data)
    log(f"{src} stays compressed; report {report}")
    return 0


def cmd_discard(args: argparse.Namespace) -> int:
    """Delete a copy that was not swapped in, and the report that describes it. A report left by
    `finish` stays: it records that run, and a later `copy` that failed may have left the copy."""
    src, copy, orig, report = paths(args.src)
    data = read_report(report) if report.exists() else {}
    if data.get("state") == "swapped" or orig.exists():
        raise Usage(f"the copy was swapped in (the original is at {orig}); use `rollback` first")
    if copy.is_dir():
        shutil.rmtree(copy)
        log(f"deleted the copy {copy}; {src} is unchanged")
    elif data.get("state") != "copied":
        raise Usage(f"no copy at {copy}")
    else:
        log(f"no copy at {copy}; removing its report")
    if data.get("state") == "copied":
        report.unlink()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    st = verify.tree_stats(args.path)
    ratio = st.allocated / st.logical if st.logical else 1
    log(
        f"{st.files} files, logical {st.logical / 2**30:.2f} GiB, allocated {st.allocated / 2**30:.2f} GiB "
        f"({ratio:.3f})"
    )
    log(f"types: {st.types}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    v = verify.compare(args.original, args.copy, threads=args.threads)
    log(known_differences(v))
    log(f"{'PASS' if v.passed else 'FAIL'} ({len(v.mismatches)} mismatches)")
    return 0 if v.passed else 1


def cmd_codesign(args: argparse.Namespace) -> int:
    c = codesign.compare(args.original, args.copy, threads=args.threads)
    log(f"{'PASS' if c.passed else 'FAIL'} ({len(c.different)} differences)")
    return 0 if c.passed else 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apfs-compress", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in (
        ("copy", cmd_copy, "make and verify a compressed copy next to SRC"),
        ("swap", cmd_swap, "put the verified copy in SRC's place; the original moves aside"),
        ("rollback", cmd_rollback, "undo `swap`"),
        ("finish", cmd_finish, "delete the original that `swap` moved aside"),
        ("discard", cmd_discard, "delete a copy that was not swapped in"),
    ):
        s = sub.add_parser(name, help=help_)
        s.add_argument("src")
        s.add_argument("--threads", type=int, default=verify.DEFAULT_THREADS)
        s.add_argument("--force", action="store_true", help="go on although processes name the tree")
        s.add_argument("--yes", action="store_true", help="confirm `finish`")
        s.set_defaults(fn=fn)
    s = sub.add_parser("status", help="files, logical and allocated size and compression types of a tree")
    s.add_argument("path")
    s.set_defaults(fn=cmd_status)
    for name, fn, help_ in (
        ("verify", cmd_verify, "compare content and metadata of two trees"),
        ("codesign", cmd_codesign, "compare code signatures of two trees"),
    ):
        s = sub.add_parser(name, help=help_)
        s.add_argument("original")
        s.add_argument("copy")
        s.add_argument("--threads", type=int, default=verify.DEFAULT_THREADS)
        s.set_defaults(fn=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except Usage as e:
        print(f"apfs-compress: {e}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f"apfs-compress: {e}", file=sys.stderr)
        return 3
    except OSError as e:
        print(f"apfs-compress: {e}", file=sys.stderr)
        return 4


__version__ = "0.1.0"  # x-release-please-version
