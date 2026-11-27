"""도시 공방(city-atelier) 첫 화면의 지구본 모형 셋 — 판타지 · 스팀펑크 · 사이버펑크.

세계 하나를 짓고(구 `Globe` + 돌지 않는 받침 `Stand`), glb 로 내보내고, 검사하고, 정지 그림을 뽑는다.

실행:
    ~/blender/blender -b -P scenes/city_globe.py -- --world fantasy
    ~/blender/blender -b -P scenes/city_globe.py -- --world all          # 셋 + 비교판
    # --no-render(내보내기 · 검사만) · --samples N · --board-only(있는 정지 그림으로 비교판만)

좌표 약속(웹 lib/globe-math.js 와 같다 — 어기면 도시 점이 엉뚱한 나라 위에 뜬다):
    웹(three, Y-up)  latLonToVec3(lat, lon) = [cos(lat)·sin(lon), sin(lat), cos(lat)·cos(lon)]
    glTF +Y Up 내보내기는 three (x, y, z) = Blender (x, z, −y) 이므로 Blender 안에서는
    x = cos(lat)·sin(lon),  y = −cos(lat)·cos(lon),  z = sin(lat)      (경도 0 = −Y · 동경 90 = +X · 북극 = +Z)
    UV 는 꼭짓점 위치에서 바로:  u = (lon + 180) / 360,  v = (lat + 90) / 180   (정방형 도법 — 왼쪽 끝 경도 −180, 위 끝 북위 90)
    웹은 매 프레임 Globe.rotation 을 덮어쓴다 → Globe 노드는 회전 0 · 크기 1 · 위치 0, 방향은 메시 데이터에.

결과(작업 폴더 renders/city-globe/ — 저장소에 올리지 않는다):
    <세계>.glb · <세계>-still.webp · compare.webp · tex/<세계>-*.png · report.json
    scenes/city_globe_<세계>.blend
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tarfile
import time

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "renders", "city-globe")
CFG = json.load(open(os.path.join(HERE, "city_globe.json"), encoding="utf-8"))
WORLDS = ("fantasy", "steampunk", "cyberpunk")
LAND_URLS = ("https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-50m.json",
             "https://registry.npmjs.org/world-atlas/-/world-atlas-2.0.2.tgz")   # 같은 파일(꾸러미 안 land-50m.json) — CDN 이 막힌 곳에서

sys.path.insert(0, HERE)
import city_globe_check  # noqa: E402  (검사 — 내보낸 뒤 바로 돌린다)


def log(*a):
    print("[city_globe]", *a, flush=True)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="city_globe.py")
    p.add_argument("--world", default="all", choices=WORLDS + ("all",))
    p.add_argument("--no-render", action="store_true", help="내보내기 · 검사만")
    p.add_argument("--board-only", action="store_true", help="있는 정지 그림으로 비교판만 다시")
    p.add_argument("--samples", type=int, default=CFG["still"]["samples"])
    return p.parse_args(argv)


# ──────────────────────────────────────────────────────────────────────────
# 색
# ──────────────────────────────────────────────────────────────────────────
def hex3(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)


def to_lin(c):
    c = np.asarray(c, dtype=np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    c = np.clip(np.asarray(c, dtype=np.float32), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def lin4(h, gain=1.0):
    return (*np.clip(to_lin(hex3(h)) * gain, 0, 1).tolist(), 1.0)


# ──────────────────────────────────────────────────────────────────────────
# 땅 — world-atlas land-50m (TopoJSON 을 직접 푼다)
# ──────────────────────────────────────────────────────────────────────────
def land_file():
    d = os.path.join(OUT, "data")
    f = os.path.join(d, "land-50m.json")
    if os.path.isfile(f) and os.path.getsize(f) > 100000:
        return f
    os.makedirs(d, exist_ok=True)
    for url in LAND_URLS:
        tmp = os.path.join(d, os.path.basename(url))
        r = subprocess.run(["curl", "-fsSL", "--max-time", "120", "-o", tmp, url], capture_output=True, text=True)
        if r.returncode != 0:
            log("못 받음", url, r.stderr.strip()[:120])
            continue
        if url.endswith(".tgz"):
            with tarfile.open(tmp) as t:
                src = t.extractfile("package/land-50m.json")
                open(f, "wb").write(src.read())
        else:
            os.replace(tmp, f)
        return f
    raise SystemExit("land-50m.json 을 못 받았다 — renders/city-globe/data/land-50m.json 에 직접 넣는다")


def load_land():
    """→ 고리 목록(각각 (N, 2) [경도, 위도], 닫힌 꼴). 구멍도 고리 하나 — 칠은 짝홀 규칙이라 따로 가르지 않는다."""
    topo = json.load(open(land_file(), encoding="utf-8"))
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for a in topo["arcs"]:                       # 델타 복원 + transform
        q = np.cumsum(np.asarray(a, dtype=np.int64), axis=0).astype(np.float64)
        arcs.append(np.stack([q[:, 0] * sx + tx, q[:, 1] * sy + ty], axis=1))
    rings = []

    def ring(ids):
        pts = []
        for i in ids:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.append(a if not pts else a[1:])   # 이음 점은 한 번만
        return np.concatenate(pts)

    def walk(g):
        if g["type"] == "GeometryCollection":
            for x in g["geometries"]:
                walk(x)
        elif g["type"] == "Polygon":
            rings.extend(ring(r) for r in g["arcs"])
        elif g["type"] == "MultiPolygon":
            for poly in g["arcs"]:
                rings.extend(ring(r) for r in poly)

    walk(topo["objects"]["land"])
    return rings


def raster_land(rings, W, H):
    """정방형 도법 W×H 에 땅을 칠한다(짝홀 주사선). 행 0 = 북위 90, 열 0 = 경도 −180. → bool (H, W), 날짜변경선을 넘는 고리 수, 극을 도는 고리 수

    world-atlas 는 경도 −180 과 180 을 한 점으로 꿰어 둔다 — 추코트카(유라시아) · 피지 · 남극의 고리는 날짜변경선을 넘어가고,
    남극 고리는 극을 한 바퀴 돈다(−90 을 따라 닫는 밑변이 없다). 그대로 칠하면 가로로 번진다. 그래서:
      ① 고리마다 경도를 이어 붙인다(이웃 점의 차를 ±180 안으로 — 넘어가면 ±360 을 더해 계속 간다)
      ② 한 바퀴(±360) 돌아 닫히는 고리는 극을 거쳐 닫는다
      ③ 세 배 폭 화폭에 칠한 뒤 W 씩 접어 겹친다(짝홀)"""
    x0s, y0s, x1s, y1s = [], [], [], []
    crossing = polar = 0
    for r in rings:
        lon, lat = r[:, 0], r[:, 1]
        if not (abs((lon[0] - lon[-1] + 180.0) % 360.0 - 180.0) < 1e-9 and lat[0] == lat[-1]):   # 안 닫힌 고리면 닫는다
            lon, lat = np.append(lon, lon[0]), np.append(lat, lat[0])
        d = (np.diff(lon) + 180.0) % 360.0 - 180.0
        crossing += int((np.abs(np.diff(lon)) > 180.0).any())
        lonu = lon[0] + np.concatenate([[0.0], np.cumsum(d)])
        turn = lonu[-1] - lonu[0]
        if abs(turn) > 180.0:                                  # 극을 도는 고리 — 극으로 내려가(올라가) 닫는다
            polar += 1
            pole = -90.0 if lat.mean() < 0 else 90.0
            lonu = np.concatenate([lonu, [lonu[-1], lonu[0], lonu[0]]])
            lat = np.concatenate([lat, [pole, pole, lat[0]]])
        x = (lonu + 180.0) / 360.0 * W
        y = (90.0 - lat) / 180.0 * H
        x0s.append(x[:-1]); y0s.append(y[:-1]); x1s.append(x[1:]); y1s.append(y[1:])
    x0, y0, x1, y1 = (np.concatenate(a) for a in (x0s, y0s, x1s, y1s))
    keep = np.abs(y1 - y0) > 1e-12
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    lo, hi = np.minimum(y0, y1), np.maximum(y0, y1)
    j0 = np.ceil(lo - 0.5).astype(np.int64)        # 행 가운데 yc = j + 0.5 가 [lo, hi) 안
    j1 = np.ceil(hi - 0.5).astype(np.int64)
    n = np.clip(j1 - j0, 0, None)
    idx = np.repeat(np.arange(len(n)), n)
    first = np.repeat(np.cumsum(n) - n, n)
    j = np.repeat(j0, n) + (np.arange(len(idx)) - first)
    ok = (j >= 0) & (j < H)
    idx, j = idx[ok], j[ok]
    yc = j + 0.5
    xc = x0[idx] + (yc - y0[idx]) * (x1[idx] - x0[idx]) / (y1[idx] - y0[idx])
    c = np.ceil(xc - 0.5).astype(np.int64) + W     # 화폭은 경도 −540 ~ 540(세 배 폭)
    assert c.min() >= 0 and c.max() <= 3 * W, "이어 붙인 경도가 세 배 폭을 넘는다"
    tog = np.zeros((H, 3 * W + 1), dtype=np.int32)
    np.add.at(tog, (j, c), 1)
    par = (np.cumsum(tog, axis=1)[:, :3 * W] & 1).astype(bool)
    mask = par[:, :W] ^ par[:, W:2 * W] ^ par[:, 2 * W:]
    return mask, crossing, polar


def down(a, s):
    """s×s 묶어 평균 — (H·s, W·s) → (H, W) float32"""
    H, W = a.shape[0] // s, a.shape[1] // s
    return a.reshape(H, s, W, s).mean(axis=(1, 3), dtype=np.float32)


def lat_rows(H):
    return 90.0 - (np.arange(H) + 0.5) / H * 180.0


def _kernel(sigma):
    r = max(1, int(math.ceil(sigma * 3)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2).astype(np.float32)
    return r, k / k.sum()


def blur(a, sigma, by_lat=False):
    """가우시안 — 가로는 감기고 세로는 끝을 늘인다. by_lat 이면 가로 폭을 1/cos(위도)만큼 넓힌다(구 위에서 고른 폭 — 몇 단계로 묶어서)."""
    if sigma <= 0:
        return a
    out = np.zeros_like(a)
    if by_lat:
        f = 1.0 / np.maximum(np.cos(np.radians(lat_rows(a.shape[0]))), 0.1)
        level = np.round(np.log2(f) * 2) / 2                 # 2^(0, 0.5, 1 …)
        for lv in np.unique(level):
            rows = np.nonzero(level == lv)[0]
            r, k = _kernel(sigma * 2 ** lv)
            src = a[rows]
            acc = np.zeros_like(src)
            for i, w in enumerate(k):
                acc += w * np.roll(src, i - r, axis=1)
            out[rows] = acc
    else:
        r, k = _kernel(sigma)
        for i, w in enumerate(k):
            out += w * np.roll(a, i - r, axis=1)
    r, k = _kernel(sigma)
    pad = np.concatenate([np.repeat(out[:1], r, axis=0), out, np.repeat(out[-1:], r, axis=0)], axis=0)
    res = np.zeros_like(a)
    for i, w in enumerate(k):
        res += w * pad[i:i + a.shape[0]]
    return res


def graticule(W, H, px, step=10.0, dash=None):
    """경위선(d3.geoGraticule10 과 같은 자리 — 10도마다, 작은 경선은 ±80 까지, 90도 경선만 극까지). → 0..1 (H, W)
    굵기 px 는 구 위에서 고르게(경선은 위도에 따라 화소 폭이 넓어진다). dash = (주기 도, 켜진 몫) 이면 점선."""
    lat = lat_rows(H)[:, None]
    lon = ((np.arange(W) + 0.5) / W * 360.0 - 180.0)[None, :]
    deg = 360.0 / W
    cosl = np.maximum(np.cos(np.radians(lat)), 1e-3)

    def near(v, s):
        return np.abs((v + s / 2) % s - s / 2)

    m_minor = np.clip(px / 2 + 0.5 - near(lon, step) * cosl / deg, 0, 1) * (np.abs(lat) <= 80.0)
    m_major = np.clip(px / 2 + 0.5 - near(lon, 90.0) * cosl / deg, 0, 1)
    par = np.clip(px / 2 + 0.5 - near(lat, step) / deg, 0, 1) * (np.abs(lat) < 85.0) * np.ones_like(lon)
    mer = np.maximum(m_minor, m_major)
    if dash:
        period, on = dash
        mer = mer * ((lat % period) < period * on)
        par = par * (((lon * cosl) % period) < period * on)
    return np.maximum(mer, par).astype(np.float32)


def sphere_noise(W, H, freq, seed, octaves=4):
    """구 위에서 이음매 없는 fBm 값 잡음(3D 격자) → 0..1 (H, W)"""
    lat = np.radians(lat_rows(H))[:, None]
    lon = np.radians((np.arange(W) + 0.5) / W * 360.0 - 180.0)[None, :]
    P = np.stack([np.cos(lat) * np.sin(lon), -np.cos(lat) * np.cos(lon), np.sin(lat) * np.ones_like(lon)], axis=-1).astype(np.float32)

    def h(i, j, k, s):
        n = (i.astype(np.uint32) * np.uint32(374761393) + j.astype(np.uint32) * np.uint32(668265263)
             + k.astype(np.uint32) * np.uint32(2246822519) + np.uint32(s) * np.uint32(3266489917))
        n = (n ^ (n >> np.uint32(13))) * np.uint32(1274126177)
        n = n ^ (n >> np.uint32(16))
        return n.astype(np.float32) / np.float32(4294967295.0)

    total = np.zeros((H, W), dtype=np.float32)
    amp, norm = 1.0, 0.0
    with np.errstate(over="ignore"):
        for o in range(octaves):
            Q = (P + 2.0) * (freq * 2 ** o)
            I = np.floor(Q).astype(np.int64)
            F = Q - I
            F = F * F * (3 - 2 * F)
            v = 0
            for dx in (0, 1):
                for dy in (0, 1):
                    for dz in (0, 1):
                        w = ((F[..., 0] if dx else 1 - F[..., 0]) * (F[..., 1] if dy else 1 - F[..., 1]) * (F[..., 2] if dz else 1 - F[..., 2]))
                        v = v + w * h(I[..., 0] + dx, I[..., 1] + dy, I[..., 2] + dz, seed + o)
            total += amp * v
            norm += amp
            amp *= 0.5
    return total / norm


def smooth(a, lo, hi):
    t = np.clip((a - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def mix(a, b, t):
    return a * (1 - t[..., None]) + b * t[..., None]


def over(base_lin, hexcolor, alpha):
    """선을 화면값(sRGB)에서 얹는다 — CSS 의 반투명 선(rgb(… / .3))과 같은 셈. 선형에서 섞으면 어두운 바다 위의 옅은 선이 훨씬 밝아진다."""
    return to_lin(mix(to_srgb(base_lin), hex3(hexcolor)[None, None] * np.ones_like(base_lin), alpha))


def build_textures(world, land_ss):
    """→ {'base': sRGB (H, W, 3), 'orm': (h, w, 3) 선형, 'normal': 선형 | 없음, 'emit': sRGB | 없음}, 땅 몫 (H, W)"""
    T, w = CFG["texture"], CFG["worlds"][world]
    W, H, S = T["width"], T["height"], T["supersample"]
    land = down(land_ss.astype(np.float32), S)
    # 해안선 — 흐린 땅 몫이 0.5 를 지나는 띠(매끈한 선). 흐림 폭보다 작은 섬은 옅은 점으로 남는다. 가로는 이음매를 넘어 감기니 ±180 에 선이 서지 않는다
    soft = blur(land, w["coast_px"] * 0.42, by_lat=True)
    coast = np.clip(1.5 - np.abs(soft - 0.5) / 0.28, 0, 1)
    sea_c, land_c, coast_c, grat_c = (to_lin(hex3(w[k])) for k in ("sea", "land", "coast", "grat"))
    gain = w["gain"]
    n_lo, n_hi = sphere_noise(W, H, 3.0, 11), sphere_noise(W, H, 14.0, 23)
    near = blur(land, 7.0)                              # 뭍 가까이(얕은 바다 · 해안 안쪽)
    emit = normal = None
    rough = land * w["land_rough"] + (1 - land) * w["sea_rough"]
    metal = land * w["land_metal"] + (1 - land) * w["sea_metal"]

    if world == "fantasy":
        grat = graticule(W, H, w["grat_px"])
        sea = sea_c[None, None] * (0.86 + 0.28 * n_lo)[..., None]
        sea = mix(sea, to_lin(hex3("#2f55b0"))[None, None] * np.ones_like(sea), 0.55 * smooth(near, 0.02, 0.5) * (1 - land))   # 뭍 둘레의 얕은 물빛
        sea = over(sea * gain, w["grat"], w["grat_alpha"] * grat) / gain
        gold = land_c[None, None] * (0.84 + 0.3 * n_hi)[..., None]
        gold = mix(gold, to_lin(hex3("#8d6a24"))[None, None] * np.ones_like(gold), 0.4 * smooth(near, 0.75, 1.0))             # 뭍 안쪽은 조금 짙게(지도 그늘)
        base = mix(sea, gold, land)
        base = over(base * gain, w["coast"], coast) / gain
    elif world == "steampunk":
        grat = graticule(W, H, w["grat_px"])
        soot = smooth(n_lo, 0.35, 0.75)
        sea = sea_c[None, None] * (0.82 + 0.4 * n_hi)[..., None] * (1.0 - 0.25 * soot)[..., None]
        sea = over(sea * gain, w["grat"], w["grat_alpha"] * grat) / gain
        brz = land_c[None, None] * (0.8 + 0.4 * n_hi)[..., None] * (1.0 - 0.3 * soot)[..., None]
        pat = smooth(sphere_noise(W, H, 10.0, 41, octaves=3), 0.62, 0.74) * smooth(near, 0.35, 0.9)                              # 녹청은 뭍 안쪽에 작은 얼룩으로
        brz = mix(brz, to_lin(hex3(w["patina"]))[None, None] * (0.75 + 0.4 * n_hi)[..., None], 0.5 * pat)
        base = mix(sea, brz, land)
        lon = ((np.arange(W) + 0.5) / W * 360.0 - 180.0)[None, :]
        lat = lat_rows(H)[:, None]
        dash = (((lon * np.cos(np.radians(lat)) + lat * 1.7) % 1.6) < 1.1).astype(np.float32)                                    # 끊긴 점선 느낌
        base = over(base * gain, w["coast"], coast * dash) / gain
        rough = rough + 0.3 * pat * land + 0.15 * soot
        metal = metal * (1 - 0.8 * pat * land)
    else:  # cyberpunk — 빛나는 선은 emissive 에, 바탕은 어두운 남색
        grat = graticule(W, H, w["grat_px"], dash=(2.0, 0.3))
        tint = to_lin(hex3(w["sea"]) * (1 - w["land_tint"]) + hex3(w["coast"]) * w["land_tint"])
        base = mix(sea_c[None, None] * np.ones((H, W, 3), np.float32), tint[None, None] * np.ones((H, W, 3), np.float32), land)
        lat = lat_rows(H)
        scan = np.clip(1.0 - np.abs(((lat + 1.2) % 2.4) - 1.2) / (180.0 / H) / 0.9, 0, 1)[:, None] * np.ones((1, W), np.float32)  # 가로 주사선(위도 2.4도마다)
        glow = blur(coast, 2.2)
        e = (coast_c[None, None] * (0.05 * land + 0.10 * scan * (1 - coast) + 0.35 * glow)[..., None]
             + grat_c[None, None] * (w["grat_alpha"] * grat * (1 - coast))[..., None])
        e = mix(e, coast_c[None, None] * np.ones_like(e), coast)
        emit = to_srgb(e)

    if w["emboss"] > 0:                                    # 뭍을 살짝 돋운다(노멀맵 — U = 동, V = 북)
        hgt = blur(land, 1.3)
        cosl = np.maximum(np.cos(np.radians(lat_rows(H))), 0.08)[:, None]
        dx = (np.roll(hgt, -1, axis=1) - np.roll(hgt, 1, axis=1)) * 0.5 / cosl
        dy = np.zeros_like(hgt)
        dy[1:-1] = (hgt[2:] - hgt[:-2]) * 0.5              # 행은 남쪽으로 커진다 → ∂h/∂v = −dy
        k = w["emboss"]
        n = np.stack([-k * dx, k * dy, np.ones_like(hgt)], axis=-1)
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        normal = n * 0.5 + 0.5

    base = to_srgb(np.clip(base * gain, 0, 1))
    ow = T["orm_width"]
    s = W // ow
    orm = np.stack([np.ones((H // s, ow), np.float32), down(np.clip(rough, 0.05, 1).astype(np.float32), s), down(np.clip(metal, 0, 1).astype(np.float32), s)], axis=-1)
    return {"base": base, "orm": orm, "normal": normal, "emit": emit}, land


def make_image(name, rgb, colorspace, path):
    """(H, W, 3) 위 행부터 → Blender 이미지(PNG 로 저장해 두고 .blend 에 싼다)"""
    H, W = rgb.shape[:2]
    img = bpy.data.images.new(name, W, H, alpha=False)
    img.colorspace_settings.name = colorspace          # 화소보다 먼저 — 뒤에 바꾸면 Blender 가 그림을 비운다
    px = np.ones((H, W, 4), dtype=np.float32)
    px[..., :3] = np.clip(rgb[::-1], 0, 1)
    img.pixels.foreach_set(px.ravel())
    img.update()
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    img.pack()
    return img


# ──────────────────────────────────────────────────────────────────────────
# 메시 모으기 — 받침 부품은 닫힌 덩어리로 짓고 한 메시로 합친다
# ──────────────────────────────────────────────────────────────────────────
def Rx(a):
    return Matrix.Rotation(math.radians(a), 4, "X")


def Ry(a):
    return Matrix.Rotation(math.radians(a), 4, "Y")


def Rz(a):
    return Matrix.Rotation(math.radians(a), 4, "Z")


def Tr(x, y, z):
    return Matrix.Translation((x, y, z))


Y_AXIS = Rx(-90)        # 부품의 +Z 축을 +Y(화면 안쪽)로 — 정면에서 원으로 보이는 테 · 톱니


class Geo:
    def __init__(self):
        self.v, self.f, self.m = [], [], []

    def add(self, verts, faces, mat, M=None):
        b = len(self.v)
        self.v.extend((tuple(M @ Vector(p)) if M is not None else tuple(p)) for p in verts)
        self.f.extend(tuple(b + i for i in f) for f in faces)
        self.m.extend([mat] * len(faces))

    def lathe(self, profile, mat, seg=48, M=None):
        """(r, z) 옆모습을 Z 축으로 돌린다. 양 끝은 r = 0 이면 꼭짓점 하나, 아니면 뚜껑."""
        verts, faces, rows = [], [], []
        for r, z in profile:
            if r <= 1e-9:
                rows.append([len(verts)]); verts.append((0.0, 0.0, z))
            else:
                rows.append(list(range(len(verts), len(verts) + seg)))
                verts.extend((r * math.cos(2 * math.pi * i / seg), r * math.sin(2 * math.pi * i / seg), z) for i in range(seg))
        for a, b in zip(rows[:-1], rows[1:]):
            for i in range(seg):
                j = (i + 1) % seg
                if len(a) == 1 and len(b) == 1:
                    continue
                if len(a) == 1:
                    faces.append((a[0], b[j], b[i]))
                elif len(b) == 1:
                    faces.append((a[i], a[j], b[0]))
                else:
                    faces.append((a[i], a[j], b[j], b[i]))
        if len(rows[0]) > 1:
            faces.append(tuple(reversed(rows[0])))
        if len(rows[-1]) > 1:
            faces.append(tuple(rows[-1]))
        self.add(verts, faces, mat, M)

    def torus(self, R, r, mat, seg=96, tseg=10, M=None):
        verts, faces = [], []
        for i in range(seg):
            a = 2 * math.pi * i / seg
            for j in range(tseg):
                b = 2 * math.pi * j / tseg
                q = R + r * math.cos(b)
                verts.append((q * math.cos(a), q * math.sin(a), r * math.sin(b)))
        for i in range(seg):
            for j in range(tseg):
                a, b = i * tseg + j, i * tseg + (j + 1) % tseg
                c, d = ((i + 1) % seg) * tseg + (j + 1) % tseg, ((i + 1) % seg) * tseg + j
                faces.append((a, d, c, b))
        self.add(verts, faces, mat, M)

    def gear(self, r_in, r_root, r_tip, teeth, h, mat, M=None, top=0.5):
        """XY 평면의 톱니 고리(두께 h, z ∈ [0, h]). 이 하나에 바깥 점 넷 — SVG 톱니 테와 같은 사다리꼴."""
        n = teeth * 4
        out, inn = [], []
        w = (1 - top) / 2
        for i in range(teeth):
            for f, R in ((0.0, r_root), (w * 0.55, r_tip), (w * 0.55 + top * 0.9, r_tip), (w * 1.1 + top * 0.9, r_root)):
                a = 2 * math.pi * (i + f) / teeth
                out.append((R * math.cos(a), R * math.sin(a)))
                inn.append((r_in * math.cos(a), r_in * math.sin(a)))
        verts = [(x, y, 0.0) for x, y in out] + [(x, y, 0.0) for x, y in inn] + [(x, y, h) for x, y in out] + [(x, y, h) for x, y in inn]
        faces = []
        for i in range(n):
            j = (i + 1) % n
            faces.append((i, j, n + j, n + i))                       # 밑
            faces.append((2 * n + i, 3 * n + i, 3 * n + j, 2 * n + j))  # 위
            faces.append((i, 2 * n + i, 2 * n + j, j))               # 바깥 벽
            faces.append((n + i, n + j, 3 * n + j, 3 * n + i))       # 안 벽
        self.add(verts, faces, mat, M)

    def ball(self, r, mat, seg=12, rings=6, M=None, squash=1.0):
        prof = [(0.0, -r * squash)] + [(r * math.sin(math.pi * k / rings), -r * squash * math.cos(math.pi * k / rings)) for k in range(1, rings)] + [(0.0, r * squash)]
        self.lathe(prof, mat, seg, M)

    def box(self, sx, sy, sz, mat, M=None):
        v = [(x * sx / 2, y * sy / 2, z * sz / 2) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        f = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        self.add(v, f, mat, M)

    def arc_tube(self, pts, radii, mat, tseg=8):
        """점들을 따라가는 굵기가 변하는 관(양 끝은 막는다) — 발톱 · 굽은 버팀대"""
        verts, faces = [], []
        P = [Vector(p) for p in pts]
        for i, p in enumerate(P):
            t = (P[min(i + 1, len(P) - 1)] - P[max(i - 1, 0)]).normalized()
            u = t.cross(Vector((0, 0, 1)))
            u = (t.cross(Vector((1, 0, 0))) if u.length < 1e-4 else u).normalized()
            w = t.cross(u).normalized()
            for j in range(tseg):
                a = 2 * math.pi * j / tseg
                verts.append(tuple(p + (u * math.cos(a) + w * math.sin(a)) * radii[i]))
        for i in range(len(P) - 1):
            for j in range(tseg):
                a, b = i * tseg + j, i * tseg + (j + 1) % tseg
                faces.append((a, b, b + tseg, a + tseg))
        faces.append(tuple(reversed(range(tseg))))
        faces.append(tuple(range((len(P) - 1) * tseg, len(P) * tseg)))
        self.add(verts, faces, mat)

    def to_object(self, name, materials, sharp_deg=32.0):
        me = bpy.data.meshes.new(name)
        me.from_pydata(self.v, [], self.f)
        me.polygons.foreach_set("material_index", self.m)
        me.polygons.foreach_set("use_smooth", [True] * len(self.f))
        for m in materials:
            me.materials.append(m)
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)       # 부품마다 닫힌 덩어리 → 바깥쪽으로
        bm.to_mesh(me)
        bm.free()
        me.set_sharp_from_angle(angle=math.radians(sharp_deg))
        me.update()
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
        return ob


def pbr(name, color, metal=0.0, rough=0.5, emit=None, emit_strength=1.0, gain=1.0):
    m = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    b.inputs["Base Color"].default_value = lin4(color, gain)
    b.inputs["Metallic"].default_value = metal
    b.inputs["Roughness"].default_value = rough
    if emit:
        b.inputs["Emission Color"].default_value = lin4(emit)
        b.inputs["Emission Strength"].default_value = emit_strength
    m.diffuse_color = lin4(color)
    m.use_backface_culling = True                    # glTF doubleSided 끔 — 닫힌 덩어리뿐이다
    return m


# ──────────────────────────────────────────────────────────────────────────
# 구 — UV 는 꼭짓점 위치에서 바로, ±180 이음매와 극은 꼭짓점을 갈라 둔다
# ──────────────────────────────────────────────────────────────────────────
def lonlat_to_blender(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.sin(lo), -math.cos(la) * math.cos(lo), math.sin(la))


def blender_to_uv(p, lon_hint=None):
    """위 식을 거꾸로 — (x, y, z) → (u, v). 이음매(경도 ±180)와 극에서는 경도를 정할 수 없어 lon_hint 를 쓴다."""
    x, y, z = p
    lat = math.degrees(math.asin(max(-1.0, min(1.0, z))))
    lon = math.degrees(math.atan2(x, -y))
    if lon_hint is not None and (abs(abs(lon) - 180.0) < 1e-6 or math.hypot(x, y) < 1e-9):
        lon = lon_hint
    return ((lon + 180.0) / 360.0, (lat + 90.0) / 180.0)


def build_globe(textures, world, texdir):
    S, R = CFG["sphere"]["segments"], CFG["sphere"]["rings"]
    verts, uvs, faces = [], [], []

    def vert(lat, lon):
        p = lonlat_to_blender(lat, lon)
        verts.append(p)
        uvs.append(blender_to_uv(p, lon_hint=lon))
        return len(verts) - 1

    # 안쪽 고리들: 열 S + 1 개(첫 열 경도 −180, 끝 열 +180 — 같은 자리, u 만 0 과 1)
    grid = [[vert(90.0 - 180.0 * r / R, -180.0 + 360.0 * c / S) for c in range(S + 1)] for r in range(1, R)]
    for r in range(R - 2):
        for c in range(S):
            a, b, d, e = grid[r][c], grid[r][c + 1], grid[r + 1][c + 1], grid[r + 1][c]
            faces.append((a, e, d, b))                      # 바깥에서 보아 반시계
    for c in range(S):                                      # 극은 칸마다 꼭짓점 하나(u = 그 칸의 가운데) — 부채꼴 줄무늬가 안 생긴다
        mid = -180.0 + 360.0 * (c + 0.5) / S
        n, s = vert(90.0, mid), vert(-90.0, mid)
        faces.append((n, grid[0][c], grid[0][c + 1]))
        faces.append((s, grid[R - 2][c + 1], grid[R - 2][c]))

    me = bpy.data.meshes.new("Globe")
    me.from_pydata(verts, [], faces)
    me.polygons.foreach_set("use_smooth", [True] * len(faces))
    uv = me.uv_layers.new(name="UVMap")
    for loop in me.loops:
        uv.data[loop.index].uv = uvs[loop.vertex_index]
    me.normals_split_custom_set_from_vertices([Vector(v).normalized() for v in verts])   # 갈라 둔 이음매 · 극에서도 매끈하게(법선 = 위치)
    me.update()

    w = CFG["worlds"][world]
    mat = bpy.data.materials.new("Globe_" + world)
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    mat.use_backface_culling = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")

    def tex(key, colorspace, x, y):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = make_image(f"{world}-{key}", textures[key], colorspace, os.path.join(texdir, f"{world}-{key}.png"))
        n.location = (x, y)
        return n

    nt.links.new(tex("base", "sRGB", -700, 400).outputs["Color"], bsdf.inputs["Base Color"])
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (-380, 100)
    nt.links.new(tex("orm", "Non-Color", -700, 100).outputs["Color"], sep.inputs["Color"])
    nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
    nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
    if textures["normal"] is not None:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.location = (-380, -200)
        nt.links.new(tex("normal", "Non-Color", -700, -200).outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    if textures["emit"] is not None:
        nt.links.new(tex("emit", "sRGB", -700, -500).outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0
    me.materials.append(mat)
    ob = bpy.data.objects.new("Globe", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


# ──────────────────────────────────────────────────────────────────────────
# 받침 — 세계마다. 화면 자리: 위 z ≤ 1.15 · 양옆 |x| ≤ 1.2 · 아래 z ≥ −1.39. 구(반지름 1)를 파고들지 않는다.
# ──────────────────────────────────────────────────────────────────────────
def tilted_ring(squash, roll):
    """수평 고리를 화면 쪽으로 눕혀(앞이 내려간다) 타원으로 보이게 한 뒤, 보는 축(Y)으로 굴린다(SVG rotate(roll) — 오른쪽이 올라간다)."""
    return Ry(roll) @ Rx(math.degrees(math.asin(squash)))


def stand_fantasy(w):
    g = Geo()
    GOLD, DEEP, RUBY = 0, 1, 2
    mats = [pbr("Gold", "#ffd866", metal=0.3, rough=0.4, gain=1.2), pbr("GoldDeep", "#c9962f", metal=0.35, rough=0.48), pbr("Ruby", w["accent2"], metal=0.0, rough=0.16, gain=1.1)]
    cup = [(0.2, -1.012 * math.sqrt(1 - (0.2 / 1.012) ** 2))] + [(r, -math.sqrt(1.012 ** 2 - r * r)) for r in (0.16, 0.12, 0.08, 0.04)] + [(0.0, -1.012)]
    g.lathe([(0.0, -1.39), (0.50, -1.39), (0.52, -1.378), (0.52, -1.358), (0.485, -1.348), (0.45, -1.335), (0.41, -1.33), (0.41, -1.316),
             (0.31, -1.292), (0.215, -1.27), (0.135, -1.243), (0.092, -1.214), (0.074, -1.186), (0.074, -1.176),
             (0.108, -1.168), (0.126, -1.151), (0.108, -1.134), (0.074, -1.126), (0.07, -1.108), (0.098, -1.082), (0.165, -1.058), (0.2, -1.046)] + cup, GOLD, 64)
    g.torus(0.445, 0.012, DEEP, 64, 8, Tr(0, 0, -1.332))                 # 받침대의 짙은 띠
    g.torus(0.092, 0.008, DEEP, 32, 8, Tr(0, 0, -1.108))
    for k in range(4):                                                    # 구를 받치는 굽은 발톱 넷(대각선 — 정면에서 둘이 보인다)
        az = math.radians(45 + 90 * k)
        pts, rad = [], []
        for i in range(9):
            t = i / 8
            ang = math.radians(11 + 24 * t)                               # 아래 극에서 벌어진 각
            rr = 1.03 + 0.012 * math.sin(math.pi * t)
            pts.append((rr * math.sin(ang) * math.cos(az), rr * math.sin(ang) * math.sin(az), -rr * math.cos(ang)))
            rad.append(0.019 * (1 - 0.72 * t))
        g.arc_tube(pts, rad, GOLD)
        g.ball(0.013, RUBY if k in (1, 2) else GOLD, 10, 5, Tr(*pts[-1]))
    g.torus(1.019, 0.012, GOLD, 128, 10, Y_AXIS)                          # 둘레의 금 테(SVG 의 테두리 선) — 정면에서 원
    g.torus(1.1, 0.011, GOLD, 128, 10, tilted_ring(0.22, -14))            # 기운 금 고리(돌지 않는다)
    g.lathe([(0.0, 1.028), (0.02, 1.03), (0.02, 1.04), (0.012, 1.046), (0.0, 1.048)], DEEP, 16)
    g.ball(0.015, RUBY, 12, 6, Tr(0, 0, 1.058))
    g.lathe([(0.0, 1.07), (0.036, 1.106), (0.0, 1.148)], GOLD, 4, Rz(45))  # 꼭대기 마름모(z ≤ 1.15)
    g.ball(0.03, RUBY, 14, 7, Tr(0, -0.122, -1.151) @ Y_AXIS, squash=0.55)  # 받침 옹이의 붉은 보석
    return g, mats


def stand_steampunk(w):
    g = Geo()
    BRASS, IRON, BRONZE, COPPER = 0, 1, 2, 3
    mats = [pbr("Brass", "#e0a868", metal=0.42, rough=0.4, gain=1.1), pbr("Iron", "#4a4038", metal=0.35, rough=0.6),
            pbr("Bronze", "#8d6a42", metal=0.4, rough=0.5), pbr("Copper", w["accent"], metal=0.35, rough=0.42, gain=1.1)]
    PY = 0.07                                                               # 톱니 테 · 기둥이 선 깊이(구의 가운데보다 뒤)
    g.gear(1.0006, 1.056, 1.118, 40, 0.08, BRONZE, Tr(0, PY - 0.04, 0) @ Y_AXIS)   # 톱니 테 — 정면에서 원, 지구본 뒤쪽
    g.torus(1.021, 0.013, BRASS, 128, 10, Y_AXIS)                           # 놋쇠 테
    for k in range(20):                                                     # 테의 리벳
        a = 2 * math.pi * (k + 0.5) / 20
        g.ball(0.0085, BRASS, 8, 4, Tr(1.043 * math.cos(a), PY - 0.045, 1.043 * math.sin(a)) @ Y_AXIS, squash=0.6)
    post = Tr(0, PY, 0)
    g.lathe([(0.0, -1.335), (0.115, -1.335), (0.115, -1.312), (0.08, -1.302), (0.066, -1.285), (0.066, -1.205), (0.088, -1.198), (0.088, -1.178),
             (0.066, -1.171), (0.066, -1.135), (0.1, -1.122), (0.1, -1.09), (0.085, -1.082), (0.0, -1.082)], BRASS, 32, post)
    g.torus(0.089, 0.006, COPPER, 32, 8, post @ Tr(0, 0, -1.188))           # 기둥의 주황 띠
    g.lathe([(0.0, -1.39), (0.47, -1.39), (0.47, -1.36), (0.45, -1.352), (0.36, -1.352), (0.36, -1.338), (0.34, -1.33), (0.0, -1.33)], IRON, 48, post)
    for k in range(14):                                                     # 받침의 리벳
        a = 2 * math.pi * k / 14
        g.ball(0.016, BRASS, 8, 4, post @ Tr(0.405 * math.cos(a), 0.405 * math.sin(a), -1.352), squash=0.6)
    g.box(0.4, 0.028, 0.15, IRON, Tr(0.012, PY - 0.082, -1.226))            # 톱니바퀴를 단 판
    for sx in (-1, 1):
        for sz in (-1, 1):
            g.ball(0.009, BRASS, 8, 4, Tr(0.012 + sx * 0.18, PY - 0.098, -1.226 + sz * 0.058) @ Y_AXIS, squash=0.6)
    for (cx, cz, rr, rt, n, mat, rot) in ((0.092, -1.206, 0.078, 0.1, 12, BRASS, 0.0), (-0.052, -1.246, 0.05, 0.068, 8, COPPER, 22.5)):
        M = Tr(cx, PY - 0.128, cz) @ Y_AXIS @ Rz(rot)
        g.gear(0.022, rr, rt, n, 0.026, mat, M, top=0.42)
        g.lathe([(0.0, -0.004), (0.03, -0.004), (0.03, 0.03), (0.0, 0.03)], IRON, 16, M)   # 축
        g.ball(0.012, BRASS, 8, 4, M @ Tr(0, 0, -0.006), squash=0.6)
    return g, mats


def stand_cyberpunk(w):
    g = Geo()
    CYAN, MAG, DARK = 0, 1, 2
    mats = [pbr("NeonCyan", "#06242b", rough=0.4, emit=w["accent2"], emit_strength=1.0), pbr("NeonMagenta", "#2b0a1e", rough=0.4, emit=w["accent"], emit_strength=1.0),
            pbr("Deck", "#1b1a3c", metal=0.3, rough=0.35, gain=1.2)]
    g.torus(1.012, 0.006, CYAN, 128, 8, Y_AXIS)                             # 구 둘레의 청록 빛 테(SVG 의 테두리 선)
    g.torus(1.1, 0.0075, MAG, 128, 8, tilted_ring(0.2 / 1.1, -12))          # 감싸는 마젠타 빛 고리
    base = Tr(0, 0, -1.268) @ Rx(11)                                       # 떠 있는 받침 고리들 — 화면 쪽으로 눕혀 타원으로 보인다
    g.torus(0.47, 0.012, CYAN, 96, 8, base)
    g.torus(0.365, 0.007, MAG, 96, 8, base)
    g.torus(0.25, 0.005, CYAN, 64, 8, base)
    for k in range(36):                                                     # 바깥 눈금
        a = 360.0 * k / 36
        long_ = k % 3 == 0
        g.box(0.034 if long_ else 0.018, 0.006, 0.006, MAG if long_ else CYAN, base @ Rz(a) @ Tr(0.525 if long_ else 0.517, 0, 0))
    g.lathe([(0.0, -1.39), (0.15, -1.39), (0.17, -1.38), (0.17, -1.37), (0.12, -1.362), (0.0, -1.362)], DARK, 6)   # 빛을 쏘는 바닥 장치
    g.lathe([(0.0, -1.364), (0.07, -1.364), (0.07, -1.356), (0.0, -1.356)], CYAN, 24)
    g.torus(0.118, 0.004, MAG, 6, 6, Tr(0, 0, -1.36))
    return g, mats


STANDS = {"fantasy": stand_fantasy, "steampunk": stand_steampunk, "cyberpunk": stand_cyberpunk}


# ──────────────────────────────────────────────────────────────────────────
# 장면 · 내보내기 · 렌더
# ──────────────────────────────────────────────────────────────────────────
def setup_light_camera(scene, samples):
    """웹의 빛을 흉내 낸다 — 환경맵 없음. 앰비언트(World, 확산에만 · 가림 없음) + 방향광 하나(그림자 없음)."""
    L, S = CFG["light"], CFG["still"]
    world = bpy.data.worlds.new("WebAmbient")
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (1, 1, 1, 1)
    bg.inputs["Strength"].default_value = L["ambient"] / math.pi      # three AmbientLight I → 확산면 밝기 = 바탕색 × I / π
    scene.world = world
    world.cycles_visibility.glossy = False                            # three 의 앰비언트는 반사에 안 잡힌다
    world.cycles_visibility.camera = False
    sun = bpy.data.lights.new("WebSun", "SUN")
    sun.energy = L["sun"]                                             # three DirectionalLight I → 바탕색 × I / π × N·L
    sun.angle = math.radians(0.5)
    sun.use_shadow = False                                            # 웹은 그림자를 안 그린다
    so = bpy.data.objects.new("WebSun", sun)
    so.rotation_euler = Vector(L["sun_from"]).normalized().to_track_quat("Z", "Y").to_euler()   # 빛은 −Z 로 나간다 → +Z 가 빛이 오는 쪽
    scene.collection.objects.link(so)
    cam = bpy.data.cameras.new("WebCamera")
    cam.type = "ORTHO"
    cam.ortho_scale = S["ortho_scale"]
    co = bpy.data.objects.new("WebCamera", cam)
    co.location = (0, -10, S["center_z"])
    co.rotation_euler = (math.radians(90), 0, 0)                      # −Y 쪽에서 +Y 를 본다(웹 카메라와 같다)
    scene.collection.objects.link(co)
    scene.camera = co

    scene.render.engine = "CYCLES"
    c = scene.cycles
    c.device = "CPU"
    c.samples = samples
    c.use_adaptive_sampling = True
    c.max_bounces = c.diffuse_bounces = c.glossy_bounces = c.transmission_bounces = c.volume_bounces = 0   # 웹은 직접광뿐
    try:
        import _cycles
        c.use_denoising = bool(getattr(_cycles, "with_openimagedenoise", False))
    except ImportError:
        c.use_denoising = False
    if c.use_denoising:
        c.denoiser = "OPENIMAGEDENOISE"
    r = scene.render
    r.film_transparent = True
    r.resolution_x = r.resolution_y = S["size"]
    r.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"                   # 웹은 톤매핑 없이 sRGB
    scene.view_settings.look = "None"
    r.image_settings.file_format = "WEBP"
    r.image_settings.color_mode = "RGBA"
    r.image_settings.quality = 92


def export_glb(path, objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.export_scene.gltf(
        filepath=path, export_format="GLB", use_selection=True, export_yup=True, export_apply=True,
        export_cameras=False, export_lights=False, export_animations=False, export_extras=False,
        export_image_format="JPEG", export_jpeg_quality=CFG["texture"]["jpeg_quality"],
        export_draco_mesh_compression_enable=False, export_texcoords=True, export_normals=True, export_tangents=False,
        export_materials="EXPORT")


def tri_count(ob):
    return sum(len(p.vertices) - 2 for p in ob.data.polygons)


def bounds(ob):
    a = np.array([v.co[:] for v in ob.data.vertices])
    return a.min(axis=0), a.max(axis=0)


def build_world(world, args, land_ss, wide):
    t0 = time.perf_counter()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    w = CFG["worlds"][world]
    texdir = os.path.join(OUT, "tex")
    os.makedirs(texdir, exist_ok=True)

    textures, _ = build_textures(world, land_ss)
    globe = build_globe(textures, world, texdir)
    geo, mats = STANDS[world](w)
    stand = geo.to_object("Stand", mats)
    for ob in (globe, stand):
        ob.visible_shadow = False                                     # 웹에는 그림자 · 가림이 없다

    # 자리 검사 — 받침이 화면 여백 안에 있고 구를 파고들지 않는다
    lim = CFG["limits"]
    lo, hi = bounds(stand)
    rmin = float(np.linalg.norm(np.array([v.co[:] for v in stand.data.vertices]), axis=1).min())
    fit = {"x": [round(float(lo[0]), 4), round(float(hi[0]), 4)], "z": [round(float(lo[2]), 4), round(float(hi[2]), 4)], "stand_min_radius": round(rmin, 4)}
    assert hi[2] <= lim["top"] + 1e-6 and lo[2] >= lim["bottom"] - 1e-6 and max(-lo[0], hi[0]) <= lim["side"] + 1e-6, f"{world} 받침이 화면 여백을 넘는다: {fit}"
    assert rmin >= 1.0, f"{world} 받침이 구를 파고든다(가장 가까운 꼭짓점 반지름 {rmin:.4f})"
    tris = {"Globe": tri_count(globe), "Stand": tri_count(stand)}
    assert tris["Globe"] <= lim["globe_tris"] and tris["Stand"] <= lim["stand_tris"], f"삼각형이 많다: {tris}"

    setup_light_camera(scene, args.samples)
    glb = os.path.join(OUT, world + ".glb")
    export_glb(glb, [globe, stand])
    check = city_globe_check.check(glb, world)
    for line in check["lines"]:
        log(line)

    still = None
    if not args.no_render:
        S = CFG["still"]
        # 웹이 그 도시를 가운데 둘 때: three rotation.set(lat, −lon, 0, 'XYZ') = Blender 에서 Rx(lat) · Rz(−lon)
        globe.matrix_world = Rx(S["lat"]) @ Rz(-S["lon"])
        still = os.path.join(OUT, world + "-still.webp")
        scene.render.filepath = still
        t1 = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        log(f"{world} 정지 그림 {time.perf_counter() - t1:.1f}s → {still}")
        globe.matrix_world = Matrix.Identity(4)                       # 파일에는 회전을 남기지 않는다

    blend = os.path.join(HERE, f"city_globe_{world}.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
    rep = {"world": world, "name": w["name"], "glb": glb, "glb_bytes": os.path.getsize(glb), "triangles": tris, "fit": fit,
           "still": still, "blend": blend, "blend_bytes": os.path.getsize(blend), "check": check["ok"], "check_lines": check["lines"],
           "antimeridian": wide, "seconds": round(time.perf_counter() - t0, 1)}
    log(f"{world}: glb {rep['glb_bytes'] / 1e6:.2f}MB · 삼각형 {tris} · 검사 {'통과' if check['ok'] else '실패'} · {rep['seconds']}s")
    return rep


def board():
    """정지 그림 셋을 나란히 — 위 줄은 어두운 화면, 아래 줄은 밝은 화면의 바탕색 위에(한 glb 가 둘 다에 쓰인다)."""
    n = CFG["still"]["size"]
    out = np.zeros((2 * n, 3 * n, 4), dtype=np.float32)
    out[..., 3] = 1
    for i, world in enumerate(WORLDS):
        f = os.path.join(OUT, world + "-still.webp")
        if not os.path.isfile(f):
            return None
        img = bpy.data.images.load(f)
        px = np.empty(n * n * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(n, n, 4)
        bpy.data.images.remove(img)
        for row, tone in enumerate(("light", "dark")):               # Blender 는 아래 행부터 — 밝은 판이 아래
            bg = hex3(CFG["board"][world][tone])
            a = px[..., 3:4]
            out[row * n:(row + 1) * n, i * n:(i + 1) * n, :3] = px[..., :3] * a + bg[None, None] * (1 - a)
    img = bpy.data.images.new("compare", 3 * n, 2 * n, alpha=False)
    img.pixels.foreach_set(out.ravel())
    path = os.path.join(OUT, "compare.webp")
    scene = bpy.context.scene
    scene.render.image_settings.file_format = "WEBP"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.quality = 90
    scene.view_settings.view_transform = "Standard"
    img.save_render(path, scene=scene)
    return path


def main():
    args = parse_args()
    os.makedirs(OUT, exist_ok=True)
    reports = []
    if not args.board_only:
        T = CFG["texture"]
        t0 = time.perf_counter()
        rings = load_land()
        land_ss, crossing, polar = raster_land(rings, T["width"] * T["supersample"], T["height"] * T["supersample"])
        wide = {"crossing_rings": crossing, "polar_rings": polar}
        log(f"땅 고리 {len(rings)}개 → {land_ss.shape[1]}×{land_ss.shape[0]} · 땅 몫 {land_ss.mean() * 100:.1f}%(정방형 화소) · 날짜변경선을 넘는 고리 {crossing}개 · 극을 도는 고리 {polar}개 · {time.perf_counter() - t0:.1f}s")
        for world in (WORLDS if args.world == "all" else (args.world,)):
            reports.append(build_world(world, args, land_ss, wide))
        rp = os.path.join(OUT, "report.json")
        old = json.load(open(rp, encoding="utf-8")) if os.path.isfile(rp) else {}
        old.update({r["world"]: r for r in reports})
        json.dump(old, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if not args.no_render:
        b = board()
        log("비교판 →", b if b else "정지 그림 셋이 다 있어야 만든다")
    if any(not r["check"] for r in reports):
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
