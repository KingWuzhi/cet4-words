# -*- coding: utf-8 -*-
"""Parse the CET4 word-list PDFs into JSON: unit/lesson + word/meaning pairs."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdftext

CJK = re.compile(r'[\u4e00-\u9fff]')
IDX = re.compile(r'^\d{1,3}$')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 词表 PDF 所在目录：默认 ../pdfs，可用环境变量 CET4_PDF_DIR 指定
SRC = os.environ.get('CET4_PDF_DIR', os.path.join(BASE, 'pdfs'))
OUT = os.path.join(BASE, 'data', 'words.json')


def page_index_columns(runs):
    """Return sorted list of x positions that start an index column."""
    if not runs:
        return []
    ytop = min(r[1] for r in runs)
    cand = [r for r in runs if IDX.match(r[2].strip()) and r[1] > ytop + 55]
    if not cand:
        return []
    xs = sorted(r[0] for r in cand)
    groups = [[xs[0]]]
    for x in xs[1:]:
        if x - groups[-1][-1] > 60:
            groups.append([x])
        else:
            groups[-1].append(x)
    groups = [g for g in groups if len(g) >= 5]
    return [min(g) for g in groups]


def header_cut(runs):
    """Column boundary from the table header row (序号 单词 释义 ...)."""
    rows = {}
    for x, y, t, s in runs:
        if t.strip() in ('序号', '单词', '释义'):
            rows.setdefault(round(y), []).append((x, t.strip()))
    for y, cells in sorted(rows.items()):
        cells.sort()
        seq = [i for i, c in enumerate(cells) if c[1] == '序号']
        if len(seq) < 2:
            continue
        i2 = seq[1]
        left = cells[:i2]
        if not left:
            continue
        cut = (left[-1][0] + cells[i2][0]) / 2.0
        if cut > left[0][0] + 100:
            return cut
    return None


def split_columns(runs):
    """Split page runs into left/right column lists."""
    hc = header_cut(runs)
    if hc:
        return [[r for r in runs if r[0] < hc], [r for r in runs if r[0] >= hc]]
    cols = page_index_columns(runs)
    if len(cols) < 2:
        return [runs]
    lo, hi = cols[0], cols[-1]
    best_cut, best_score = (lo + hi) / 2.0, -1e9
    for c in [(lo + hi) / 2.0, hi - 100, lo + 180]:
        left = [r for r in runs if r[0] < c]
        right = [r for r in runs if r[0] >= c]
        le = column_entries(left)
        re_ = column_entries(right)
        score = 0.0
        for e in le + re_:
            score += 2 if (e['w'] and e['m']) else -1.5
        score -= abs(len(le) - len(re_)) * 0.35
        if score > best_score:
            best_cut, best_score = c, score
    return [[r for r in runs if r[0] < best_cut], [r for r in runs if r[0] >= best_cut]]


def runs_of_page(pdf, pnum):
    return pdftext.extract_glyphs(pdf, pnum)


def column_entries(runs):
    """Group a column's runs into entries: {no, word, meaning}."""
    if not runs:
        return []
    ytop = min(r[1] for r in runs)
    idx = [r for r in runs if IDX.match(r[2].strip()) and r[1] > ytop + 55]
    if not idx:
        return []
    ix = min(r[0] for r in idx)
    order = sorted(idx, key=lambda r: (r[1], r[0]))
    # merge index runs sharing the same y
    anchors = []
    for r in order:
        if anchors and abs(anchors[-1][1] - r[1]) <= 3:
            continue
        anchors.append((r[2].strip(), r[1], r[0]))

    ents = [{'no': a[0], 'y': a[1], 'words': [], 'meaning': []} for a in anchors]

    def nearest(y):
        best, bd = None, 1e9
        for e in ents:
            d = abs(e['y'] - y)
            if d < bd:
                best, bd = e, d
        return (best, bd) if bd <= 22 else (None, bd)

    for x, y, t, s in runs:
        tt = t.strip()
        if not tt:
            continue
        if IDX.match(tt) and abs(x - ix) < 30:
            continue
        e, d = nearest(y)
        if e is None:
            continue
        if CJK.search(tt) or re.match(r'^(n|v|adj|adv|prep|conj|pron|num|art|int|aux|vt|vi)\.', tt):
            e['meaning'].append((y, x, tt))
        else:
            e['words'].append((y, x, tt))

    out = []
    for e in sorted(ents, key=lambda e: int(e['no'])):
        word = ' '.join(p[2] for p in sorted(e['words'], key=lambda p: (p[0], p[1]))).strip()
        mean = ' '.join(p[2] for p in sorted(e['meaning'], key=lambda p: (p[0], p[1]))).strip()
        word = re.sub(r'\s+', ' ', word)
        mean = re.sub(r'\s+', ' ', mean)
        out.append({'no': int(e['no']), 'w': word, 'm': mean})
    return out


def parse_file(path):
    pdf = pdftext.PDF(path)
    words = []
    for pnum in pdf.pages():
        runs = runs_of_page(pdf, pnum)
        if not runs:
            continue
        for col in split_columns(runs):
            words.extend(column_entries(col))
    # dedupe by index no keeping first non-empty
    seen = {}
    for w in words:
        if not w['w']:
            continue
        if w['no'] in seen:
            if not seen[w['no']]['m'] and w['m']:
                seen[w['no']] = w
            continue
        seen[w['no']] = w
    return [seen[k] for k in sorted(seen)]


def main():
    files = sorted(os.listdir(SRC))
    data = {}
    report = []
    for fn in files:
        if not fn.lower().endswith('.pdf'):
            continue
        m = re.match(r'U(\d+)\s*L(\d+)', fn, re.I)
        if not m:
            print('skip', fn)
            continue
        unit, lesson = int(m.group(1)), int(m.group(2))
        key = 'U%d L%d' % (unit, lesson)
        ws = parse_file(os.path.join(SRC, fn))
        clean = []
        for w in ws:
            word = w['w'].strip()
            if not word or not w['m']:
                report.append('%s #%s missing (%r / %r)' % (key, w['no'], word, w['m']))
                continue
            if not re.search(r'[A-Za-z]', word):
                report.append('%s #%s no latin (%r)' % (key, w['no'], word))
                continue
            clean.append({'w': word, 'm': w['m']})
        data[key] = clean
        print('%-8s %3d words  (%s)' % (key, len(clean), fn))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print('\ntotal', sum(len(v) for v in data.values()), 'words in', len(data), 'lessons')
    print('\n'.join(report))


if __name__ == '__main__':
    main()