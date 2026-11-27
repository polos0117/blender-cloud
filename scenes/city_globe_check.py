"""도시 공방 지구본 glb 검사 — 웹(three.js)과의 좌표 약속이 지켜졌는지 glb 안의 값으로 본다.

실행(파이썬만 — Pillow 가 있으면 그것으로, 없으면 Blender 안에서 돌린다):
    python3 scenes/city_globe_check.py                          # renders/city-globe/ 의 세 세계
    python3 scenes/city_globe_check.py renders/city-globe/fantasy.glb [--world fantasy]
    ~/blender/blender -b -P scenes/city_globe_check.py -- renders/city-globe/fantasy.glb

보는 것:
 1. 노드 Globe · Stand 가 있고 Stand 는 Globe 의 자식이 아니다
 2. Globe 노드의 회전 0 · 크기 1 · 위치 0(웹이 매 프레임 회전을 덮어쓴다)
 3. Globe 꼭짓점이 원점에서 반지름 1 ± 0.02
 4. 파일 ≤ 1.5MB · Draco / meshopt 없음 · 카메라 · 빛 · 애니메이션 없음 · 삼각형 수
 5. 방향 — (0,0,1) 꼭짓점의 u ≈ 0.5(경도 0) · (1,0,0) 은 u ≈ 0.75(동경 90) · 북극 (0,1,0) 은 glTF v ≈ 0
 6. 땅 — 바탕 텍스처에서 서울 · 파리 · 리우는 땅색, 태평양 한가운데 · 남대서양은 바다색
 7. 축 — latLonToVec3(서울) ≈ (0.633, 0.610, −0.477), 그 방향이 지나는 삼각형의 UV 가 식과 같고 거기 화소가 땅
 8. 받침이 화면 여백 안(three 좌표 y ∈ [−1.39, 1.15] · |x| ≤ 1.2)이고 구를 파고들지 않는다
"""

import json
import math
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CFG = json.load(open(os.path.join(HERE, "city_globe.json"), encoding="utf-8"))
LAND = (("서울", 37.57, 126.98), ("파리", 48.86, 2.35), ("리우", -22.91, -43.17))
SEA = (("태평양 한가운데", 0.0, -160.0), ("남대서양", -30.0, -15.0))
SEOUL_VEC = (0.633, 0.610, -0.477)
CTYPE = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def lat_lon_to_vec3(lat, lon):
    """웹 lib/globe-math.js 의 latLonToVec3 그대로(three 좌표 — 경도 0 = +Z, 동경 90 = +X, 북극 = +Y)"""
    p, l = math.radians(lat), math.radians(lon)
    return np.array([math.cos(p) * math.sin(l), math.sin(p), math.cos(p) * math.cos(l)])


def read_glb(path):
    data = open(path, "rb").read()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF" and version == 2 and length == len(data), "glb 머리가 이상하다"
    off, doc, blob = 12, None, b""
    while off < length:
        n, kind = struct.unpack_from("<II", data, off)
        chunk = data[off + 8:off + 8 + n]
        if kind == 0x4E4F534A:
            doc = json.loads(chunk.decode("utf-8"))
        elif kind == 0x004E4942:
            blob = chunk
        off += 8 + n
    return doc, blob, len(data)


def accessor(doc, blob, i):
    a = doc["accessors"][i]
    bv = doc["bufferViews"][a["bufferView"]]
    code, size = CTYPE[a["componentType"]]
    n = NCOMP[a["type"]]
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride", size * n)
    if stride == size * n:
        arr = np.frombuffer(blob, dtype="<" + code, count=a["count"] * n, offset=start)
    else:
        arr = np.array([struct.unpack_from("<" + code * n, blob, start + k * stride) for k in range(a["count"])]).ravel()
    return arr.reshape(a["count"], n) if n > 1 else arr


def decode_image(raw, mime):
    """→ (H, W, 3) uint8, 위 행부터"""
    try:
        import io

        from PIL import Image
        return np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
    except ImportError:
        pass
    try:
        import tempfile

        import bpy
    except ImportError:
        raise SystemExit("텍스처를 풀 수 없다 — Pillow 를 깔거나(pip install pillow) Blender 로 돌린다(blender -b -P scenes/city_globe_check.py -- <glb>)")
    with tempfile.NamedTemporaryFile(suffix=".jpg" if "jpeg" in mime else ".png", delete=False) as f:
        f.write(raw)
    img = bpy.data.images.load(f.name)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    os.unlink(f.name)
    return (px.reshape(h, w, 4)[::-1, :, :3] * 255 + 0.5).astype(np.uint8)


def srgb_ref(hexcolor, gain):
    c = np.array([int(hexcolor.lstrip("#")[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4) * gain
    lin = np.clip(lin, 0, 1)
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055) * 255.0


def sample(img, u, v_gltf):
    """glTF UV(위 왼쪽이 원점) 자리의 3×3 평균 색"""
    H, W = img.shape[:2]
    x, y = int(min(W - 1, max(0, u * W))), int(min(H - 1, max(0, v_gltf * H)))
    ys = [min(H - 1, max(0, y + d)) for d in (-1, 0, 1)]
    xs = [(x + d) % W for d in (-1, 0, 1)]
    return img[np.ix_(ys, xs)].reshape(-1, 3).mean(axis=0)


def ray_uv(P, UV, idx, d):
    """원점에서 d 방향으로 쏜 빛이 지나는 삼각형의 무게중심 UV"""
    tri = idx.reshape(-1, 3)
    a, b, c = P[tri[:, 0]], P[tri[:, 1]], P[tri[:, 2]]
    e1, e2 = b - a, c - a
    h = np.cross(d, e2)
    det = (e1 * h).sum(axis=1)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = -a
    u = (s * h).sum(axis=1) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = (e2 * q).sum(axis=1) * inv
    hit = ok & (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7) & (t > 0)
    k = np.nonzero(hit)[0]
    if not len(k):
        return None
    k = k[0]
    w0 = 1 - u[k] - v[k]
    return w0 * UV[tri[k, 0]] + u[k] * UV[tri[k, 1]] + v[k] * UV[tri[k, 2]]


def check(path, world=None):
    world = world or os.path.basename(path).split(".")[0].split("-")[-1]
    W = CFG["worlds"].get(world)
    lim = CFG["limits"]
    lines, ok = [], True

    def say(passed, text):
        nonlocal ok
        ok = ok and bool(passed)
        lines.append(("통과 " if passed else "실패 ") + text)

    doc, blob, size = read_glb(path)
    nodes = doc.get("nodes", [])
    name = {n.get("name"): i for i, n in enumerate(nodes)}

    # 1
    has = "Globe" in name and "Stand" in name
    kids = set()

    def walk(i):
        for c in nodes[i].get("children", []):
            kids.add(c)
            walk(c)
    if "Globe" in name:
        walk(name["Globe"])
    say(has and name.get("Stand") not in kids, "1 노드 — Globe · Stand 가 있고 Stand 는 Globe 의 자식이 아니다" + ("" if has else f"(있는 노드: {sorted(k for k in name if k)})"))
    if not has:
        return {"ok": False, "lines": lines}
    G, S = nodes[name["Globe"]], nodes[name["Stand"]]

    # 2
    rot, scl, trn = G.get("rotation", [0, 0, 0, 1]), G.get("scale", [1, 1, 1]), G.get("translation", [0, 0, 0])
    unit = np.allclose(rot, [0, 0, 0, 1], atol=1e-6) and np.allclose(scl, 1, atol=1e-6) and np.allclose(trn, 0, atol=1e-6) and "matrix" not in G
    say(unit, f"2 Globe 노드 — 회전 {rot} · 크기 {scl} · 위치 {trn}")

    def mesh_data(node):
        P, UV, I = [], [], []
        base = 0
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            p = accessor(doc, blob, prim["attributes"]["POSITION"]).astype(np.float64)
            uv = accessor(doc, blob, prim["attributes"]["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in prim["attributes"] else np.zeros((len(p), 2))
            i = accessor(doc, blob, prim["indices"]).astype(np.int64) if "indices" in prim else np.arange(len(p))
            P.append(p); UV.append(uv); I.append(i + base)
            base += len(p)
        return np.concatenate(P), np.concatenate(UV), np.concatenate(I)

    GP, GUV, GI = mesh_data(G)
    SP, _, SI = mesh_data(S)

    # 3
    r = np.linalg.norm(GP, axis=1)
    say(r.min() >= 0.98 and r.max() <= 1.02, f"3 Globe 반지름 — {r.min():.4f} ~ {r.max():.4f}(1 ± 0.02), 꼭짓점 {len(GP)}")

    # 4
    ext = doc.get("extensionsUsed", [])
    bad = [e for e in ext if "draco" in e.lower() or "meshopt" in e.lower()]
    gt, st = len(GI) // 3, len(SI) // 3
    say(size <= lim["glb_bytes"] and not bad, f"4 파일 {size / 1e6:.3f}MB(≤ {lim['glb_bytes'] / 1e6:.1f}) · 확장 {ext or '없음'}")
    say(gt <= lim["globe_tris"] and st <= lim["stand_tris"], f"4 삼각형 — Globe {gt}(≤ {lim['globe_tris']}) · Stand {st}(≤ {lim['stand_tris']})")
    extra = [k for k in ("cameras", "animations", "skins") if doc.get(k)] + (["lights"] if "KHR_lights_punctual" in ext else [])
    say(not extra, "4 카메라 · 빛 · 애니메이션 없음" + (f" — 있다: {extra}" if extra else ""))

    # 5
    def nearest(v):
        return int(np.argmin(np.linalg.norm(GP - np.asarray(v, dtype=np.float64), axis=1)))
    u0, u90 = GUV[nearest((0, 0, 1))][0], GUV[nearest((1, 0, 0))][0]
    pole = np.nonzero(GP[:, 1] > 0.9999)[0]
    vN = GUV[pole][:, 1]
    say(abs(u0 - 0.5) < 0.002, f"5 방향 — (0,0,1) 의 u = {u0:.4f}(경도 0 → 0.5)")
    say(abs(u90 - 0.75) < 0.002, f"5 방향 — (1,0,0) 의 u = {u90:.4f}(동경 90 → 0.75)")
    say(len(pole) > 0 and float(vN.max()) < 0.002, f"5 방향 — 북극 (0,1,0) 의 glTF v = {float(vN.max()):.4f}(→ 0), 극 꼭짓점 {len(pole)}개(u {GUV[pole][:, 0].min():.3f} ~ {GUV[pole][:, 0].max():.3f} — 칸마다 따로)")
    seam = np.nonzero((np.abs(GP[:, 0]) < 1e-6) & (GP[:, 2] < -1e-3))[0]          # 경도 ±180 자오선 위
    us = np.round(GUV[seam][:, 0], 4)
    say(len(seam) > 0 and set(np.unique(us).tolist()) <= {0.0, 1.0} and (us == 0).any() and (us == 1).any(), f"5 이음매 — 경도 ±180 꼭짓점 {len(seam)}개의 u 는 0 과 1 로 갈려 있다")

    # 6 · 7 — 바탕 텍스처
    mat = doc["materials"][doc["meshes"][G["mesh"]]["primitives"][0]["material"]]
    tex = doc["textures"][mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]
    im = doc["images"][tex["source"]]
    bv = doc["bufferViews"][im["bufferView"]]
    base = decode_image(blob[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]], im.get("mimeType", ""))
    T = CFG["texture"]
    say(base.shape[1] == T["width"] and base.shape[0] == T["height"], f"6 바탕 텍스처 {base.shape[1]}×{base.shape[0]} {im.get('mimeType')}")
    imgs = [(i.get("mimeType"), doc["bufferViews"][i["bufferView"]]["byteLength"]) for i in doc.get("images", [])]
    lines.append("     텍스처 " + " · ".join(f"{m.split('/')[-1]} {n / 1e3:.0f}KB" for m, n in imgs))
    if W:
        land_ref, sea_ref = srgb_ref(W["land"], W["gain"]), srgb_ref(W["sea"], W["gain"])

        def kind(c):
            return "땅" if np.linalg.norm(c - land_ref) < np.linalg.norm(c - sea_ref) else "바다"
        for want, places in (("땅", LAND), ("바다", SEA)):
            for nm, lat, lon in places:
                c = sample(base, (lon + 180.0) / 360.0, (90.0 - lat) / 180.0)
                say(kind(c) == want, f"6 {want} — {nm}({lat}, {lon}) 화소 {tuple(int(x) for x in c)} → {kind(c)}")
        # 7
        d = lat_lon_to_vec3(LAND[0][1], LAND[0][2])
        say(np.allclose(d, SEOUL_VEC, atol=1e-3), f"7 축 — latLonToVec3(서울) = ({d[0]:.3f}, {d[1]:.3f}, {d[2]:.3f})")
        worst = 0.0
        for nm, lat, lon in LAND + SEA + (("시드니", -33.87, 151.21), ("뉴욕", 40.71, -74.01), ("앵커리지", 61.22, -149.9), ("피지", -17.8, 178.0)):
            uv = ray_uv(GP, GUV, GI, lat_lon_to_vec3(lat, lon))
            if uv is None:
                say(False, f"7 축 — {nm} 방향이 구에 안 닿는다")
                continue
            worst = max(worst, abs(uv[0] - (lon + 180.0) / 360.0), abs(uv[1] - (90.0 - lat) / 180.0))
            if nm == "서울":
                c = sample(base, uv[0], uv[1])
                say(kind(c) == "땅", f"7 축 — 서울 방향이 지나는 삼각형의 UV ({uv[0]:.4f}, {uv[1]:.4f}) 화소 {tuple(int(x) for x in c)} → {kind(c)}")
        say(worst < 0.002, f"7 축 — 도시 아홉 방향의 메시 UV 가 식 u = (lon+180)/360 · v = (90−lat)/180 과 같다(가장 큰 차 {worst:.5f} — 2048 폭에서 {worst * T['width']:.2f}화소)")
    else:
        say(False, f"세계 「{world}」 를 모른다 — --world 로 알려 준다")

    # 8
    lo, hi = SP.min(axis=0), SP.max(axis=0)
    rs = float(np.linalg.norm(SP, axis=1).min())
    say(hi[1] <= lim["top"] + 1e-4 and lo[1] >= lim["bottom"] - 1e-4 and max(-lo[0], hi[0]) <= lim["side"] + 1e-4,
        f"8 받침 자리 — x {lo[0]:.3f} ~ {hi[0]:.3f}(±{lim['side']}) · 위아래 {lo[1]:.3f} ~ {hi[1]:.3f}({lim['bottom']} ~ {lim['top']})")
    say(rs >= 1.0, f"8 받침이 구를 파고들지 않는다 — 가장 가까운 꼭짓점 반지름 {rs:.4f}")
    srot, sscl, strn = S.get("rotation", [0, 0, 0, 1]), S.get("scale", [1, 1, 1]), S.get("translation", [0, 0, 0])
    say(np.allclose(srot, [0, 0, 0, 1], atol=1e-6) and np.allclose(sscl, 1, atol=1e-6) and np.allclose(strn, 0, atol=1e-6), "8 Stand 노드도 회전 0 · 크기 1 · 위치 0")
    lines.insert(0, f"── {os.path.basename(path)} ({world}) — {'모두 통과' if ok else '실패 있음'}")
    return {"ok": ok, "lines": lines, "bytes": size, "triangles": {"Globe": gt, "Stand": st}}


def main(argv):
    world = None
    if "--world" in argv:
        i = argv.index("--world")
        world = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    paths = argv or [os.path.join(REPO, "renders", "city-globe", w + ".glb") for w in CFG["worlds"]]
    bad = 0
    for p in paths:
        if not os.path.isfile(p):
            print("없음", p)
            bad += 1
            continue
        r = check(p, world)
        print("\n".join(r["lines"]))
        bad += 0 if r["ok"] else 1
    return 1 if bad else 0


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ([] if "bpy" in sys.modules else sys.argv[1:])
    sys.exit(main(args))
