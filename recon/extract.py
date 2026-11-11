"""Unpack stock Skylight Frame 2 boot images and Rockchip resource files, decompile DTBs.

Usage: python -I extract.py <backup_dir> <out_dir>
Reads only; writes extracted pieces into <out_dir>.
"""
import struct
import sys
from pathlib import Path

import fdt


def unpack_bootimg(path: Path, out: Path) -> None:
    data = path.read_bytes()
    assert data[:8] == b"ANDROID!", f"{path} is not an Android boot image"
    (ksz, kaddr, rsz, raddr, ssz, saddr, tags, page) = struct.unpack_from("<8I", data, 8)
    name = data[0x30:0x40].split(b"\0")[0].decode(errors="replace")
    cmdline = data[0x40:0x240].split(b"\0")[0].decode(errors="replace")
    print(f"{path.name}: kernel={ksz}@{kaddr:#x} ramdisk={rsz}@{raddr:#x} "
          f"second={ssz}@{saddr:#x} tags={tags:#x} page={page} name={name!r} cmdline={cmdline!r}")

    def pages(n: int) -> int:
        return (n + page - 1) // page * page

    off = page
    pieces = {"kernel": ksz, "ramdisk": rsz, "second": ssz}
    for piece, size in pieces.items():
        if size:
            (out / f"{path.stem}.{piece}").write_bytes(data[off:off + size])
        off += pages(size)


def unpack_rsce(path: Path, out: Path) -> list[Path]:
    data = path.read_bytes()
    assert data[:4] == b"RSCE", f"{path} is not a Rockchip resource file"
    found = []
    pos = 0
    while (pos := data.find(b"ENTR", pos)) != -1:
        name = data[pos + 4:pos + 260].split(b"\0")[0].decode(errors="replace")
        blk_off, size = struct.unpack_from("<II", data, pos + 260)
        start = blk_off * 512
        if name and 0 < size <= len(data) - start:
            dest = out / f"{path.stem}__{name.replace('/', '_')}"
            dest.write_bytes(data[start:start + size])
            print(f"  {path.name}: entry {name!r} size={size} offset={start:#x}")
            found.append(dest)
        pos += 4
    return found


def decompile(dtb: Path) -> None:
    tree = fdt.parse_dtb(dtb.read_bytes())
    dts = dtb.with_suffix(dtb.suffix + ".dts")
    dts.write_text(tree.to_dts())
    print(f"  decompiled {dtb.name} -> {dts.name} ({len(dts.read_text().splitlines())} lines)")


def main() -> None:
    backup, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    parts = backup / "partitions"
    for img in ("boot", "recovery"):
        unpack_bootimg(parts / f"{img}.img", out)
    candidates = [parts / "resource.img", out / "boot.second", out / "recovery.second"]
    for c in candidates:
        if c.exists() and c.read_bytes()[:4] == b"RSCE":
            print(f"RSCE: {c.name}")
            for entry in unpack_rsce(c, out):
                if entry.read_bytes()[:4] == b"\xd0\x0d\xfe\xed":
                    decompile(entry)


if __name__ == "__main__":
    main()
