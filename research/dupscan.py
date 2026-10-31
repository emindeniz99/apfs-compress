"""Read-only: how many bytes under the Unity editor folders are byte-identical copies."""
import collections, hashlib, os, sys

ROOTS = sys.argv[1:]
MIN = 16 * 1024
by_size = collections.defaultdict(list)
total = 0
for root in ROOTS:
    for dp, dns, fns in os.walk(root, followlinks=False):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not os.path.isfile(p) or os.path.islink(p):
                continue
            total += st.st_size
            if st.st_size >= MIN:
                by_size[st.st_size].append(p)


def digest(p):
    h = hashlib.blake2b(digest_size=20)
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


dup_bytes = 0
pairs = collections.Counter()  # (editor of copy, editor of original) -> bytes
kinds = collections.Counter()
groups = 0
for size, paths in by_size.items():
    if len(paths) < 2:
        continue
    by_hash = collections.defaultdict(list)
    for p in paths:
        try:
            by_hash[digest(p)].append(p)
        except OSError:
            pass
    for same in by_hash.values():
        if len(same) < 2:
            continue
        groups += 1
        dup_bytes += size * (len(same) - 1)
        ed = lambda p: next((r for r in ROOTS if p.startswith(r + "/")), "?").rsplit("/", 1)[-1]
        first = ed(same[0])
        for p in same[1:]:
            pairs[(ed(p), first)] += size
            rel = p.split("/", 7)[-1]
            kinds["/".join(rel.split("/")[:3])] += size

G = 1 << 30
print(f"scanned {total / G:.1f} GiB; identical copies {dup_bytes / G:.2f} GiB in {groups} groups")
print("by editor pair (copy <- first):")
for (a, b), n in pairs.most_common(15):
    print(f"  {a} <- {b}: {n / G:.2f} GiB")
print("by area:")
for k, n in kinds.most_common(15):
    print(f"  {k}: {n / G:.2f} GiB")
