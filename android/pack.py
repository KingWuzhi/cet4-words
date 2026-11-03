# -*- coding: utf-8 -*-
"""把 aapt2 产出的 base.apk + classes.dex + assets 合成一个 APK。"""
import os
import sys
import zipfile


def main():
    base, dex, assets_dir, out = sys.argv[1:5]
    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.write(dex, "classes.dex")
        for root, _dirs, files in os.walk(assets_dir):
            for name in files:
                p = os.path.join(root, name)
                arc = os.path.relpath(p, assets_dir).replace("\\", "/")
                zout.write(p, "assets/" + arc)
    print("packed", out, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    main()