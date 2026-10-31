// Single-thread ratio and speed of libcompression algorithms on 1 MiB blocks of one file.
// Build: clang -O2 algobench.c -lcompression -o algobench ; run: ./algobench FILE
#include <compression.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    fseek(f, 0, SEEK_END);
    size_t n = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *src = malloc(n);
    if (fread(src, 1, n, f) != n) return 1;
    fclose(f);
    const size_t B = 1 << 20;
    unsigned char *dst = malloc(2 * B + 4096), *back = malloc(B);
    struct { const char *name; compression_algorithm a; } algos[] = {
        {"LZ4", COMPRESSION_LZ4_RAW}, {"LZBITMAP", COMPRESSION_LZBITMAP}, {"LZFSE", COMPRESSION_LZFSE},
        {"LZMESH", COMPRESSION_LZMESH}, {"ZLIB", COMPRESSION_ZLIB}, {"BROTLI", COMPRESSION_BROTLI},
        {"LZRAVEN", COMPRESSION_LZRAVEN}, {"LZMA", COMPRESSION_LZMA},
    };
    printf("sample %.2f GiB, 1 MiB blocks, one thread\n", n / 1073741824.0);
    for (size_t i = 0; i < sizeof algos / sizeof algos[0]; i++) {
        size_t out = 0;
        double enc = 0, dec = 0;
        int bad = 0;
        for (size_t off = 0; off < n; off += B) {
            size_t len = n - off < B ? n - off : B;
            double t0 = now();
            size_t c = compression_encode_buffer(dst, 2 * B, src + off, len, NULL, algos[i].a);
            double t1 = now();
            if (c == 0 || c >= len) { out += len; enc += t1 - t0; continue; }  // stored raw
            size_t d = compression_decode_buffer(back, B, dst, c, NULL, algos[i].a);
            double t2 = now();
            if (d != len || memcmp(back, src + off, len)) bad = 1;
            out += c; enc += t1 - t0; dec += t2 - t1;
        }
        printf("%-9s ratio %.3f  encode %7.0f MB/s  decode %7.0f MB/s%s\n", algos[i].name, (double)out / n,
               n / 1e6 / enc, n / 1e6 / dec, bad ? "  ROUNDTRIP MISMATCH" : "");
        fflush(stdout);
    }
    return 0;
}
