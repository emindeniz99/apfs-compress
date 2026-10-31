"""Sequential write and read of a 4 GiB random file with the page cache bypassed (F_NOCACHE)."""
import fcntl, os, sys, time

F_NOCACHE = 48
CHUNK = 8 << 20
SIZE = 4 << 30
block = os.urandom(CHUNK)

for d in sys.argv[1:]:
    p = os.path.join(d, "diskspeed.tmp")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    fcntl.fcntl(fd, F_NOCACHE, 1)
    t = time.time()
    for _ in range(SIZE // CHUNK):
        os.write(fd, block)
    os.fsync(fd)
    w = time.time() - t
    os.close(fd)
    fd = os.open(p, os.O_RDONLY)
    fcntl.fcntl(fd, F_NOCACHE, 1)
    t = time.time()
    while os.read(fd, CHUNK):
        pass
    r = time.time() - t
    os.close(fd)
    os.remove(p)
    print(f"{d}: write {SIZE / 2**20 / w:6.0f} MiB/s  read {SIZE / 2**20 / r:6.0f} MiB/s")
