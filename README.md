# apfs-compress

## What it does

It shrinks read-mostly trees on APFS, such as Unity editors and Android SDKs, with APFS transparent compression. Files stay compressed on disk, and the kernel decompresses them into memory when they are read, so programs see no difference.

It never compresses in place. Apple's own `ditto --noclone --hfsCompression` makes a compressed copy next to the original. The tool then checks every file of the copy against the original: content, metadata and code signatures. It changes the original only through separate steps you run yourself: `swap`, `rollback` and `finish`.

## How to run

macOS on APFS, with Python 3.9 or later. The tool uses the standard library only. Development uses uv and Python from `mise.toml` and `.python-version`.

```bash
uv tool install ./projects/apfs-compress     # puts apfs-compress in ~/.local/bin

apfs-compress copy   /Volumes/T7Data/Unity/Editors/6000.3.27f2   # copy, verify, report; original untouched
# test the copy: it is at .../Editors/.apfscmp-6000.3.27f2 (run the app from there)
apfs-compress swap   /Volumes/T7Data/Unity/Editors/6000.3.27f2   # only after a PASS report
apfs-compress rollback /Volumes/T7Data/Unity/Editors/6000.3.27f2 # undo swap
apfs-compress finish /Volumes/T7Data/Unity/Editors/6000.3.27f2 --yes  # delete the moved-aside original
apfs-compress discard /Volumes/T7Data/Unity/Editors/6000.3.27f2  # delete a copy that was not swapped in

apfs-compress status  PATH            # files, logical vs allocated size, compression types
apfs-compress verify  ORIGINAL COPY   # content and metadata comparison alone
apfs-compress codesign ORIGINAL COPY  # code-signature comparison alone
```

Close every program that uses the tree first. `copy` and `swap` refuse when a process's command line names the tree; `--force` overrides that check.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | done |
| 1 | the verification found a difference; the original is untouched |
| 2 | a precondition failed; nothing was changed |
| 3 | `ditto` failed |

### Files beside SRC

| Path | What it is |
|---|---|
| `.apfscmp-<name>` | the compressed copy, before `swap` |
| `.apfscmp-orig-<name>` | the original, after `swap` |
| `.apfscmp-<name>.json` | the report: result, state, sizes, compression types, mismatches |

The copy sits beside the original at the same depth, so relative symlinks that point out of the tree keep resolving while you test the copy. Our Unity editors' `AndroidPlayer/SDK -> ../../../../Shared/...` is such a link.

### Development

```bash
uv sync --locked
uv run ruff format --check && uv run ruff check && uv run ty check --error-on-warning
uv run python -m unittest discover -s tests -v
```

## What `copy` checks

- **Same tree:** the same relative paths and entry types.
- **Same metadata:**
  - mode, owner, group and BSD flags, except the `compressed` flag;
  - mtime of files and directories;
  - symlink targets;
  - extended attributes, by name and value;
  - extended ACLs.
- **Same content:** the size and SHA-256 of every regular file.
- **Same hard links:** names that share an inode in the original share one in the copy (`ditto` keeps them). A file whose other links lie outside the tree becomes a separate file in the copy; the report counts those. The NDK in `Shared/Android-6000` has one: `libc++_shared.so`, with 3 links and only 1 of them inside the tree.
- **Same code signatures:**
  - `codesign --verify --deep --strict` on every top-level `.app`, or on the tree itself when it is one;
  - `codesign --verify --strict` on every Mach-O file, and on every other file signed through extended attributes (`com.apple.cs.CodeDirectory`), such as an ad-hoc signed static library.
  
  A file must verify in both trees or in neither. Unsigned build tools are unsigned in both.
- **Recorded, not checked:**
  - which decmpfs type each file got;
  - logical and allocated size;
  - the free space the copy consumed.

Before verifying, `copy` puts back what `ditto` leaves out:

- **AppleDouble files (`._name`):** `ditto` treats them as metadata of `name` and does not copy them, with or without a `name` beside them. Tarballs packed on macOS and unpacked elsewhere leave them behind; the Android NDK r30 and emulator in `android-sdk` have five. `copy` copies them with `cp -p`, byte for byte.
- **Zero-length extended attributes:** `copy` writes back the ones that `ditto` leaves out, with or without compression (`cp -p` keeps them). Unity 2022.3.62f3 has six iOS simulator static libraries whose `com.apple.cs.CodeSignature` is empty. They verify either way, but the metadata would differ.

Known differences are reported but not failed:

- **Symlink mtimes:** `ditto` does not keep a symlink's own mtime. It does keep the symlink's target, which is checked.
- **`com.apple.provenance` values:** macOS 13 and later stamp this 11-byte attribute on every file a process creates. Its last 8 bytes are an ID of that process's app in the ExecPolicy database. The kernel writes it, so no copy can keep the original value. A tree installed by Unity Hub and copied by this tool differs in every file, for example `01 02 00 45 30 A1 03 3F 84 B5 A9` against `01 02 00 BB 71 2F AA 4D 27 09 9C`. Only the attribute's presence is compared ([Eclectic Light](https://eclecticlight.co/2023/03/16/what-is-macos-ventura-doing-tracking-provenance/) on what it is).
- **`com.apple.macl` values:** TCC and the sandbox keep in it the apps that were granted access to a file, and a copy gains an entry for the copying process. On 2021.3.45f2 only `Unity.app` itself had it: all zeros in the original, one entry in the copy. Only its presence is compared.

Hashing and code-signature checks use one thread per CPU core by default (`--threads` to limit). The report records each phase's time.

## Notes / learnings (measured 2026-10-31, macOS 27.2, MacBook Pro M-series, Samsung T7 Shield over USB 10 Gb/s)

### First real tree: Unity 6000.3.27f2 (Android and iOS modules)

| | |
|---|---|
| Copy time | 87 s for 59,840 entries |
| Allocated size | 17.11 GiB, compressed to 8.71 GiB (0.509): **8.40 GiB saved**. The copy took 8.74 GiB of free space |
| Types chosen by `ditto` | 15,763 files LZVN (type 8 in the resource fork, 192 small files type 7 in the attribute); 38,565 files left alone (already compressed or too small to gain) |
| Content, metadata, ACLs | all equal; 22 symlinks with a new mtime |
| Signatures | Unity.app and the Bug Reporter valid in both trees; 740 Mach-O files: 264 valid in both, 476 unsigned in both, 0 different |
| Function | from the copy: the xLua editor suite 48/48, an Android IL2CPP APK, an iOS export |
| Start time | batch open and quit of one project: 7.5 and 5.0 s original, 4.5 and 4.3 s compressed |

### Rollout on the T7 (2026-10-31)

Each tree went through `copy`, `swap` and a functional check, then `finish` after the owner approved it. Free space on the T7 was 83 GiB with the originals kept beside the copies, and 182 GiB after `finish`.

| Tree | Allocated before → after | Saved | Functional check |
|---|---|---|---|
| Unity 6000.3.27f2 | 17.11 → 8.71 GiB | 8.40 GiB | `-createProject` in batch mode |
| Unity 6000.6.7f1 | 24.34 → 11.63 GiB | 12.72 GiB | same |
| Unity 2021.3.45f2 | 16.03 → 8.57 GiB | 7.47 GiB | same |
| Unity 2022.3.62f3 | 18.13 → 9.68 GiB | 8.45 GiB | same |
| Unity 6000.0.85f1 | 8.74 → 5.61 GiB | 3.13 GiB | same |
| Unity `Shared/Android-6000` (SDK, NDK, OpenJDK) | 3.64 → 1.74 GiB | 1.90 GiB | `java -version`, NDK clang builds an arm64 binary, `aapt2 version` |
| Android NDK r30 | 2.80 → 1.08 GiB | 1.73 GiB | NDK clang builds an arm64 binary |
| Android emulator | 1.22 → 0.44 GiB | 0.78 GiB | `emulator -version` |
| OpenHarmony SDK 7.0 | 6.82 → 5.22 GiB | 1.60 GiB | none: the checks above only (Linux binaries) |
| **Total** | | **46.2 GiB** | |

- **Android system images** (4.25 GiB) would have saved 0.08 GiB: the disk images inside are already compressed. That copy was rolled back and discarded.
- **Left alone:** `platform-tools` (the shared adb server runs from it) and the AVD home (written while an emulator runs).
- **Time per tree,** for 2022.3.62f3 with 129,000 files: `ditto` 199 s, verification 75 s, signatures 129 s.
- **Restores that were needed:** 6 empty `com.apple.cs.CodeSignature` attributes in 2022.3.62f3, and 5 AppleDouble files in the NDK and the emulator. Each failed verification first, which is how they were found.

Unity 2021.3.45f2, installed by Unity Hub, first failed the check on 146,007 `com.apple.provenance` values and on nothing else. A copy run from its compressed tree created a project and quit cleanly, which led to the rule above. Its `Unity.app` also fails `codesign --deep` in the original ("invalid destination for symbolic link in bundle"), and fails the same way in the copy.

Gatekeeper (`spctl`) rejects both the original and the copy of 6000.3.27f2 as "Unnotarized Developer ID". The cause is Unity's notarization of that release, not the compression; 6000.6.7f1 is accepted. The editor runs, because the files carry no quarantine attribute.

### Why `ditto` and LZVN

- **APFS compression types:**
  - ZLIB (types 3 and 4) since Mac OS X 10.6;
  - LZVN (7 and 8);
  - LZFSE (11 and 12) since OS X 10.11;
  - LZBITMAP (13 and 14) since macOS 12.
  
  The tools here can write only ZLIB, LZVN and LZFSE. LZMESH and LZRAVEN, both new in macOS 27, exist in libcompression and Apple Archive, not in APFS.
- **`ditto` chooses the algorithm itself;** it has no flag for one. On macOS 27.2 it chose LZVN for every file. `DITTO_TEST_OPTIONS=1 ditto ...` prints its copier settings and exits: ACLs, extended attributes, quarantine, resource forks and restricted flags are all kept, and `useHFSPlusCompression` is on. There is no algorithm key.
- **Same 4.8 GiB copy on the T7** (`research/`, 39,000 mostly small files):

  | Type | Size on disk | Read speed |
  |---|---|---|
  | none | 100% | 272-295 MiB/s |
  | LZVN | 76.6% | 296-299 MiB/s |
  | LZFSE | 74.9% | 269-274 MiB/s |
  | ZLIB | 80.4% | 231-290 MiB/s |

  LZVN costs no read speed. The T7's USB link, not the decoder, is the limit.
- **Other tools:**
  - **applesauce** (current, writes atomically) and **afsctool** (writes in place) write the same three types.
  - **fscmp** can write LZBITMAP through the private AppleFSCompression framework, but it is one 2022 commit against an unstable private interface.
  
  All of them modify files in place. `ditto` copies, so the original stays recoverable until `finish`.
- **What applesauce's source does differently** (`crates/applesauce/src/threads/writer.rs`, read 2026-10-31):
  - It copies extended attributes with `fcopyfile(COPYFILE_XATTR)`. That keeps zero-length attributes, as `cp -p` does; `ditto` drops them, hence the restore step here.
  - It copies mode and ACLs with `COPYFILE_SECURITY`, and sets `UF_COMPRESSED` itself.
  - It skips hard-linked files (`SkipReason::HardLink`), because compressing in place would detach them.
  - Its verification is optional (`--verify`) and compares content only.

### Algorithms on macOS 27.2

`research/algobench.c`: 1 GiB sampled from a whole Unity editor, 1 MiB blocks, one core. APFS uses 64 KiB blocks and many small files, so real APFS ratios are worse, but the order holds.

| Algorithm | Since | Ratio | Encode MB/s | Decode MB/s |
|---|---|---|---|---|
| LZ4 | 10.11 | 0.532 | 871 | 4,934 |
| LZBITMAP | 12 | 0.441 | 311 | 4,896 |
| LZFSE | 10.11 | 0.421 | 163 | 1,599 |
| LZMESH | 27 | 0.391 | 333 | 2,188 |
| ZLIB | 10.11 | 0.426 | 63 | 567 |
| Brotli | 12 | 0.417 | 251 | 460 |
| LZRAVEN | 27 | 0.352 | 10 | 325 |
| LZMA | 10.11 | 0.358 | 8 | 98 |
| zstd -3 / -9 / -19 (Homebrew 1.5.7) | | 0.414 / 0.400 / 0.375 | 491 / 120 / 8 | 1,658 / 1,772 / 1,302 |

- **LZMESH** beats LZFSE on all three counts and zstd -3 on ratio and decode speed.
- **LZRAVEN** beats LZMA's ratio and decodes 3.3 times faster.

Re-run this when APFS gains a new type.

### Other findings

- **Duplicates:** the five Unity editors held 14.3 GiB of byte-identical copies out of 86.6 GiB, mostly built-in packages and NDK toolchains (`research/dupscan.py`). APFS clones could share them, but this tool does not do that.
- **Disk speed,** with the page cache bypassed (`research/diskspeed.py`):

  | | Large-file read | Large-file write | Unity's small files |
  |---|---|---|---|
  | Internal SSD | ~2,550 MB/s | ~2,200 MB/s | ~1,000 MB/s |
  | T7 Shield | ~800 MB/s | ~820 MB/s | ~380 MB/s |

- **Filesystems:** APFS (case-insensitive for Unity and Xcode) remains the right choice.
  - HFS+ has the same compression, but no clones or snapshots.
  - exFAT and FAT have no compression.
  - OpenZFS needs a kernel extension and has no Spotlight or Time Machine support.
- **Rejected:** a compressed DMG (UDIF ULFO/ULMO) compresses across files, but it has to be mounted, and it is read-only, so every update rebuilds it.

## Not for

- Trees that change often: git checkouts, build caches such as Unity `Library` or Gradle, VM disks and databases. A write stores the file uncompressed again.
- Already-compressed data: APKs, zips and media.

The repo's [disk hygiene rule](../../.claude/rules/disk-hygiene.md) covers deleting regenerable caches. This tool is for what you keep.
