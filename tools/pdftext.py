# -*- coding: utf-8 -*-
"""Minimal PDF text extractor (no third-party deps).

Handles: classic xref-less object scanning, FlateDecode streams, page tree,
per-font ToUnicode CMaps, text state (Tf/Tm/Td/TD/T*), Tj/TJ/'/\" show operators.
Returns positioned glyph runs so the caller can rebuild lines/columns.
"""
import re
import zlib


def balanced_after(buf, key):
    """Find `key` then return the balanced <<...>> block that follows."""
    i = buf.find(key)
    while i >= 0:
        j = buf.find(b'<<', i + len(key))
        k = buf.find(b'/D', i + len(key))
        if j < 0:
            return b''
        if k >= 0 and k < j:
            i = buf.find(key, i + 1)
            continue
        depth = 0
        p = j
        while p < len(buf):
            if buf[p:p + 2] == b'<<':
                depth += 1
                p += 2
                continue
            if buf[p:p + 2] == b'>>':
                depth -= 1
                p += 2
                if depth == 0:
                    return buf[j:p]
                continue
            p += 1
        return b''
    return b''


class PDF:
    def __init__(self, path):
        with open(path, 'rb') as f:
            self.buf = f.read()
        self.objs = {}
        self._scan_objects()

    # ---------- object scanning ----------
    def _scan_objects(self):
        buf = self.buf
        for m in re.finditer(rb'(?<![0-9])(\d+)\s+(\d+)\s+obj\b', buf):
            num = int(m.group(1))
            start = m.end()
            end = buf.find(b'endobj', start)
            if end < 0:
                continue
            self.objs[num] = buf[start:end]

    def raw(self, num):
        return self.objs.get(num)

    def dict_bytes(self, num):
        raw = self.raw(num)
        if raw is None:
            return b''
        i = raw.find(b'stream')
        return raw if i < 0 else raw[:i]

    def stream(self, num):
        raw = self.raw(num)
        if raw is None:
            return None
        m = re.search(rb'stream\r?\n', raw)
        if not m:
            return None
        data = raw[m.end():]
        # strip trailing endstream / EOL
        j = data.rfind(b'endstream')
        if j >= 0:
            data = data[:j]
        data = data.rstrip(b'\r\n')
        d = self.dict_bytes(num)
        if b'/FlateDecode' in d:
            try:
                data = zlib.decompress(data)
            except Exception:
                try:
                    data = zlib.decompressobj().decompress(data)
                except Exception:
                    return b''
        elif b'/ASCIIHexDecode' in d:
            hx = re.sub(rb'[^0-9A-Fa-f]', b'', data.split(b'>')[0])
            data = bytes.fromhex(hx.decode())
        return data

    # ---------- pages ----------
    def pages(self):
        """Return list of (page_obj_num, resources_obj_num or dict bytes)."""
        out = []
        # find catalog -> pages tree, walk Kids in order
        cat = None
        for num, raw in self.objs.items():
            if b'/Type' in raw and re.search(rb'/Type\s*/Catalog', raw):
                cat = num
                break
        seen = set()

        def walk(num, depth=0):
            if num in seen or depth > 40:
                return
            seen.add(num)
            d = self.dict_bytes(num)
            if re.search(rb'/Type\s*/Page[^s]', d):
                out.append(num)
                return
            mk = re.search(rb'/Kids\s*\[(.*?)\]', d, re.S)
            if mk:
                for r in re.finditer(rb'(\d+)\s+\d+\s+R', mk.group(1)):
                    walk(int(r.group(1)), depth + 1)

        if cat is not None:
            cd = self.dict_bytes(cat)
            m = re.search(rb'/Pages\s+(\d+)\s+\d+\s+R', cd)
            if m:
                walk(int(m.group(1)))
        if not out:
            for num in sorted(self.objs):
                d = self.dict_bytes(num)
                if re.search(rb'/Type\s*/Page[^s]', d):
                    out.append(num)
        return out

    def page_resources(self, pnum):
        d = self.dict_bytes(pnum)
        m = re.search(rb'/Resources\s+(\d+)\s+\d+\s+R', d)
        if m:
            return self.dict_bytes(int(m.group(1)))
        blk = balanced_after(d, b'/Resources')
        if blk:
            return blk
        # inherit from parent
        par = re.search(rb'/Parent\s+(\d+)\s+\d+\s+R', d)
        if par:
            pd = self.dict_bytes(int(par.group(1)))
            m = re.search(rb'/Resources\s+(\d+)\s+\d+\s+R', pd)
            if m:
                return self.dict_bytes(int(m.group(1)))
            blk = balanced_after(pd, b'/Resources')
            if blk:
                return blk
        return b''

    def page_fonts(self, pnum):
        """name(bytes) -> font object number"""
        res = self.page_resources(pnum)
        fonts = {}
        m = re.search(rb'/Font\s+(\d+)\s+\d+\s+R', res)
        if m:
            res = self.dict_bytes(int(m.group(1)))
        else:
            blk = balanced_after(res, b'/Font')
            if blk:
                res = blk
        for fm in re.finditer(rb'/([A-Za-z0-9_.+-]+)\s+(\d+)\s+\d+\s+R', res):
            fonts[fm.group(1)] = int(fm.group(2))
        return fonts

    def page_content(self, pnum):
        d = self.dict_bytes(pnum)
        chunks = []
        m = re.search(rb'/Contents\s*\[(.*?)\]', d, re.S)
        if m:
            for r in re.finditer(rb'(\d+)\s+\d+\s+R', m.group(1)):
                s = self.stream(int(r.group(1)))
                if s:
                    chunks.append(s)
        else:
            m = re.search(rb'/Contents\s+(\d+)\s+\d+\s+R', d)
            if m:
                s = self.stream(int(m.group(1)))
                if s:
                    chunks.append(s)
        return b'\n'.join(chunks)


# ---------- ToUnicode CMap ----------
def parse_tounicode(data):
    """Return dict: code(int) -> unicode str."""
    cmap = {}
    if not data:
        return cmap
    txt = data
    for blk in re.findall(rb'beginbfchar(.*?)endbfchar', txt, re.S):
        for m in re.finditer(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>', blk):
            src = int(m.group(1), 16)
            cmap[src] = utf16be(m.group(2))
    for blk in re.findall(rb'beginbfrange(.*?)endbfrange', txt, re.S):
        for m in re.finditer(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<([0-9A-Fa-f]*)>|\[(.*?)\])', blk, re.S):
            lo = int(m.group(1), 16)
            hi = int(m.group(2), 16)
            if m.group(4) is not None:
                base = m.group(4)
                if len(base) >= 4:
                    val = int(base[-4:], 16)
                    for i, code in enumerate(range(lo, hi + 1)):
                        cmap[code] = chr((val + i) & 0xFFFF)
                else:
                    for code in range(lo, hi + 1):
                        cmap[code] = chr(code)
            else:
                items = re.findall(rb'<([0-9A-Fa-f]*)>', m.group(5))
                for i, code in enumerate(range(lo, hi + 1)):
                    if i < len(items):
                        cmap[code] = utf16be(items[i])
    return cmap


def utf16be(hx):
    if isinstance(hx, bytes):
        hx = hx.decode()
    if len(hx) % 4 == 0 and len(hx) > 0:
        try:
            return bytes.fromhex(hx).decode('utf-16-be')
        except Exception:
            return ''
    try:
        return chr(int(hx, 16)) if hx else ''
    except Exception:
        return ''


class Font:
    def __init__(self, pdf, num):
        self.num = num
        d = pdf.dict_bytes(num)
        self.two_byte = b'/Type0' in d or b'Identity-H' in d
        self.cmap = {}
        m = re.search(rb'/ToUnicode\s+(\d+)\s+\d+\s+R', d)
        if m:
            self.cmap = parse_tounicode(pdf.stream(int(m.group(1))))
        self.name = b''

    def decode(self, bs):
        out = []
        if self.two_byte:
            for i in range(0, len(bs) - 1, 2):
                code = (bs[i] << 8) | bs[i + 1]
                out.append(self.cmap.get(code, ''))
        else:
            for b in bs:
                out.append(self.cmap.get(b, chr(b) if 32 <= b < 127 else ''))
        return ''.join(out)


NUM = rb'[-+]?[0-9]*\.?[0-9]+'


def tokenize(content):
    """Yield (op, operands) pairs for text-relevant operators."""
    i = 0
    n = len(content)
    operands = []
    while i < n:
        c = content[i:i + 1]
        if c in b' \t\r\n\x00\x0c':
            i += 1
            continue
        if c == b'%':
            j = content.find(b'\n', i)
            i = n if j < 0 else j + 1
            continue
        if c == b'(':
            # literal string
            depth = 1
            j = i + 1
            buf = bytearray()
            while j < n and depth:
                ch = content[j:j + 1]
                if ch == b'\\':
                    nxt = content[j + 1:j + 2]
                    esc = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\b', b'f': b'\f',
                           b'(': b'(', b')': b')', b'\\': b'\\'}
                    if nxt in esc:
                        buf += esc[nxt]
                        j += 2
                        continue
                    mm = re.match(rb'[0-7]{1,3}', content[j + 1:j + 4])
                    if mm:
                        buf.append(int(mm.group(0), 8) & 0xFF)
                        j += 1 + len(mm.group(0))
                        continue
                    buf += nxt
                    j += 2
                    continue
                if ch == b'(':
                    depth += 1
                elif ch == b')':
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                buf += ch
                j += 1
            operands.append(('str', bytes(buf)))
            i = j
            continue
        if c == b'<':
            if content[i + 1:i + 2] == b'<':
                # dict - skip balanced
                depth = 0
                j = i
                while j < n:
                    if content[j:j + 2] == b'<<':
                        depth += 1
                        j += 2
                        continue
                    if content[j:j + 2] == b'>>':
                        depth -= 1
                        j += 2
                        if depth == 0:
                            break
                        continue
                    j += 1
                operands.append(('dict', b''))
                i = j
                continue
            j = content.find(b'>', i)
            if j < 0:
                break
            hx = re.sub(rb'[^0-9A-Fa-f]', b'', content[i + 1:j])
            if len(hx) % 2:
                hx += b'0'
            operands.append(('str', bytes.fromhex(hx.decode())))
            i = j + 1
            continue
        if c == b'[':
            # array of strings/numbers - collect raw then parse
            depth = 1
            j = i + 1
            start = j
            while j < n and depth:
                ch = content[j:j + 1]
                if ch == b'[':
                    depth += 1
                elif ch == b']':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            items = []
            for mm in re.finditer(rb'<([0-9A-Fa-f]*)>|\(((?:\\.|[^\\)])*)\)|(' + NUM + rb')', content[start:j]):
                if mm.group(1) is not None:
                    hx = mm.group(1)
                    if len(hx) % 2:
                        hx += b'0'
                    items.append(('str', bytes.fromhex(hx.decode())))
                elif mm.group(2) is not None:
                    items.append(('str', mm.group(2)))
                else:
                    items.append(('num', float(mm.group(3))))
            operands.append(('array', items))
            i = j + 1
            continue
        # number / name / operator
        m = re.match(rb'[-+]?[0-9]*\.?[0-9]+', content[i:i + 40])
        if m and m.group(0) not in (b'', b'-', b'+', b'.'):
            operands.append(('num', float(m.group(0))))
            i += len(m.group(0))
            continue
        if c == b'/':
            m = re.match(rb'/[^\s/\[\]<>(){}%]*', content[i:i + 120])
            operands.append(('name', m.group(0)))
            i += len(m.group(0))
            continue
        m = re.match(rb'[A-Za-z\'"*]+', content[i:i + 20])
        if m:
            op = m.group(0)
            yield op, operands
            operands = []
            i += len(op)
            continue
        i += 1


def extract_glyphs(pdf, pnum):
    """Yield (x, y, text, size) glyph runs in page text space."""
    fonts = {k: Font(pdf, v) for k, v in pdf.page_fonts(pnum).items()}
    content = pdf.page_content(pnum)
    out = []
    if not content:
        return out
    # text state
    tm = [1, 0, 0, 1, 0, 0]
    tlm = [1, 0, 0, 1, 0, 0]
    cur = None
    size = 12
    leading = 0

    def mul(m, n):
        a, b, c, d, e, f = m
        A, B, C, D, E, F = n
        return [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D,
                e * A + f * C + E, e * B + f * D + F]

    for op, ops in tokenize(content):
        if op == b'BT':
            tm = [1, 0, 0, 1, 0, 0]
            tlm = [1, 0, 0, 1, 0, 0]
        elif op == b'Tf':
            names = [o for t in ops if t[0] == 'name' for o in [t[1]]]
            nums = [t[1] for t in ops if t[0] == 'num']
            if names:
                key = names[-1][1:]
                cur = fonts.get(key)
            if nums:
                size = nums[-1]
        elif op == b'Tm':
            nums = [t[1] for t in ops if t[0] == 'num']
            if len(nums) >= 6:
                tm = nums[-6:]
                tlm = list(tm)
        elif op in (b'Td', b'TD'):
            nums = [t[1] for t in ops if t[0] == 'num']
            if len(nums) >= 2:
                tx, ty = nums[-2], nums[-1]
                tlm = mul([1, 0, 0, 1, tx, ty], tlm)
                tm = list(tlm)
                if op == b'TD':
                    leading = -ty
        elif op == b'T*':
            tlm = mul([1, 0, 0, 1, 0, -leading], tlm)
            tm = list(tlm)
        elif op == b'TL':
            nums = [t[1] for t in ops if t[0] == 'num']
            if nums:
                leading = nums[-1]
        elif op in (b'Tj', b"'", b'"'):
            if op in (b"'", b'"'):
                tlm = mul([1, 0, 0, 1, 0, -leading], tlm)
                tm = list(tlm)
            strs = [t[1] for t in ops if t[0] == 'str']
            if strs and cur:
                txt = cur.decode(strs[-1])
                if txt:
                    out.append((tm[4], tm[5], txt, size))
                # advance approximate
                tm = mul([1, 0, 0, 1, len(txt) * size * 0.5, 0], tm)
        elif op == b'TJ':
            arrs = [t[1] for t in ops if t[0] == 'array']
            if arrs and cur:
                for kind, val in arrs[-1]:
                    if kind == 'str':
                        txt = cur.decode(val)
                        if txt:
                            out.append((tm[4], tm[5], txt, size))
                            tm = mul([1, 0, 0, 1, len(txt) * size * 0.5, 0], tm)
                    else:
                        tm = mul([1, 0, 0, 1, -val / 1000.0 * size, 0], tm)
    return out


def page_lines(pdf, pnum, ytol=3.0):
    glyphs = extract_glyphs(pdf, pnum)
    # group by y
    glyphs.sort(key=lambda g: (g[1], g[0]))
    lines = []
    for x, y, t, s in glyphs:
        placed = False
        for ln in lines:
            if abs(ln['y'] - y) <= ytol:
                ln['chars'].append((x, t))
                placed = True
                break
        if not placed:
            lines.append({'y': y, 'chars': [(x, t)]})
    for ln in lines:
        ln['chars'].sort(key=lambda c: c[0])
        # insert a space when the x gap is large
        parts = []
        prev_end = None
        for x, t in ln['chars']:
            if prev_end is not None and x - prev_end > 6 and parts and not parts[-1].endswith(' '):
                parts.append(' ')
            parts.append(t)
            prev_end = x + 12
        ln['text'] = ''.join(parts)
    lines.sort(key=lambda l: l['y'])
    return [(l['y'], l['text']) for l in lines]