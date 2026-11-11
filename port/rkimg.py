"""Pack and unpack the boot images the stock Skylight Frame 2 U-Boot accepts.

Boot image: Android v0 header ("ANDROID!") with kernel, ramdisk and "second".
The 20-byte id is Rockchip's mkbootimg SHA-1 (see RECON.md, "Boot image hash").
"second" is a Rockchip RSCE resource container holding rk-kernel.dtb and the logos.

Usage:
  rkimg.py selftest <stock.img>
  rkimg.py mkboot --template <stock.img> --kernel zImage [--ramdisk rd.cpio.gz] [--dtb board.dtb] --out new.img
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

BLK = 512
ID_OFF, ID_LEN = 0x240, 20


def _pad(b: bytes, n: int) -> bytes:
    return b + b"\0" * (-len(b) % n)


def parse_boot(d: bytes) -> dict:
    assert d[:8] == b"ANDROID!", "not an Android boot image"
    ksz, kaddr, rsz, raddr, ssz, saddr, tags, page = struct.unpack_from("<8I", d, 8)
    pg = lambda n: (n + page - 1) // page * page  # noqa: E731
    o = page
    kernel = d[o:o + ksz]; o += pg(ksz)
    ramdisk = d[o:o + rsz]; o += pg(rsz)
    second = d[o:o + ssz]; o += pg(ssz)
    return dict(header=d[:page], kernel=kernel, ramdisk=ramdisk, second=second, page=page, end=o)


def rk_id(header: bytes, kernel: bytes, ramdisk: bytes, second: bytes) -> bytes:
    size = lambda b: struct.pack("<I", len(b))  # noqa: E731
    return hashlib.sha1(kernel + size(kernel) + ramdisk + size(ramdisk) + second + size(second)
                        + header[32:ID_OFF]).digest()


def pack_boot(template_header: bytes, kernel: bytes, ramdisk: bytes, second: bytes, total: int) -> bytes:
    """Reuse the template's load addresses, page size, name and cmdline; recompute sizes and id."""
    page = struct.unpack_from("<I", template_header, 36)[0]
    h = bytearray(template_header[:page])
    struct.pack_into("<I", h, 8, len(kernel))
    struct.pack_into("<I", h, 16, len(ramdisk))
    struct.pack_into("<I", h, 24, len(second))
    h[ID_OFF:ID_OFF + 32] = rk_id(bytes(h), kernel, ramdisk, second) + b"\0" * 12
    img = bytes(h) + _pad(kernel, page) + _pad(ramdisk, page) + _pad(second, page)
    if len(img) > total:
        raise ValueError(f"image is {len(img)} bytes, partition holds {total}")
    return img + b"\0" * (total - len(img))


def unpack_rsce(d: bytes) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    """Return (header block, [(raw 512-byte entry block, content)]) in table order."""
    assert d[:4] == b"RSCE"
    hdr_blocks, tbl_off, entry_blocks = d[8], d[9], d[10]
    count = struct.unpack_from("<I", d, 12)[0]
    assert (hdr_blocks, tbl_off, entry_blocks) == (1, 1, 1), "unexpected RSCE layout"
    out = []
    for i in range(count):
        e = d[(tbl_off + i) * BLK:(tbl_off + i + 1) * BLK]
        assert e[:4] == b"ENTR"
        off, size = struct.unpack_from("<II", e, 260)
        out.append((e, d[off * BLK:off * BLK + size]))
    return d[:BLK], out


def entry_name(e: bytes) -> str:
    return e[4:260].split(b"\0")[0].decode()


def pack_rsce(header: bytes, entries: list[tuple[bytes, bytes]]) -> bytes:
    """Lay contents out after the table in table order, each on a block boundary."""
    h = bytearray(header)
    struct.pack_into("<I", h, 12, len(entries))
    table, blob = b"", b""
    off = 1 + len(entries)
    for e, content in entries:
        e = bytearray(e)
        struct.pack_into("<II", e, 260, off, len(content))
        table += bytes(e)
        padded = _pad(content, BLK)
        blob += padded
        off += len(padded) // BLK
    return bytes(h) + table + blob


def replace_dtb(second: bytes, dtb: bytes) -> bytes:
    header, entries = unpack_rsce(second)
    names = [entry_name(e) for e, _ in entries]
    assert names.count("rk-kernel.dtb") == 1, names
    return pack_rsce(header, [(e, dtb if entry_name(e) == "rk-kernel.dtb" else c) for e, c in entries])


def selftest(stock_path: Path) -> None:
    d = stock_path.read_bytes()
    p = parse_boot(d)
    assert rk_id(p["header"], p["kernel"], p["ramdisk"], p["second"]) == d[ID_OFF:ID_OFF + ID_LEN], "id mismatch"
    header, entries = unpack_rsce(p["second"])
    repacked = pack_rsce(header, entries)
    same_rsce = repacked == p["second"]
    rebuilt = pack_boot(p["header"], p["kernel"], p["ramdisk"], p["second"], len(d))
    print("entries:", [entry_name(e) for e, _ in entries])
    print("RSCE repack identical:", same_rsce, f"({len(repacked)} vs {len(p['second'])} bytes)")
    print("boot image repack identical:", rebuilt == d)
    if not (same_rsce and rebuilt == d):
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("selftest"); st.add_argument("stock", type=Path)
    mk = sub.add_parser("mkboot")
    mk.add_argument("--template", type=Path, required=True)
    mk.add_argument("--kernel", type=Path, required=True)
    mk.add_argument("--ramdisk", type=Path)
    mk.add_argument("--dtb", type=Path, help="replace rk-kernel.dtb in 'second'; omit to keep the stock 'second'")
    mk.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest(a.stock)
    t = a.template.read_bytes()
    p = parse_boot(t)
    second = replace_dtb(p["second"], a.dtb.read_bytes()) if a.dtb else p["second"]
    # Every image the loader has accepted had kernel and ramdisk lengths that are multiples of 4;
    # the one image with odd lengths was refused (RECON.md, "Boot image hash"). Trailing zeros are
    # harmless after an appended DTB and after a gzip'd cpio.
    kernel = _pad(a.kernel.read_bytes(), 4)
    ramdisk = _pad(a.ramdisk.read_bytes(), 4) if a.ramdisk else b""
    img = pack_boot(p["header"], kernel, ramdisk, second, len(t))
    q = parse_boot(img)
    assert rk_id(q["header"], q["kernel"], q["ramdisk"], q["second"]) == img[ID_OFF:ID_OFF + ID_LEN]
    a.out.write_bytes(img)
    print(f"{a.out}: kernel {len(q['kernel'])} ramdisk {len(q['ramdisk'])} second {len(q['second'])} "
          f"used {q['end']}/{len(img)} md5 {hashlib.md5(img).hexdigest()}")


if __name__ == "__main__":
    main()
