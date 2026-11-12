"""Rewrite Chrome MediaRecorder's fragmented MP4 (moov + moof/mdat pairs, duration 0) as a regular MP4.

The fragmented file plays in browsers and VLC, but its header says 0 s and some players (QuickTime,
PowerPoint, projector boxes) will not seek it or show its length. This keeps every sample's bytes
unchanged and builds a regular moov in front: one video track, a full sample table (stts, stss, stsz,
stsc, co64) and real durations. No re-encoding, no ffmpeg. Handles what MediaRecorder writes: one track,
tfhd with default-base-is-moof, trun with data offset, per-sample duration and size, optional first-sample
flags and per-sample flags/composition offsets.

Usage: python remux_mp4.py in.mp4 out.mp4
"""
import struct
import sys

CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "mvex", "moof", "traf", "dinf", "edts"}


def boxes(b, off=0, end=None):
    end = len(b) if end is None else end
    while off + 8 <= end:
        size, typ = struct.unpack(">I4s", b[off:off + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", b[off + 8:off + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        yield typ.decode("latin1"), off, size, hdr
        off += size


def child(b, parent, name):
    _, off, size, hdr = parent
    for bx in boxes(b, off + hdr, off + size):
        if bx[0] == name:
            return bx
    raise KeyError(name)


def raw(b, bx):
    return b[bx[1]:bx[1] + bx[2]]


def box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ.encode()) + payload


def full(typ, version, flags, payload):
    return box(typ, struct.pack(">I", (version << 24) | flags) + payload)


def read_fragments(b):
    """[(sample bytes, duration, is_sync, cts_offset)] in decode order, and the first decode time."""
    samples, first_dt = [], None
    for top in boxes(b):
        if top[0] != "moof":
            continue
        moof_off = top[1]
        traf = child(b, top, "traf")
        tfhd = child(b, traf, "tfhd")
        p = tfhd[1] + tfhd[3]
        vf = struct.unpack(">I", b[p:p + 4])[0]
        flags, q = vf & 0xFFFFFF, p + 8          # skip version/flags and track_ID
        base = moof_off
        if flags & 0x1:
            base = struct.unpack(">Q", b[q:q + 8])[0]
            q += 8
        if flags & 0x2:
            q += 4
        d_dur = d_size = d_flags = None
        if flags & 0x8:
            d_dur = struct.unpack(">I", b[q:q + 4])[0]
            q += 4
        if flags & 0x10:
            d_size = struct.unpack(">I", b[q:q + 4])[0]
            q += 4
        if flags & 0x20:
            d_flags = struct.unpack(">I", b[q:q + 4])[0]
        try:
            tfdt = child(b, traf, "tfdt")
            p = tfdt[1] + tfdt[3]
            v = b[p]
            dt = struct.unpack(">Q", b[p + 4:p + 12])[0] if v else struct.unpack(">I", b[p + 4:p + 8])[0]
            first_dt = dt if first_dt is None else first_dt
        except KeyError:
            pass
        for bx in boxes(b, traf[1] + traf[3], traf[1] + traf[2]):
            if bx[0] != "trun":
                continue
            p = bx[1] + bx[3]
            vf, count = struct.unpack(">II", b[p:p + 8])
            version, tf = vf >> 24, vf & 0xFFFFFF
            q = p + 8
            data = base
            if tf & 0x1:
                data = base + struct.unpack(">i", b[q:q + 4])[0]
                q += 4
            first_flags = None
            if tf & 0x4:
                first_flags = struct.unpack(">I", b[q:q + 4])[0]
                q += 4
            for i in range(count):
                dur, size, sflags, cts = d_dur, d_size, d_flags, 0
                if tf & 0x100:
                    dur = struct.unpack(">I", b[q:q + 4])[0]
                    q += 4
                if tf & 0x200:
                    size = struct.unpack(">I", b[q:q + 4])[0]
                    q += 4
                if tf & 0x400:
                    sflags = struct.unpack(">I", b[q:q + 4])[0]
                    q += 4
                if tf & 0x800:
                    cts = struct.unpack(">i" if version else ">I", b[q:q + 4])[0]
                    q += 4
                if i == 0 and first_flags is not None:
                    sflags = first_flags
                sync = not ((sflags or 0) & 0x10000)          # sample_is_non_sync_sample
                samples.append((b[data:data + size], dur, sync, cts))
                data += size
    return samples, first_dt or 0


def remux(src, dst):
    b = open(src, "rb").read()
    tops = {t[0]: t for t in boxes(b)}
    ftyp, moov = tops["ftyp"], tops["moov"]
    samples, _ = read_fragments(b)
    mvhd = child(b, moov, "mvhd")
    trak = child(b, moov, "trak")
    tkhd, mdia = child(b, trak, "tkhd"), child(b, trak, "mdia")
    mdhd, hdlr, minf = child(b, mdia, "mdhd"), child(b, mdia, "hdlr"), child(b, mdia, "minf")
    stbl = child(b, minf, "stbl")
    stsd = child(b, stbl, "stsd")
    media_ts = struct.unpack(">I", raw(b, mdhd)[28:32] if raw(b, mdhd)[8] == 1 else raw(b, mdhd)[20:24])[0]
    movie_ts = struct.unpack(">I", raw(b, mvhd)[28:32] if raw(b, mvhd)[8] == 1 else raw(b, mvhd)[20:24])[0]
    media_dur = sum(s[1] for s in samples)
    movie_dur = media_dur * movie_ts // media_ts

    def with_duration(bx, ts_off_v0, ts_off_v1, dur):
        """A copy of mvhd/mdhd (duration right after timescale) or tkhd (at its own offset)."""
        r = bytearray(raw(b, bx))
        if r[8] == 1:
            struct.pack_into(">Q", r, ts_off_v1, dur)
        else:
            struct.pack_into(">I", r, ts_off_v0, dur)
        return bytes(r)
    new_mvhd = with_duration(mvhd, 24, 32, movie_dur)
    new_mdhd = with_duration(mdhd, 24, 32, media_dur)
    new_tkhd = with_duration(tkhd, 28, 36, movie_dur)    # tkhd: after ctime, mtime, track_ID, reserved

    stts, run = [], None
    for _, dur, _, _ in samples:
        if run and run[1] == dur:
            run[0] += 1
        else:
            run = [1, dur]
            stts.append(run)
    stts_b = full("stts", 0, 0, struct.pack(">I", len(stts)) + b"".join(struct.pack(">II", n, d) for n, d in stts))
    syncs = [i + 1 for i, s in enumerate(samples) if s[2]]
    stss_b = full("stss", 0, 0, struct.pack(">I", len(syncs)) + b"".join(struct.pack(">I", i) for i in syncs))
    stsz_b = full("stsz", 0, 0, struct.pack(">II", 0, len(samples)) + b"".join(struct.pack(">I", len(s[0])) for s in samples))
    ctts_b = b""
    if any(s[3] for s in samples):
        ctts_b = full("ctts", 0, 0, struct.pack(">I", len(samples)) +
                      b"".join(struct.pack(">II", 1, s[3] & 0xFFFFFFFF) for s in samples))
    # one chunk holding every sample, so the table is tiny and the offset is a single co64 entry
    stsc_b = full("stsc", 0, 0, struct.pack(">I", 1) + struct.pack(">III", 1, len(samples), 1))

    def build(chunk_offset):
        co64 = full("co64", 0, 0, struct.pack(">IQ", 1, chunk_offset))
        new_stbl = box("stbl", raw(b, stsd) + stts_b + ctts_b + stss_b + stsc_b + stsz_b + co64)
        others = b"".join(raw(b, bx) for bx in boxes(b, minf[1] + minf[3], minf[1] + minf[2]) if bx[0] != "stbl")
        new_minf = box("minf", others + new_stbl)
        new_mdia = box("mdia", new_mdhd + raw(b, hdlr) + new_minf)
        new_trak = box("trak", new_tkhd + new_mdia)
        return box("moov", new_mvhd + new_trak)

    head = raw(b, ftyp)
    payload = b"".join(s[0] for s in samples)
    moov_len = len(build(0))
    mdat_hdr = struct.pack(">I4sQ", 1, b"mdat", 16 + len(payload))   # 64-bit size: safe for any length
    new_moov = build(len(head) + moov_len + len(mdat_hdr))
    assert len(new_moov) == moov_len
    with open(dst, "wb") as f:
        f.write(head + new_moov + mdat_hdr + payload)
    return len(samples), media_dur / media_ts, len(syncs)


if __name__ == "__main__":
    n, secs, keys = remux(sys.argv[1], sys.argv[2])
    print(f"wrote {sys.argv[2]}: {n} frames, {secs:.1f} s, {keys} keyframes")
