"""Measure Argon2id cost on this host to bound offline passphrase guessing.

Synthetic input only; nothing is stored. Parameters mirror the design default
(m=64 MiB, t=3, p=2, 32-byte output).
"""
import secrets
import statistics
import time

from argon2 import low_level

PARAMS = dict(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, type=low_level.Type.ID)
password = b"synthetic-benchmark-passphrase-not-a-real-secret"
salt = secrets.token_bytes(16)

timings = []
for _ in range(5):
    started = time.perf_counter()
    low_level.hash_secret_raw(secret=password, salt=salt, **PARAMS)
    timings.append(time.perf_counter() - started)

mean = statistics.mean(timings)
best = min(timings)
rate = 1.0 / mean
year = 365.25 * 24 * 3600

print("argon2id  m=65536 KiB  t=3  p=2  len=32")
print("mean %.1f ms   best %.1f ms   => %.1f guesses/s per core" % (mean * 1000, best * 1000, rate))
print()

spaces = [
    ("8-char human word (1e6 list)", 1e6),
    ("8-char human word (1e7 list)", 1e7),
    ("8-char random, 62 alphabet", 62 ** 8),
    ("12-char random, 62 alphabet", 62 ** 12),
    ("128-bit recovery code", 2.0 ** 128),
]

print("%-32s %14s %14s %14s" % ("search space", "1 core", "8 cores", "1000 cores"))
for label, space in spaces:
    row = [space / (rate * cores) / year for cores in (1, 8, 1000)]
    print("%-32s %11.3e y %11.3e y %11.3e y" % (label, *row))
