"""
acreplay_merge.py
==============================
Merge two or more Assetto Corsa .acreplay files into one continuous replay.

Each source replay contributes one run played back-to-back with a single
frame-cut between runs (the car teleports to the next run's start position).

Usage
-----
    python acreplay_merge.py <out.acreplay> \\
        <replay1.acreplay> <car_index> \\
        <replay2.acreplay> <car_index> \\
        [<replay3.acreplay> <car_index> ...]

    car_index : 0-based index of which car to extract from that file.

Examples
--------
    # Two replays
    python acreplay_merge.py merged.acreplay bondar3.acreplay 0 khas3.acreplay 0

    # Three replays
    python acreplay_merge.py merged.acreplay r1.acreplay 0 r2.acreplay 0 r3.acreplay 0
"""

import math
import os
import struct
import sys

STRIDE = 292
MAX_STR = 256


# binary helpers


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _f32(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def _pu32(value: int) -> bytes:
    return struct.pack("<I", value)


def _pf32(value: float) -> bytes:
    return struct.pack("<f", value)


def _rstr(data: bytes, offset: int) -> tuple[str, int]:
    n = _u32(data, offset)
    if n > MAX_STR:
        raise ValueError(f"bad string len {n} @ 0x{offset:x}")
    return data[offset + 4 : offset + 4 + n].decode("utf-8", errors="replace"), offset + 4 + n


def _pstr(s: str) -> bytes:
    encoded = s.encode("utf-8")
    return _pu32(len(encoded)) + encoded


# file structure helpers


def _config_start(data: bytes) -> int:
    """Return byte offset of the CONFIG_RACE trailer, or EOF if absent."""
    p = data.find(b"[BENCHMARK]")
    return (p - 31) if p >= 0 else len(data)


def _next_car(data: bytes, from_off: int) -> int | None:
    """Return offset of the next car block's prelude field, or None."""
    o = from_off
    while o < len(data) - 8:
        n = _u32(data, o)
        if 5 <= n <= 60 and o + 4 + n <= len(data):
            if all(32 <= b < 127 for b in data[o + 4 : o + 4 + n]):
                return o - 4
        o += 4
    return None


def parse(path: str) -> dict:
    with open(path, "rb") as f:
        data = f.read()

    o = 0
    ver = _u32(data, o)
    o += 4
    unk1 = _u32(data, o)
    o += 4
    unkf = _f32(data, o)
    o += 4
    weather, o = _rstr(data, o)
    track, o = _rstr(data, o)
    layout, o = _rstr(data, o)
    ncars = _u32(data, o)
    o += 4
    unk2 = _u32(data, o)
    o += 4
    niv = _u32(data, o)
    o += 4
    ivs = list(struct.unpack_from(f"<{niv}I", data, o))
    o += niv * 4
    cfg = _config_start(data)

    nziv = [v for v in ivs if v > 0]
    avg_iv = int(sum(nziv) / len(nziv)) if nziv else 50000

    cars = []
    for _ in range(ncars):
        prelude = _u32(data, o)
        o += 4
        model, o = _rstr(data, o)
        driver, o = _rstr(data, o)
        nation, o = _rstr(data, o)
        unk3 = _u32(data, o)
        o += 4
        color, o = _rstr(data, o)
        _v1 = _u32(data, o)
        o += 4
        nw = _u32(data, o)
        o += 4
        pad = data[o : o + 16]
        o += 16
        fstart = o
        nxt = _next_car(data, fstart + STRIDE)
        fend = min(nxt if nxt else cfg, cfg)
        nf = (fend - fstart) // STRIDE
        cars.append(
            dict(
                model=model,
                driver=driver,
                nation=nation,
                unk3=unk3,
                color=color,
                nw=nw,
                pad=pad,
                fstart=fstart,
                nf=nf,
                avg_iv=avg_iv,
            )
        )
        o = fend

    return dict(
        ver=ver,
        unk1=unk1,
        unkf=unkf,
        weather=weather,
        track=track,
        layout=layout,
        unk2=unk2,
        cars=cars,
        data=data,
    )


# geometry


def _total_dist(data: bytes, fstart: int, nf: int, max_jump: float = 50.0) -> float:
    total = 0.0
    for i in range(nf - 1):
        b = fstart + i * STRIDE
        bn = fstart + (i + 1) * STRIDE
        dx = _f32(data, bn + 4) - _f32(data, b + 4)
        dz = _f32(data, bn + 12) - _f32(data, b + 12)
        r = math.sqrt(dx * dx + dz * dz)
        if r < max_jump:
            total += r
    return total


def _dist_from_indices(
    data: bytes, fstart: int, idxs: list[int], max_jump: float = 50.0
) -> float:
    total = 0.0
    for k in range(len(idxs) - 1):
        b = fstart + idxs[k] * STRIDE
        bn = fstart + idxs[k + 1] * STRIDE
        dx = _f32(data, bn + 4) - _f32(data, b + 4)
        dz = _f32(data, bn + 12) - _f32(data, b + 12)
        r = math.sqrt(dx * dx + dz * dz)
        if r < max_jump:
            total += r
    return total


# subsampling


def _subsample_indices(n_src: int, n_tgt: int) -> list[int]:
    """Return ~n_tgt evenly-spaced indices into [0, n_src)."""
    ratio = n_src / max(n_tgt, 1)
    idxs: list[int] = []
    pos = 0.0
    while pos < n_src:
        idxs.append(min(int(round(pos)), n_src - 1))
        pos += ratio
    return idxs


def _extract_frames(data: bytes, fstart: int, indices: list[int]) -> bytes:
    out = bytearray()
    for i in indices:
        out += data[fstart + i * STRIDE : fstart + i * STRIDE + STRIDE]
    return bytes(out)


def merge(sources: list[tuple[str, int]], out_path: str) -> None:
    if len(sources) < 2:
        raise ValueError("Need at least two source replays.")

    print(f"Loading {len(sources)} replays …\n")
    parsed = []
    for path, idx in sources:
        r = parse(path)
        c = r["cars"][idx]
        print(
            f"  {os.path.basename(path)}  car[{idx}]  "
            f"{c['model']} | {c['driver']!r} | "
            f"{c['nf']} frames @ {c['avg_iv']}µs"
        )
        parsed.append((r, c))

    # Reference = first replay; all others are normalised to its m/frame density.
    ref_r, ref_c = parsed[0]
    ref_dist = _total_dist(ref_r["data"], ref_c["fstart"], ref_c["nf"])
    ref_mpf = ref_dist / ref_c["nf"]
    prelude = ref_c["avg_iv"]

    print(f"\nReference: {ref_mpf:.4f} m/frame  prelude={prelude}µs")

    all_bytes: list[bytes] = []
    frame_counts: list[int] = []

    for i, (r, c) in enumerate(parsed):
        dist = _total_dist(r["data"], c["fstart"], c["nf"])
        if i == 0:
            # Reference run — use all frames unchanged.
            idxs = list(range(c["nf"]))
        else:
            # Subsample to match reference m/frame density.
            tgt = max(1, int(round(dist / ref_mpf)))
            idxs = _subsample_indices(c["nf"], tgt)
            # Drop the leading zero-duration frame (interval[0] == 0).
            if len(idxs) > 1:
                idxs = idxs[1:]

        actual_mpf = _dist_from_indices(r["data"], c["fstart"], idxs) / max(len(idxs), 1)
        tag = "(reference)" if i == 0 else f"subsampled {c['nf']}→{len(idxs)}"
        print(
            f"  Run {i + 1}: {dist:.1f}m / {c['nf']} frames → {len(idxs)} frames  "
            f"{actual_mpf:.4f} m/frame  {tag}"
        )

        all_bytes.append(_extract_frames(r["data"], c["fstart"], idxs))
        frame_counts.append(len(idxs))

    total_frames = sum(frame_counts)
    duration_s = total_frames * prelude / 1e6
    print(f"\nTotal: {total_frames} frames  {duration_s:.1f}s ({duration_s / 60:.1f} min)")

    # Interval table: single leading 0, then constant prelude for every frame.
    combined_ivs = [0] + [prelude] * (total_frames - 1)

    # Assemble output.
    out = bytearray()
    out += _pu32(ref_r["ver"])
    out += _pu32(ref_r["unk1"])
    out += _pf32(ref_r["unkf"])
    out += _pstr(ref_r["weather"])
    out += _pstr(ref_r["track"])
    out += _pstr(ref_r["layout"])
    out += _pu32(1)  # num_cars
    out += _pu32(ref_r["unk2"])
    out += _pu32(len(combined_ivs))
    out += struct.pack(f"<{len(combined_ivs)}I", *combined_ivs)

    # Car metadata from reference car, with prelude and v1 updated.
    out += _pu32(prelude)
    out += _pstr(ref_c["model"])
    out += _pstr(ref_c["driver"])
    out += _pstr(ref_c["nation"])
    out += _pu32(ref_c["unk3"])
    out += _pstr(ref_c["color"])
    out += _pu32(total_frames)  # v1 = total frame count
    out += _pu32(ref_c["nw"])
    out += ref_c["pad"]

    for fb in all_bytes:
        out += fb

    with open(out_path, "wb") as f:
        f.write(bytes(out))

    print(f"\nWrote: {out_path}  ({len(out) / 1024 / 1024:.2f} MB)")
    offset = 0
    for i, n in enumerate(frame_counts):
        t0 = offset * prelude / 1e6
        t1 = (offset + n) * prelude / 1e6
        print(f"  Run {i + 1}: frames {offset}–{offset + n - 1}  t={t0:.1f}s–{t1:.1f}s")
        offset += n


# CLI


if __name__ == "__main__":
    args = sys.argv[1:]
    # Expected: out replay1 idx1 replay2 idx2 [replay3 idx3 ...]
    if len(args) < 5 or (len(args) - 1) % 2 != 0:
        print(__doc__)
        sys.exit(1)

    out_path = args[0]
    pairs = args[1:]
    sources: list[tuple[str, int]] = []
    for k in range(0, len(pairs), 2):
        path = pairs[k]
        if not os.path.exists(path):
            print(f"File not found: {path}")
            sys.exit(1)
        try:
            idx = int(pairs[k + 1])
        except ValueError:
            print(f"Car index must be integer, got: {pairs[k + 1]!r}")
            sys.exit(1)
        sources.append((path, idx))

    merge(sources, out_path)
