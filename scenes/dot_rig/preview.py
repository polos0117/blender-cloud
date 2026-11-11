"""dot_rig.py 가 부르는 미리보기 만들기 (시스템 파이썬 + Pillow).

시트(PNG)를 칸으로 나눠 2배(nearest)로 키우고 회색 바탕에 올려 무한 반복 WebP 로 쓴다.
compare 가 있으면 왼쪽에 예전 걷기, 오른쪽에 이번 걷기를 한 화면에 나란히 재생한다.
쓴 파일 경로를 한 줄에 하나씩 출력한다.
"""
import json
import math
import os
import sys

from PIL import Image, ImageDraw

GRAY = (128, 128, 128, 255)


def cells(path, n, cw, ch, dy=None):
    sheet = Image.open(path).convert("RGBA")
    out = []
    for i in range(n):
        cell = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        cell.alpha_composite(sheet.crop((i * cw, 0, (i + 1) * cw, ch)), (0, int(round(dy[i])) if dy else 0)) if not dy or dy[i] >= 0 else \
            cell.alpha_composite(sheet.crop((i * cw, -int(round(dy[i])), (i + 1) * cw, ch)), (0, 0))
        out.append(cell)
    return out


def on_gray(cell, zoom):
    big = cell.resize((cell.width * zoom, cell.height * zoom), Image.NEAREST)
    bg = Image.new("RGBA", big.size, GRAY)
    bg.alpha_composite(big)
    return bg


def save_anim(path, frames, ms):
    frames = [f.convert("RGB") for f in frames]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=ms, loop=0, lossless=True, quality=70, method=4)
    print(path)


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    cw, ch = spec["cell"]
    zoom = spec["zoom"]
    name = spec["actor"]

    def out(fname):
        return os.path.join(spec["out"], spec["prefix"] + fname)

    loaded = {}
    for mname, m in spec["motions"].items():
        loaded[mname] = [on_gray(c, zoom) for c in cells(m["sheet"], m["frames"], cw, ch)]
        save_anim(out(f"{name}-{mname}-rig-preview.webp"), loaded[mname], m["ms"])

    cmp_spec = spec.get("compare")
    if cmp_spec and "walk" in loaded:
        old = [on_gray(c, zoom) for c in cells(cmp_spec["file"], cmp_spec["frames"], cw, ch, cmp_spec.get("dy"))]
        tracks = [(old, cmp_spec["ms"], f"before: GPT {cmp_spec['frames']} frames"),
                  (loaded["walk"], spec["motions"]["walk"]["ms"], f"after: rig {spec['motions']['walk']['frames']} frames")]
        total = math.lcm(*(sum(ms) for _, ms, _ in tracks))
        cuts = sorted({0} | {t for _, ms, _ in tracks for k in range(total // sum(ms)) for t in
                             [k * sum(ms) + sum(ms[:i]) for i in range(len(ms))]} | {total})
        gap = 8
        frames, durs = [], []
        for a, b in zip(cuts[:-1], cuts[1:]):
            canvas = Image.new("RGBA", (cw * zoom * 2 + gap, ch * zoom), GRAY)
            for k, (fr, ms, label) in enumerate(tracks):
                t, i = a % sum(ms), 0
                while t >= ms[i]:
                    t -= ms[i]
                    i += 1
                canvas.alpha_composite(fr[i], (k * (cw * zoom + gap), 0))
                ImageDraw.Draw(canvas).text((k * (cw * zoom + gap) + 8, 6), label, fill=(255, 255, 255, 255))
            ImageDraw.Draw(canvas).rectangle((cw * zoom, 0, cw * zoom + gap - 1, ch * zoom), fill=(96, 96, 96, 255))
            frames.append(canvas)
            durs.append(b - a)
        save_anim(out(f"{name}-walk-compare.webp"), frames, durs)


if __name__ == "__main__":
    main()
