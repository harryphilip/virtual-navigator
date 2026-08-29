"""Minimal GRIB edition-1 writer for 10 m wind (UGRD/VGRD) on a lat/lon grid.

Enough of the standard to be read by the GRIB viewers sailors actually use
(qtVlm, XyGrib, OpenCPN, Expedition, LuckGrib): one message per parameter per
forecast hour, regular lat/lon grid, simple packing, no bitmap.

Grids are supplied as row-major lists scanning west→east then north→south
(scan mode 0), values in m/s.
"""
import math
import struct


def wind_grib(ref_dt, la1, lo1, dstep, ni, nj, frames):
    """Build a GRIB1 file.

    ref_dt   : reference (issue) time, aware datetime UTC
    la1, lo1 : northwest corner of the grid, degrees
    dstep    : grid spacing in degrees (same in lat and lon)
    ni, nj   : points per row / number of rows
    frames   : [(forecast_hour, u_values, v_values), ...]
    """
    out = bytearray()
    for fh, u, v in frames:
        out += _message(ref_dt, fh, 33, la1, lo1, dstep, ni, nj, u)
        out += _message(ref_dt, fh, 34, la1, lo1, dstep, ni, nj, v)
    return bytes(out)


def _message(ref_dt, p1, param, la1, lo1, dstep, ni, nj, values):
    pds = _pds(ref_dt, p1, param)
    gds = _gds(la1, lo1, dstep, ni, nj)
    bds = _bds(values)
    total = 8 + len(pds) + len(gds) + len(bds) + 4
    return (b"GRIB" + _u3(total) + b"\x01" + pds + gds + bds + b"7777")


def _pds(ref_dt, p1, param):
    return bytes([
        0, 0, 28,                 # section length
        3,                        # parameter table version
        7,                        # originating centre (NCEP tables)
        96,                       # generating process id
        255,                      # grid id: defined in GDS
        0x80,                     # GDS present, no BMS
        param,                    # 33 = U wind, 34 = V wind
        105, 0, 10,               # level: 10 m above ground
        ref_dt.year % 100, ref_dt.month, ref_dt.day,
        ref_dt.hour, ref_dt.minute,
        1,                        # forecast time unit: hours
        min(p1, 255), 0,          # P1, P2
        0,                        # time range: valid at ref + P1
        0, 0,                     # number in average
        0,                        # number missing
        (ref_dt.year - 1) // 100 + 1,   # century
        0,                        # sub-centre
        0, 0,                     # decimal scale factor D = 0
    ])


def _gds(la1, lo1, dstep, ni, nj):
    la2 = la1 - dstep * (nj - 1)          # southernmost row
    lo2 = lo1 + dstep * (ni - 1)          # easternmost column
    return (bytes([0, 0, 32, 0, 255, 0])  # length, NV, PV/PL, latlon grid
            + struct.pack(">HH", ni, nj)
            + _s3(round(la1 * 1000)) + _s3(round(lo1 * 1000))
            + bytes([0x80])               # increments given, earth spherical
            + _s3(round(la2 * 1000)) + _s3(round(lo2 * 1000))
            + struct.pack(">HH", round(dstep * 1000), round(dstep * 1000))
            + bytes([0])                  # scan mode: +i (W→E), -j (N→S)
            + b"\x00\x00\x00\x00")


def _bds(values, nbits=12):
    vmin = min(values)
    vmax = max(values)
    rng = vmax - vmin
    if rng <= 0:
        e = 0
        packed = [0] * len(values)
    else:
        e = math.ceil(math.log2(rng / (2 ** nbits - 1)))
        scale = 2.0 ** -e
        packed = [min(2 ** nbits - 1, max(0, round((v - vmin) * scale)))
                  for v in values]

    bits = _pack_bits(packed, nbits)
    nbits_total = len(values) * nbits
    unused = len(bits) * 8 - nbits_total
    if (11 + len(bits)) % 2:              # keep section length even
        bits += b"\x00"
        unused += 8
    length = 11 + len(bits)
    return (_u3(length)
            + bytes([unused & 0x0F])      # simple packing, grid point data
            + _s2(e)
            + _ibm_float(vmin)
            + bytes([nbits])
            + bits)


def _pack_bits(vals, nbits):
    out = bytearray()
    acc = 0
    nacc = 0
    for v in vals:
        acc = (acc << nbits) | v
        nacc += nbits
        while nacc >= 8:
            nacc -= 8
            out.append((acc >> nacc) & 0xFF)
    if nacc:
        out.append((acc << (8 - nacc)) & 0xFF)
    return bytes(out)


def _ibm_float(x):
    if x == 0.0:
        return b"\x00\x00\x00\x00"
    sign = 0
    if x < 0:
        sign = 0x80
        x = -x
    e = 64
    while x >= 1.0:
        x /= 16.0
        e += 1
    while x < 1.0 / 16.0:
        x *= 16.0
        e -= 1
    m = round(x * (1 << 24))
    if m >= (1 << 24):
        m >>= 4
        e += 1
    return bytes([sign | e]) + m.to_bytes(3, "big")


def _u3(v):
    return v.to_bytes(3, "big")


def _s3(v):
    """3-byte signed-magnitude integer (GRIB1 convention)."""
    if v < 0:
        return ((-v) | 0x800000).to_bytes(3, "big")
    return v.to_bytes(3, "big")


def _s2(v):
    if v < 0:
        return ((-v) | 0x8000).to_bytes(2, "big")
    return v.to_bytes(2, "big")


# ---- self-check reader (round-trip validation in tests) --------------------

def read_messages(buf):
    """Decode our own GRIB1 output: [(param, p1, la1, lo1, dstep, ni, nj, values)]."""
    msgs = []
    i = 0
    while i < len(buf) - 4:
        assert buf[i:i + 4] == b"GRIB", "bad magic"
        total = int.from_bytes(buf[i + 4:i + 7], "big")
        m = buf[i:i + total]
        assert m[-4:] == b"7777", "bad trailer"
        pds_len = int.from_bytes(m[8:11], "big")
        pds = m[8:8 + pds_len]
        param, p1 = pds[8], pds[18]
        gds = m[8 + pds_len:]
        gds_len = int.from_bytes(gds[0:3], "big")
        ni, nj = struct.unpack(">HH", gds[6:10])
        la1 = _rd_s3(gds[10:13]) / 1000.0
        lo1 = _rd_s3(gds[13:16]) / 1000.0
        di = struct.unpack(">H", gds[23:25])[0] / 1000.0
        bds = gds[gds_len:]
        bds_len = int.from_bytes(bds[0:3], "big")
        unused = bds[3] & 0x0F
        e = _rd_s2(bds[4:6])
        ref = _rd_ibm(bds[6:10])
        nbits = bds[10]
        data = bds[11:bds_len]
        n = (len(data) * 8 - unused) // nbits
        vals = []
        acc = nacc = 0
        it = iter(data)
        for byte in it:
            acc = (acc << 8) | byte
            nacc += 8
            while nacc >= nbits and len(vals) < n:
                nacc -= nbits
                vals.append(((acc >> nacc) & ((1 << nbits) - 1)))
        values = [ref + x * (2.0 ** e) for x in vals[:ni * nj]]
        msgs.append((param, p1, la1, lo1, di, ni, nj, values))
        i += total
    return msgs


def _rd_s3(b):
    v = int.from_bytes(b, "big")
    return -(v & 0x7FFFFF) if v & 0x800000 else v


def _rd_s2(b):
    v = int.from_bytes(b, "big")
    return -(v & 0x7FFF) if v & 0x8000 else v


def _rd_ibm(b):
    if b == b"\x00\x00\x00\x00":
        return 0.0
    sign = -1.0 if b[0] & 0x80 else 1.0
    e = (b[0] & 0x7F) - 64
    m = int.from_bytes(b[1:4], "big") / float(1 << 24)
    return sign * m * (16.0 ** e)
