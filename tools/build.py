# -*- coding: utf-8 -*-
"""Inject the parsed word data into the HTML template.

用法（在仓库根目录）：python tools/build.py
输出：index.html（仓库根目录）与 android/assets/index.html（APK 打包用）
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data', 'words.json')
TPL = os.path.join(BASE, 'build', 'app.template.html')
OUT = os.path.join(BASE, 'index.html')

with open(DATA, encoding='utf-8') as f:
    words = json.load(f)

groups = []
for key, items in words.items():
    unit, lesson = key.split()
    u = int(unit[1:])
    l = int(lesson[1:])
    groups.append({'u': u, 'l': l, 'v': [[w['w'], w['m']] for w in items]})
groups.sort(key=lambda g: (g['u'], g['l']))

payload = json.dumps(groups, ensure_ascii=False, separators=(',', ':'))
with open(TPL, encoding='utf-8') as f:
    tpl = f.read()
html = tpl.replace('__DATA__', payload)
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(html)

# 本地习惯用的文件名，同时写一份（仓库里只保留 index.html）
LOCAL_COPY = os.path.join(BASE, '四级单词背诵系统.html')
if os.path.abspath(LOCAL_COPY) != os.path.abspath(OUT):
    with open(LOCAL_COPY, 'w', encoding='utf-8') as f:
        f.write(html)

# 同步一份到安卓工程 assets，供 APK 打包使用
ANDROID_ASSETS = os.path.join(BASE, 'android', 'assets')
if os.path.isdir(os.path.join(BASE, 'android')):
    os.makedirs(ANDROID_ASSETS, exist_ok=True)
    with open(os.path.join(ANDROID_ASSETS, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)

total = sum(len(g['v']) for g in groups)
print('lessons', len(groups), 'words', total, 'html bytes', len(html.encode('utf-8')))
print('out', OUT)