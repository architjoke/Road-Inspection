"""Download ONLY the India part of the official RDD2022 release.

The official release (CRDDC'2022, figshare DOI 10.6084/m9.figshare.21431547,
CC BY-SA 4.0) is a single 13.3 GB zip with six countries. This script reads the
zip's table of contents over HTTP range requests and downloads just the India
entries (~0.5 GB): images + Pascal VOC XML annotations.

Usage
-----
    python scripts/training/fetch_rdd2022_india.py --list            # show India entries only
    python scripts/training/fetch_rdd2022_india.py --out data/rdd2022_india

Annotations exist only for the official *train* split; test images are
unlabelled and skipped unless --include-test is passed.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

import requests

FIGSHARE_FILE = "https://ndownloader.figshare.com/files/38030910"  # RDD2022_released_through_CRDDC2022.zip
BLOCK = 1 << 20  # 1 MiB read-ahead


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP Range requests (fresh signed URL per request)."""

    def __init__(self, url: str):
        self.url = url
        self.session = requests.Session()
        self.pos = 0
        self.size = self._probe_size()
        self.cache_start = -1
        self.cache = b""
        self.bytes_fetched = 0

    def _get(self, start: int, end: int) -> bytes:
        for attempt in range(5):
            try:
                resp = self.session.get(
                    self.url, headers={"Range": f"bytes={start}-{end}"}, timeout=(15, 120)
                )
                if resp.status_code == 206:
                    self.bytes_fetched += len(resp.content)
                    return resp.content
                raise IOError(f"server returned {resp.status_code} for a range request")
            except (requests.RequestException, IOError) as exc:
                if attempt == 4:
                    raise
                print(f"  retry {attempt + 1} after: {exc}", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
        raise IOError("unreachable")

    def _probe_size(self) -> int:
        resp = self.session.get(self.url, headers={"Range": "bytes=0-0"}, timeout=(15, 60))
        if resp.status_code != 206:
            raise SystemExit(
                f"Server does not support partial downloads (HTTP {resp.status_code}); "
                "refusing to download the full 13.3 GB archive."
            )
        return int(resp.headers["Content-Range"].split("/")[-1])

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def readinto(self, buffer):
        n = len(buffer)
        if self.pos >= self.size or n == 0:
            return 0
        n = min(n, self.size - self.pos)
        inside = self.cache_start <= self.pos and self.pos + n <= self.cache_start + len(self.cache)
        if not inside:
            if n > BLOCK:  # large sequential read: fetch exactly
                data = self._get(self.pos, self.pos + n - 1)
                buffer[: len(data)] = data
                self.pos += len(data)
                return len(data)
            self.cache_start = self.pos
            self.cache = self._get(self.pos, min(self.size, self.pos + BLOCK) - 1)
        off = self.pos - self.cache_start
        chunk = self.cache[off: off + n]
        buffer[: len(chunk)] = chunk
        self.pos += len(chunk)
        return len(chunk)


class WindowFile(io.RawIOBase):
    """Read-only view of bytes [start, start+size) of another seekable file."""

    def __init__(self, base, start: int, size: int):
        self.base, self.start, self.size, self.pos = base, start, size, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def readinto(self, buffer):
        n = min(len(buffer), self.size - self.pos)
        if n <= 0:
            return 0
        self.base.seek(self.start + self.pos)
        got = self.base.readinto(memoryview(buffer)[:n])
        self.pos += got
        return got


def stored_member_window(outer: zipfile.ZipFile, raw, name: str) -> WindowFile:
    """Byte window of an uncompressed (stored) member inside the remote archive."""
    import struct

    info = outer.getinfo(name)
    if info.compress_type != zipfile.ZIP_STORED:
        raise SystemExit(f"{name} is compressed; cannot range-read it in place")
    raw.seek(info.header_offset)
    header = raw.read(30)
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    return WindowFile(raw, info.header_offset + 30 + name_len + extra_len, info.compress_size)


def is_india(name: str) -> bool:
    return any(
        PurePosixPath(part).stem.lower() in {"india", "rdd2022_india"}
        for part in PurePosixPath(name).parts
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("data/rdd2022_india"))
    ap.add_argument("--list", action="store_true", help="only list India entries")
    ap.add_argument("--include-test", action="store_true", help="also fetch unlabelled test images")
    args = ap.parse_args(argv)

    remote = HttpRangeFile(FIGSHARE_FILE)
    print(f"Remote archive: {remote.size / 1e9:.2f} GB (reading table of contents only)")
    outer = zipfile.ZipFile(io.BufferedReader(remote, buffer_size=BLOCK))
    names = outer.namelist()
    print(f"{len(names)} entries in archive; table of contents cost {remote.bytes_fetched / 1e6:.1f} MB")

    # The release nests one stored zip per country; read India's in place.
    nested = [n for n in names if n.lower().endswith(".zip") and is_india(n)]
    if nested:
        window = stored_member_window(outer, remote, nested[0])
        print(f"India archive: {nested[0]} ({window.size / 1e6:.1f} MB), reading in place")
        source = zipfile.ZipFile(io.BufferedReader(window, buffer_size=BLOCK))
        entries = source.namelist()
    else:
        source, entries = outer, [n for n in names if is_india(n)]

    wanted = []
    for name in entries:
        lower = name.lower()
        if name.endswith("/"):
            continue
        if "/test/" in lower and not args.include_test:
            continue
        if lower.endswith((".jpg", ".jpeg", ".png", ".xml")):
            wanted.append(name)
    wanted.sort(key=lambda n: source.getinfo(n).header_offset)  # sequential range reads
    total = sum(source.getinfo(n).compress_size for n in wanted)
    xmls = sum(1 for n in wanted if n.lower().endswith(".xml"))
    print(f"India entries to fetch: {len(wanted)} ({xmls} XML), ~{total / 1e6:.0f} MB")
    if args.list:
        for n in wanted[:10]:
            print("  ", n)
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    root = args.out.resolve()
    for i, name in enumerate(wanted, 1):
        rel = PurePosixPath(name)
        # keep the tail from the country folder down; never allow path escape
        parts = [p for p in rel.parts if p not in ("..", "")]
        target = (root / Path(*parts)).resolve()
        if root not in target.parents:
            print(f"  skipping unsafe path {name}")
            continue
        if target.exists() and target.stat().st_size == source.getinfo(name).file_size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open(name) as src, open(target, "wb") as dst:
            dst.write(src.read())
        if i % 500 == 0 or i == len(wanted):
            print(f"  {i}/{len(wanted)} files, {remote.bytes_fetched / 1e6:.0f} MB transferred", flush=True)
    print(f"Done -> {root} ({remote.bytes_fetched / 1e6:.0f} MB transferred in total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
