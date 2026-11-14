"""Read and edit OpenToonz .pli vector levels stroke by stroke, without going through OpenToonz.

A .pli is a header followed by tags (opentoonz image/pli/pli_io.cpp, tiio_pli.cpp, checked against v1.8.0):

  STYLE tags + one GROUP(type PALETTE) that lists them    the level palette
  per drawing:  IMAGE_BEGIN, PRECISION_SCALE, COLOR / stroke / GROUP tags, [INTERSECTION_DATA], IMAGE

GROUP and IMAGE tags name their children by absolute file offset, so adding one byte anywhere would break
every later reference. PliDoc therefore keeps each tag as raw bytes, resolves those references to tag objects
when it loads, and writes the offsets again when it saves. Everything it does not edit is copied byte for byte.

Integers of "dynamic" width (1, 2 or 4 bytes, sign-magnitude) depend on the last SET_DATA tag. Each tag
remembers the width it was written with; the writer puts a SET_DATA tag wherever the width has to change.

What this cannot do: write INTERSECTION_DATA (the painted regions). Strokes can be added to a drawing that has
painted regions, and a stroke can be removed only when no region depends on it or on a later stroke.
"""
from __future__ import annotations

import math
import os
import struct
import uuid

SET8, SET16, SET32, TEXT = 1, 2, 3, 4
CHAIN, GROUP, IMAGE, COLOR, STYLE, INTERSECTION, IMAGE_BEGIN, LOOP, OUTLINE, PRECISION = 10, 14, 16, 17, 20, 21, 22, 23, 24, 25
GROUP_STROKE, GROUP_PALETTE = 1, 5
SP_BYTE, SP_INT, SP_DOUBLE, SP_USHORT, SP_RASTER, SP_STRING = 1, 2, 3, 4, 5, 6
SOLID_STYLE = b"\x02\x03\x00\x00\x00"  # SP_INT 3: the tag id of TSolidColorStyle
_SET = {SET8: 1, SET16: 2, SET32: 4}
_SET_TAG = {1: SET8, 2: SET16, 4: SET32}
DEFAULT_SCALE = 128 * 128  # what OpenToonz writes for every drawing (precision 2)


class Tag:
    __slots__ = ("type", "payload", "w", "refs", "off")

    def __init__(self, type_: int, payload: bytes = b"", w: int = 2, refs: list | None = None):
        self.type, self.payload, self.w, self.refs, self.off = type_, payload, w, refs, 0


def _rd(buf: bytes, off: int, w: int) -> tuple[int, int]:
    """Signed dynamic integer (sign-magnitude)."""
    v = int.from_bytes(buf[off:off + w], "little")
    top = 1 << (8 * w - 1)
    return (-(v & (top - 1)) if v & top else v), off + w


def _wr(v: int, w: int) -> bytes:
    return (abs(v) | ((1 << (8 * w - 1)) if v < 0 else 0)).to_bytes(w, "little")


def _width(lo: int, hi: int) -> int:
    """Same thresholds as ParsedPliImp::setDynamicTypeBytesNum."""
    if hi > 32767 or lo < -32767:
        return 4
    return 2 if hi > 127 or lo < -127 else 1


def _frame_of(payload: bytes) -> int:
    return payload[0] | (payload[1] << 8)


class PliDoc:
    def __init__(self):
        self.major, self.minor, self.creator = 71, 0, b""
        self.autoclose = (2, 1, 15)  # tolerance 1.15, the OpenToonz default
        self.tags: list[Tag] = []

    # ------------------------------------------------------------------ load / save
    @classmethod
    def new(cls) -> PliDoc:
        """An empty level with the palette OpenToonz gives a new vector level: page 'colors', style 1 black."""
        d = cls()
        pages = Tag(STYLE, b"\x00\x00\x00\x00" + bytes([SP_STRING]) + struct.pack("<H", 6) + b"colors")
        d.tags = [pages, Tag(GROUP, bytes([GROUP_PALETTE]), refs=[pages])]
        d.style_for((0, 0, 0, 255))
        return d

    @classmethod
    def load(cls, path: str) -> PliDoc:
        data = open(path, "rb").read()
        if data[:4] != b"PLIM":
            raise ValueError("not a little-endian PLI file")
        d = cls()
        d.major, d.minor = data[4], data[5]
        if not 71 <= d.major < 150:
            # below 6.5 the header differs; from 150 on a drawing number carries a suffix string
            raise ValueError(f"PLI version {d.major}.{d.minor} is not supported (need 71 to 149)")
        n = struct.unpack_from("<H", data, 6)[0]
        d.creator = bytes(data[8:8 + n])
        off = 8 + n + 4 + 2
        d.autoclose = tuple(data[off:off + 3])
        off += 3
        w, by_off = 2, {}
        while off < len(data):
            start, t = off, data[off]
            off += 1
            if t == 0xFF:
                t16 = struct.unpack_from("<H", data, off)[0]
                off += 2
                lid, ttype = t16 >> 14, t16 & 0x3FFF
            else:
                lid, ttype = t >> 6, t & 0x3F
            ln = 0
            if lid:
                size = (1, 2, 4)[lid - 1]
                ln = int.from_bytes(data[off:off + size], "little")
                off += size
            if ttype == 0:
                break
            if ttype in _SET:
                w = _SET[ttype]
                continue
            if ttype > 63:
                raise ValueError(f"PLI tag type {ttype} is not supported")
            tag = Tag(ttype, bytes(data[off:off + ln]), w)
            if len(tag.payload) != ln:
                raise ValueError("truncated PLI file")
            off += ln
            if ttype in (GROUP, IMAGE):
                head = 1 if ttype == GROUP else 3
                refs = []
                for p in range(head, ln, w):
                    target = by_off.get(int.from_bytes(tag.payload[p:p + w], "little"))
                    if target is None:
                        raise ValueError(f"PLI tag at {start} points to an offset that is not a tag")
                    refs.append(target)
                tag.refs, tag.payload = refs, tag.payload[:head]
            by_off[start] = tag
            d.tags.append(tag)
        return d

    def to_bytes(self) -> bytes:
        out = bytearray(b"PLIM" + bytes([self.major, self.minor]) + struct.pack("<H", len(self.creator)) + self.creator)
        out += struct.pack("<IH", 0, len(self.frames())) + bytes(self.autoclose)
        state: int | None = 2

        def emit(tag: Tag, payload: bytes, w: int) -> None:
            nonlocal state, out
            if state != w:
                out.append(_SET_TAG[w])
                state = w
            tag.off = len(out)
            ln = len(payload)
            if ln == 0:
                out.append(tag.type)
            elif ln < 256:
                out += bytes([tag.type | 0x40, ln])
            elif ln < 65535:
                out += bytes([tag.type | 0x80]) + struct.pack("<H", ln)
            else:
                out += bytes([tag.type | 0xC0]) + struct.pack("<I", ln)
            out += payload

        for tag in self.tags:
            if tag.refs is None:
                emit(tag, tag.payload, tag.w)
            else:
                offs = [r.off for r in tag.refs]
                if any(o == 0 for o in offs):
                    raise ValueError("PLI tag refers to a tag that is not written before it")
                w = _width(min(offs), max(offs)) if offs else 2
                emit(tag, tag.payload + b"".join(o.to_bytes(w, "little") for o in offs), w)
            if tag.type == IMAGE_BEGIN:
                state = None  # OpenToonz starts every drawing at width 2 again; never rely on what came before
        out.append(0)
        for tag in self.tags:
            tag.off = 0
        return bytes(out)

    def save(self, path: str) -> None:
        data = self.to_bytes()
        tmp = f"{path}.{uuid.uuid4().hex}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

    # ------------------------------------------------------------------ palette
    def _palette(self) -> Tag:
        for t in self.tags:
            if t.type == GROUP and t.payload[:1] == bytes([GROUP_PALETTE]):
                return t
        raise ValueError("PLI file has no palette")

    def styles(self) -> dict[int, tuple[int, int, int, int] | None]:
        """Style id -> RGBA for solid colours, None for any other kind of style (textures, brushes...)."""
        out: dict[int, tuple[int, int, int, int] | None] = {}
        for t in self._palette().refs:
            p = t.payload
            sid = p[0] | (p[1] << 8)
            if len(p) <= 4 or (sid == 0 and p[4] == SP_STRING and SOLID_STYLE not in p):
                continue  # page names, or a multi-palette frame marker
            i = p.find(SOLID_STYLE, 4)
            rest = p[i + len(SOLID_STYLE):] if i >= 0 else b""
            solid = len(rest) == 8 and all(rest[k] == SP_BYTE for k in (0, 2, 4, 6))
            out[sid] = (rest[1], rest[3], rest[5], rest[7]) if solid else None
        return out

    def style_for(self, rgba: tuple[int, int, int, int]) -> int:
        """The id of the solid style with exactly this colour; added to the palette when it is not there."""
        rgba = tuple(int(v) for v in rgba)
        known = self.styles()
        for sid, col in known.items():
            if col == rgba:
                return sid
        sid = max(known, default=0) + 1
        name = f"color_{sid}".encode()
        tag = Tag(STYLE, struct.pack("<HH", sid, 0) + bytes([SP_STRING]) + struct.pack("<H", len(name)) + name
                  + SOLID_STYLE + bytes(v for c in rgba for v in (SP_BYTE, c)))
        pal = self._palette()
        self.tags.insert(self.tags.index(pal), tag)
        pal.refs.append(tag)
        return sid

    # ------------------------------------------------------------------ drawings
    def _image(self, frame: int) -> Tag | None:
        for t in self.tags:
            if t.type == IMAGE and _frame_of(t.payload) == frame:
                return t
        return None

    def frames(self) -> list[int]:
        return sorted(_frame_of(t.payload) for t in self.tags if t.type == IMAGE)

    def _scale(self, image: Tag) -> int:
        for r in image.refs:
            if r.type == PRECISION:
                return _rd(r.payload, 0, r.w)[0]
        return 128

    def _walk(self, image: Tag):
        """Yield (stroke tag, style id, parent tag) in the order OpenToonz builds the image."""
        style = -1

        def go(parent: Tag):
            nonlocal style
            for r in parent.refs:
                if r.type == COLOR:
                    style = int.from_bytes(r.payload[2:2 + r.w], "little")
                elif r.type == GROUP:
                    yield from go(r)
                elif r.type in (CHAIN, LOOP):
                    yield r, style, parent
        yield from go(image)

    def strokes(self, frame: int) -> list[dict]:
        """Strokes of a drawing. points are the control points of the quadratic chain (x, y, thickness) in
        vector units: on-curve, off-curve, on-curve, ... thickness is the half width."""
        image = self._image(frame)
        if image is None:
            return []
        scale, out = self._scale(image), []
        for i, (t, style, _parent) in enumerate(self._walk(image)):
            p, w = t.payload, t.w
            ratio = p[0] / 255.0
            x, q = _rd(p, 1, w)
            y, q = _rd(p, q, w)
            pts = [(x / scale, y / scale, p[q] * ratio)]
            q += 1
            while q < len(p):
                dx, q = _rd(p, q, w)
                dy, q = _rd(p, q, w)
                x, y = x + dx, y + dy
                pts.append((x / scale, y / scale, p[q] * ratio))
                q += 1
            out.append({"index": i, "loop": t.type == LOOP, "style": style, "points": pts})
        return out

    def _region_strokes(self, image: Tag) -> set[int]:
        """Stroke indices the painted regions of a drawing are built on."""
        used: set[int] = set()
        for r in image.refs:
            if r.type != INTERSECTION:
                continue
            p, w = r.payload, r.w
            count, q = struct.unpack_from("<I", p, 0)[0], 4
            for _ in range(count):
                idx, q = _rd(p, q, w)
                q += 2 * w + 2                      # currInter, nextBranch, style
                q += 1 if p[q] & 0x80 else 8        # w: one byte for exactly 0 or 1, else a packed double
                used.add(idx)
        return used

    @staticmethod
    def _stroke_tag(points: list[tuple[float, float, float]], loop: bool, scale: int) -> Tag:
        if len(points) < 3 or len(points) % 2 == 0:
            raise ValueError("a stroke needs an odd number of control points, at least 3")
        ints = [(round(x * scale), round(y * scale)) for x, y, _ in points]
        deltas = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(ints, ints[1:])]
        vals = [v for xy in [ints[0]] + deltas for v in xy]
        w = _width(min(vals), max(vals))
        max_t = min(255, max(1, math.ceil(max(t for _, _, t in points))))
        tb = [max(0, min(255, round(t * 255 / max_t))) for _, _, t in points]
        body = bytes([max_t]) + _wr(ints[0][0], w) + _wr(ints[0][1], w) + bytes([tb[0]])
        for (dx, dy), t in zip(deltas, tb[1:]):
            body += _wr(dx, w) + _wr(dy, w) + bytes([t])
        return Tag(LOOP if loop else CHAIN, body, w)

    def append(self, frame: int, strokes: list[dict]) -> list[int]:
        """Add strokes on top of a drawing (the drawing is created when it does not exist).
        strokes: [{"points": [(x, y, thickness), ...] control points, "style": id, "loop": bool}].
        Returns the indices of the new strokes."""
        if not 1 <= frame <= 65535:
            raise ValueError("drawing number must be 1..65535")
        image = self._image(frame)
        if image is None:
            fid = struct.pack("<H", frame) + b"\x00"
            begin, prec = Tag(IMAGE_BEGIN, fid), Tag(PRECISION, _wr(DEFAULT_SCALE, 2), 2)
            image = Tag(IMAGE, fid, refs=[prec])
            at = len(self.tags)
            for i, t in enumerate(self.tags):
                if t.type == IMAGE_BEGIN and _frame_of(t.payload) > frame:
                    at = i
                    break
            self.tags[at:at] = [begin, prec, image]
        scale = self._scale(image)
        first = sum(1 for _ in self._walk(image))
        new: list[Tag] = []
        style = None
        for s in strokes:
            sid = int(s.get("style", 1))
            if sid != style:
                style = sid
                w = _width(0, sid)
                new.append(Tag(COLOR, b"\x01\x03" + sid.to_bytes(w, "little"), w))  # SOLID, STROKE_COLOR
            new.append(self._stroke_tag(s["points"], bool(s.get("loop")), scale))
        at = self.tags.index(image)
        self.tags[at:at] = new
        # the region data must come after every stroke it could meet, as OpenToonz writes it
        keep = [r for r in image.refs if r.type != INTERSECTION]
        image.refs = keep + new + [r for r in image.refs if r.type == INTERSECTION]
        return list(range(first, first + len(strokes)))

    def remove(self, frame: int, index: int) -> None:
        """Remove one stroke. Refused when a painted region is built on it or on a later stroke, because the
        region data names strokes by position and this module cannot rewrite it."""
        image = self._image(frame)
        found = list(self._walk(image)) if image is not None else []
        if not 0 <= index < len(found):
            raise ValueError(f"drawing {frame} has no stroke {index}")
        if any(i >= index for i in self._region_strokes(image)):
            raise ValueError(f"stroke {index} of drawing {frame}, or a later stroke, borders a painted area; "
                             f"remove it in OpenToonz so the paint is rebuilt")
        tag, _style, parent = found[index]
        parent.refs = [r for r in parent.refs if r is not tag]
        self.tags = [t for t in self.tags if t is not tag]
        if parent.type == GROUP and not any(r.type in (CHAIN, LOOP, GROUP) for r in parent.refs):
            for holder in self.tags:
                if holder.refs and parent in holder.refs:
                    holder.refs = [r for r in holder.refs if r is not parent]
            self.tags = [t for t in self.tags if t is not parent]


# ---------------------------------------------------------------------- geometry
def through_points(points: list[tuple[float, float, float]], closed: bool = False) -> list[tuple[float, float, float]]:
    """Control points of a smooth quadratic chain that passes through every given (x, y, thickness) point.
    Each span is a Catmull-Rom cubic cut in half; each half becomes one quadratic."""
    pts = [tuple(map(float, p)) for p in points]
    pts = [p for i, p in enumerate(pts) if i == 0 or (p[0], p[1]) != (pts[i - 1][0], pts[i - 1][1])]
    if closed and len(pts) > 2 and (pts[0][0], pts[0][1]) == (pts[-1][0], pts[-1][1]):
        pts.pop()
    n = len(pts)
    if n < 2:
        raise ValueError("a stroke needs at least 2 different points")
    if n == 2:
        a, b = pts
        return [a, tuple((u + v) / 2 for u, v in zip(a, b)), b]
    closed = closed and n > 2

    def at(i):
        return pts[i % n] if closed else pts[max(0, min(n - 1, i))]

    out = [pts[0]]
    for i in range(n if closed else n - 1):
        p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
        b1 = tuple(p1[k] + (p2[k] - p0[k]) / 6 for k in range(3))
        b2 = tuple(p2[k] - (p3[k] - p1[k]) / 6 for k in range(3))
        mid = tuple((p1[k] + 3 * b1[k] + 3 * b2[k] + p2[k]) / 8 for k in range(3))
        # best single quadratic for each half of the cubic p1 b1 b2 p2
        c1 = tuple((3 * (p1[k] + b1[k]) / 2 + 3 * (p1[k] + 2 * b1[k] + b2[k]) / 4 - p1[k] - mid[k]) / 4
                   for k in range(3))
        c2 = tuple((3 * (b1[k] + 2 * b2[k] + p2[k]) / 4 + 3 * (b2[k] + p2[k]) / 2 - mid[k] - p2[k]) / 4
                   for k in range(3))
        out += [(c1[0], c1[1], max(0.0, c1[2])), (mid[0], mid[1], max(0.0, mid[2])),
                (c2[0], c2[1], max(0.0, c2[2])), p2]
    return out


def sample(points: list[tuple[float, float, float]], per_quad: int = 4) -> list[tuple[float, float, float]]:
    """Points on the curve of a quadratic chain (to show a stroke as a polyline)."""
    out = [points[0]]
    for i in range(0, len(points) - 2, 2):
        a, c, b = points[i], points[i + 1], points[i + 2]
        for s in range(1, per_quad + 1):
            t = s / per_quad
            out.append(tuple((1 - t) ** 2 * a[k] + 2 * t * (1 - t) * c[k] + t * t * b[k] for k in range(3)))
    return out
