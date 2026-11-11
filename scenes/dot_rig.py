"""도트 인물을 부위별로 잘라 뼈대로 움직인다 (종이 인형 방식, headless Blender).

정지 도트 한 장 → 부위 자르기(가려졌던 곳 메우기) → 부위마다 평면 판 + 뼈대 → 동작 →
4배 렌더 → 줄이고 색 맞추기 → 시트 · 미리보기 · 재생 설정 JSON 까지 한 번에 한다.

실행:
    ~/blender/blender -b --python scenes/dot_rig.py -- --actor alice --out renders/

인물마다 scenes/dot_rig/<인물>_parts.json (부위 경계 · 관절 · 뼈대) 만 있으면 되고,
동작은 scenes/dot_rig/motions/*.json 을 함께 쓴다.

좌표: 도트 픽셀은 왼쪽 위가 (0,0). Blender 에서는 X = 오른쪽, Z = 위, 1 단위 = 도트 1픽셀,
발 기준점이 원점이다. 카메라는 정면 정사영.
"""

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zlib

import bpy
import numpy as np
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RIG_DIR = os.path.join(HERE, "dot_rig")


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="dot_rig.py")
    p.add_argument("--actor", required=True, help="인물 이름 (scenes/dot_rig/<이름>_parts.json)")
    p.add_argument("--out", default=os.path.join(REPO, "renders"))
    p.add_argument("--prefix", default="", help="결과 파일 이름 앞에 붙일 글자 (예: 2026-11-11_)")
    p.add_argument("--source", default=None, help="정지 도트 PNG 경로 (없으면 부위 파일의 source 주소에서 받는다)")
    p.add_argument("--scale", type=int, default=4, help="렌더 배율 (기본 4배로 굽고 줄인다)")
    p.add_argument("--reduce", choices=["nearest", "mode"], default="mode",
                   help="줄이는 방식: mode = 4x4 안에서 가장 많은 색(기본, 윤곽이 덜 깨진다), nearest = 가운데 한 점")
    p.add_argument("--parts-only", action="store_true", help="부위 자르기와 확인용 그림까지만")
    p.add_argument("--no-blend", action="store_true")
    return p.parse_args(argv)


def log(msg):
    print(f"[dot_rig] {msg}", flush=True)


# ==========================================================================
# 그림 읽고 쓰기
# ==========================================================================
def read_image(path):
    """PNG → (H, W, 4) uint8, 윗줄이 먼저."""
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    bpy.data.images.remove(img)
    return np.rint(buf.reshape(h, w, 4)[::-1] * 255.0).astype(np.uint8)


def write_png(path, arr):
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape[:2]
    ch = 1 if arr.ndim == 2 else arr.shape[2]
    raw = np.concatenate([np.zeros((h, 1), np.uint8), arr.reshape(h, w * ch)], axis=1).tobytes()

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, {1: 0, 3: 2, 4: 6}[ch], 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def fetch(url, path):
    if not os.path.isfile(path):
        log(f"받는 중: {url}")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "dot-rig"}), timeout=60) as r, open(path, "wb") as f:
            f.write(r.read())
    return path


def hex_rgb(s):
    return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))


# ==========================================================================
# 1. 부위 자르기
# ==========================================================================
def region_mask(spec, shape):
    """runs([첫 줄, 끝 줄, 첫 칸, 끝 칸]) 와 poly(다각형) 로 적은 영역 → 불리언 마스크."""
    h, w = shape
    mask = np.zeros((h, w), dtype=bool)
    for y0, y1, x0, x1 in spec.get("runs", []):
        mask[max(0, y0):y1 + 1, max(0, x0):x1 + 1] = True
    if spec.get("poly"):
        yy, xx = np.mgrid[0:h, 0:w]
        px, py = xx + 0.5, yy + 0.5
        for poly in spec["poly"]:
            inside = np.zeros((h, w), dtype=bool)
            n = len(poly)
            for i in range(n):
                (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
                if ay == by:
                    continue
                cross = ((ay > py) != (by > py)) & (px < (bx - ax) * (py - ay) / (by - ay) + ax)
                inside ^= cross
            mask |= inside
    if spec.get("rows"):
        r0, r1 = spec["rows"]
        mask[:r0] = False
        mask[r1 + 1:] = False
    return mask


def nearest_colors(targets, sources, colors):
    """targets 좌표마다 가장 가까운 sources 좌표의 색."""
    if len(targets) == 0 or len(sources) == 0:
        return np.zeros((len(targets), 3), np.uint8)
    d = ((targets[:, None, :].astype(np.int32) - sources[None, :, :].astype(np.int32)) ** 2).sum(axis=2)
    return colors[np.argmin(d, axis=1)]


def cut_parts(src, actor):
    """정지 도트를 부위별 RGBA 그림으로 나눈다. 가려졌던 곳은 이웃 색으로 메운다(새 색은 만들지 않는다)."""
    h, w = src.shape[:2]
    opaque = src[:, :, 3] >= 128
    rgb = src[:, :, :3]
    lum = rgb.astype(np.float32) @ np.array([0.3, 0.59, 0.11], dtype=np.float32)
    outline_set = [hex_rgb(c) for c in actor.get("outline_colors", [])]
    is_outline = np.zeros((h, w), dtype=bool)
    for c in outline_set:
        is_outline |= (rgb == np.array(c, np.uint8)).all(axis=2)

    parts = actor["parts"]
    if len({p["z"] for p in parts}) != len(parts):
        raise SystemExit("parts 의 z(층)가 겹친다 — 같은 층의 판은 서로 가려서 한쪽이 안 보인다")
    owner = np.full((h, w), -1, dtype=np.int32)
    for i, p in enumerate(parts):                       # 적힌 차례로 임자를 정한다
        m = (opaque & (owner < 0)) if p.get("rest") else (region_mask(p, (h, w)) & opaque & (owner < 0))
        owner[m] = i
    index = {p["name"]: i for i, p in enumerate(parts)}
    owner_z = np.full((h, w), -10**6, dtype=np.int64)   # 그 픽셀 임자가 몇 번째 층인가
    for i, p in enumerate(parts):
        owner_z[owner == i] = p["z"]

    # 가려진 가장자리 잇기(extend): 줄마다 제 픽셀의 끝에서 바깥으로 몇 칸을 같은 색으로 잇고 윤곽선 색으로 닫는다
    base = {}
    for i, p in enumerate(parts):
        own = owner == i
        img = np.zeros((h, w, 4), dtype=np.uint8)
        img[own, :3] = rgb[own]
        img[own, 3] = 255
        ext = np.zeros((h, w), dtype=bool)
        for ex in p.get("extend", []):
            step = -1 if ex.get("dir", "left") == "left" else 1
            colors = [None] * int(ex.get("copy", 2)) + [hex_rgb(c) for c in ex.get("outline", [])]
            for y in range(ex["rows"][0], ex["rows"][1] + 1):
                xs = np.nonzero(own[y])[0]
                if len(xs) == 0:
                    continue
                edge = xs.min() if step < 0 else xs.max()
                for k, col in enumerate(colors, start=1):
                    x = edge + step * k
                    if not (0 <= x < w) or not opaque[y, x] or owner_z[y, x] <= p["z"]:
                        break
                    img[y, x, :3] = rgb[y, edge] if col is None else col
                    img[y, x, 3] = 255
                    ext[y, x] = True
        base[i] = (img, own, ext)

    out, stats = {}, {}
    for i, p in enumerate(parts):
        img, own, ext = base[i]
        img = img.copy()
        shared = np.zeros((h, w), dtype=bool)
        for sh in p.get("share", []):                   # 관절 겹침: 이웃 부위 픽셀(이은 가장자리 포함)을 그대로 빌려 온다
            j = index[sh["from"]]
            m = region_mask(sh, (h, w)) & (base[j][1] | base[j][2]) & ~own & ~ext
            img[m] = base[j][0][m]
            shared |= m
        filled = ext.copy()
        for fl in p.get("fill", []):                    # 나보다 앞 층의 부위에 가려져 있던 곳만 메운다
            m = region_mask(fl, (h, w)) & opaque & ~own & ~shared & ~filled & (owner_z > p["z"])
            if not m.any():
                continue
            ok = own if fl.get("mode", "no_outline") == "any" else (own & ~is_outline)
            tgt = np.argwhere(m)
            img[m, :3] = nearest_colors(tgt, np.argwhere(ok), rgb[ok])
            img[m, 3] = 255
            filled |= m
        total = img[:, :, 3] > 0
        for fl in p.get("fill", []):                    # 메운 곳의 바깥 가장자리에 윤곽선을 다시 둔다
            mode = fl.get("outline")
            if not mode:
                continue
            m = region_mask(fl, (h, w)) & filled & ~ext
            pad = np.pad(total, 1)
            below, left, right, above = pad[2:, 1:-1], pad[1:-1, :-2], pad[1:-1, 2:], pad[:-2, 1:-1]
            edge = {"bottom": ~below, "side": ~left | ~right, "all": ~below | ~left | ~right | ~above}[mode]
            m &= edge
            dark = own & (lum < 95)
            if m.any() and dark.any():
                img[m, :3] = nearest_colors(np.argwhere(m), np.argwhere(dark), rgb[dark])
        out[p["name"]] = img
        stats[p["name"]] = {"own": int(own.sum()), "shared": int(shared.sum()), "filled": int(filled.sum())}
        p["_own"], p["_shared"], p["_filled"] = own, shared, filled
    unowned = int((opaque & (owner < 0)).sum())
    return out, owner, stats, unowned


def debug_sheets(parts_img, actor, src, out_dir):
    """확인용: 부위를 나란히 놓은 그림(메운 곳 = 자홍, 빌려 온 곳 = 청록 표시본 포함)과 임자 지도."""
    h, w = src.shape[:2]
    names = [p["name"] for p in actor["parts"]]
    cols = 5
    rows = math.ceil(len(names) / cols)
    for tag in ("parts", "parts_marked"):
        sheet = np.full((rows * h, cols * w, 3), 128, dtype=np.uint8)
        sheet[::16, :, :] = 110
        for k, p in enumerate(actor["parts"]):
            img = parts_img[p["name"]].copy()
            if tag == "parts_marked":
                img[p["_filled"], :3] = (255, 0, 255)
                img[p["_shared"], :3] = (0, 255, 255)
            r, c = divmod(k, cols)
            cell = sheet[r * h:(r + 1) * h, c * w:(c + 1) * w]
            a = img[:, :, 3] > 0
            cell[a] = img[a, :3]
        write_png(os.path.join(out_dir, f"{tag}.png"), sheet)
    rng = np.random.default_rng(3)
    pal = rng.integers(60, 255, size=(len(names) + 1, 3)).astype(np.uint8)
    own = np.zeros((h, w, 3), dtype=np.uint8) + 40
    for k, p in enumerate(actor["parts"]):
        own[p["_own"]] = pal[k]
    write_png(os.path.join(out_dir, "owner.png"), own)


# ==========================================================================
# 2. 판 · 뼈대
# ==========================================================================
def px_to_world(actor, x, y):
    fx, fy = actor["foot"]
    return (x - fx, fy - y)


def joint_xy(actor, ref):
    return actor["joints"][ref] if isinstance(ref, str) else ref


def bone_order(actor):
    order, seen = [], set()

    def visit(name):
        if name in seen:
            return
        parent = actor["bones"][name].get("parent")
        if parent:
            visit(parent)
        seen.add(name)
        order.append(name)

    for name in actor["bones"]:
        visit(name)
    return order


def build_scene(actor, parts_img, parts_dir, scale):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    w, h = actor["cell"]
    fx, fy = actor["foot"]

    # 뼈대: 관절마다 뼈 하나 (위로 선 짧은 뼈)
    arm_data = bpy.data.armatures.new(f"{actor['actor']}_rig")
    arm = bpy.data.objects.new(f"{actor['actor']}_rig", arm_data)
    scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for name in bone_order(actor):
        spec = actor["bones"][name]
        x, z = px_to_world(actor, *joint_xy(actor, spec["joint"]))
        eb = arm_data.edit_bones.new(name)
        eb.head = (x, 0.0, z)
        eb.tail = (x, 0.0, z + 8.0)
        if spec.get("parent"):
            eb.parent = arm_data.edit_bones[spec["parent"]]
            eb.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")

    # 부위마다 평면 판: 칸 전체 크기, 픽셀 그대로(Closest), 조명 없는 방출 재질
    for p in actor["parts"]:
        name = p["name"]
        path = os.path.join(parts_dir, f"{name}.png")
        write_png(path, parts_img[name])
        img = bpy.data.images.load(path)
        img.pack()
        mat = bpy.data.materials.new(f"part_{name}")
        if bpy.app.version < (5, 0, 0):
            mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.interpolation = "Closest"
        tex.extension = "CLIP"
        em = nt.nodes.new("ShaderNodeEmission")
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        outn = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(tex.outputs["Color"], em.inputs["Color"])
        nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
        nt.links.new(tr.outputs["BSDF"], mix.inputs[1])
        nt.links.new(em.outputs["Emission"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], outn.inputs["Surface"])

        y = -0.05 * p["z"]                               # 앞에 오는 부위일수록 카메라 쪽
        mesh = bpy.data.meshes.new(f"plate_{name}")
        mesh.from_pydata([(-fx, y, fy - h), (w - fx, y, fy - h), (w - fx, y, fy), (-fx, y, fy)], [], [(0, 1, 2, 3)])
        uv = mesh.uv_layers.new(name="UVMap")
        for loop, co in zip(uv.data, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop.uv = co
        mesh.materials.append(mat)
        ob = bpy.data.objects.new(f"plate_{name}", mesh)
        scene.collection.objects.link(ob)
        ob.vertex_groups.new(name=p["bone"]).add([0, 1, 2, 3], 1.0, "REPLACE")
        ob.modifiers.new("Armature", "ARMATURE").object = arm
        ob.parent = arm

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(w, h)
    cam = bpy.data.objects.new("Camera", cam_data)
    cam.location = (w / 2 - fx, -20.0, fy - h / 2)
    cam.rotation_euler = (math.pi / 2, 0.0, 0.0)
    scene.collection.objects.link(cam)
    scene.camera = cam

    r, c = scene.render, scene.cycles
    r.engine = "CYCLES"
    c.device = "CPU"
    c.samples, c.use_adaptive_sampling, c.use_denoising = 1, False, False
    c.pixel_filter_type, c.filter_width = "BOX", 0.01     # 픽셀 가운데 한 점만 찍는다
    c.max_bounces, c.transparent_max_bounces = 0, 64
    r.film_transparent = True
    r.resolution_x, r.resolution_y, r.resolution_percentage = w * scale, h * scale, 100
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGBA"
    r.image_settings.color_depth = "8"
    r.dither_intensity = 0.0
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    return scene, arm


# ==========================================================================
# 3. 동작: 2D 로 풀어서 뼈대에 넣는다
# ==========================================================================
def m_translate(dx, dz):
    return np.array([[1.0, 0.0, dx], [0.0, 1.0, dz], [0.0, 0.0, 1.0]])


def m_rotate(angle, cx, cz):
    c, s = math.cos(angle), math.sin(angle)
    return m_translate(cx, cz) @ np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]) @ m_translate(-cx, -cz)


def apply2(m, pt):
    v = m @ np.array([pt[0], pt[1], 1.0])
    return (float(v[0]), float(v[1]))


def leg_geometry(actor, key):
    """다리 하나의 길이와 발바닥 점(종아리 부위 그림의 맨 아래 세 줄에서 잡는다)."""
    spec = actor["legs"][key]
    shin = next(p for p in actor["parts"] if p["name"] == spec["shin"])
    ys, xs = np.nonzero(shin["_own"])
    bottom = ys.max()
    sel = ys >= bottom - 2
    x0, x1 = xs[sel].min(), xs[sel].max() + 1
    hip = px_to_world(actor, *actor["joints"][spec["hip"]])
    knee = px_to_world(actor, *actor["joints"][spec["knee"]])
    heel = px_to_world(actor, x0, bottom + 1)
    toe = px_to_world(actor, x1, bottom + 1)
    mid = ((heel[0] + toe[0]) / 2, heel[1])
    return {"hip": hip, "knee": knee, "sole": [heel, toe], "mid": mid,
            "l1": math.dist(hip, knee), "l2": math.dist(knee, mid),
            "a1": math.atan2(knee[1] - hip[1], knee[0] - hip[0]), "a2": math.atan2(mid[1] - knee[1], mid[0] - knee[0]),
            "ground": mid[1], "thigh": spec["thigh"], "shin": spec["shin"]}


def solve_leg(geo, hip_new, target, fwd):
    """두 마디 IK. 무릎은 보는 쪽으로 굽는다. 돌려주는 것: 허벅지 · 종아리의 2D 변환."""
    dx, dz = target[0] - hip_new[0], target[1] - hip_new[1]
    l1, l2 = geo["l1"], geo["l2"]
    dist = math.hypot(dx, dz)
    reach = dist <= l1 + l2
    d = min(max(dist, abs(l1 - l2) + 1e-6), l1 + l2)
    bend = math.acos(max(-1.0, min(1.0, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d))))
    a_thigh = math.atan2(dz, dx) + fwd * bend
    knee_new = (hip_new[0] + l1 * math.cos(a_thigh), hip_new[1] + l1 * math.sin(a_thigh))
    if reach:
        a_shin = math.atan2(target[1] - knee_new[1], target[0] - knee_new[0])
    else:
        a_shin = a_thigh
    m_thigh = m_translate(hip_new[0] - geo["hip"][0], hip_new[1] - geo["hip"][1]) @ m_rotate(a_thigh - geo["a1"], *geo["hip"])
    m_shin = m_translate(knee_new[0] - geo["knee"][0], knee_new[1] - geo["knee"][1]) @ m_rotate(a_shin - geo["a2"], *geo["knee"])
    return m_thigh, m_shin, reach


def pose_frames(actor, motion, legs_geo):
    """동작 값 → 칸마다 뼈 이름별 2D 변환(3x3)과 다리 기록."""
    fwd = 1.0 if actor.get("facing", "right") == "right" else -1.0
    order = bone_order(actor)
    n = motion["frames"]
    stride = 0.0
    if motion.get("legs"):
        mean_len = sum(g["l1"] + g["l2"] for g in legs_geo.values()) / len(legs_geo)
        stride = round(motion["stride"] * mean_len / 4.0) * 4.0       # 칸마다 정수 픽셀로 떨어지게
    frames, notes = [], []
    for f in range(n):
        world = {}
        for name in order:
            spec = actor["bones"][name]
            pivot = px_to_world(actor, *joint_xy(actor, spec["joint"]))
            ang = math.radians(motion.get("rot", {}).get(name, [0.0] * n)[f]) * fwd
            sh = motion.get("shift", {}).get(name, {})
            dx = sh.get("dx", [0.0] * n)[f]
            dz = -sh.get("dy", [0.0] * n)[f]
            if name == "root":
                dz -= motion.get("root", {}).get("dy", [0.0] * n)[f]
            local = m_translate(dx, dz) @ m_rotate(ang, *pivot)
            parent = spec.get("parent")
            world[name] = (world[parent] @ local) if parent else local
        note = {}
        root_x = px_to_world(actor, *actor["joints"]["root"])[0]
        for key, geo in legs_geo.items():
            ch = motion.get("legs", {}).get(key)
            if not ch:
                continue
            # 걸을 때 발이 지나는 줄: 서 있는 자리에서 몸 가운데 쪽으로 narrow 만큼 모은다
            geo["center"] = round(geo["mid"][0] + motion.get("narrow", 0.0) * (root_x - geo["mid"][0]))
            hip_new = apply2(world["root"], geo["hip"])
            lift = ch["lift"][f] * (geo["l1"] + geo["l2"])
            want_low = geo["ground"] + lift                           # 발바닥에서 가장 낮은 점이 있어야 할 높이
            target = [geo["center"] + fwd * ch["x"][f] * stride / 2.0, want_low]
            for _ in range(24):                                       # 신발이 돌아도 발바닥의 가장 낮은 점이 땅에 오게 맞춘다
                m_thigh, m_shin, reach = solve_leg(geo, hip_new, target, fwd)
                low = min(apply2(m_shin, pt)[1] for pt in geo["sole"])
                if abs(low - want_low) < 0.005 and reach:
                    break
                target[1] -= low - want_low
            world[geo["thigh"]], world[geo["shin"]] = m_thigh, m_shin
            mid = apply2(m_shin, geo["mid"])
            low = min(apply2(m_shin, pt)[1] for pt in geo["sole"])
            note[key] = {"x": round(float(mid[0] - geo["center"]), 2), "above_ground": round(float(low - geo["ground"]), 2),
                         "stance": ch["lift"][f] == 0, "reach": reach}
        frames.append(world)
        notes.append(note)
    return frames, notes, stride


def m4(m):
    return Matrix(((m[0][0], 0.0, m[0][1], m[0][2]), (0.0, 1.0, 0.0, 0.0), (m[1][0], 0.0, m[1][1], m[1][2]), (0.0, 0.0, 0.0, 1.0)))


def set_pose(arm, actor, world):
    for name in bone_order(actor):
        pb = arm.pose.bones[name]
        pb.matrix = m4(world[name]) @ pb.bone.matrix_local
        bpy.context.view_layer.update()                 # 부모가 먼저 자리 잡아야 자식 값이 맞게 풀린다


def bake_action(arm, actor, name, frames):
    """칸마다 자세를 키로 박아 액션 하나로 남긴다 (.blend 에서 다시 재생할 수 있게)."""
    if arm.animation_data is None:
        arm.animation_data_create()
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    arm.animation_data.action = act
    for f, world in enumerate(frames, start=1):
        set_pose(arm, actor, world)
        for pb in arm.pose.bones:
            pb.keyframe_insert("location", frame=f)
            pb.keyframe_insert("rotation_quaternion", frame=f)
    return act


def use_action(arm, act):
    arm.animation_data.action = act
    if hasattr(arm.animation_data, "action_slot") and getattr(act, "slots", None):
        if arm.animation_data.action_slot is None:
            arm.animation_data.action_slot = act.slots[0]


# ==========================================================================
# 4. 픽셀 정리
# ==========================================================================
def snap_palette(rgb, palette):
    d = ((rgb[:, None, :].astype(np.int32) - palette[None, :, :].astype(np.int32)) ** 2).sum(axis=2)
    return np.argmin(d, axis=1)


def clean_frame(big, palette, scale, reduce):
    """4배 렌더 → 원래 크기. 색은 정지 도트 색 목록으로, 알파는 0 아니면 255."""
    hh, ww = big.shape[:2]
    h, w = hh // scale, ww // scale
    idx = snap_palette(big[:, :, :3].reshape(-1, 3), palette).reshape(hh, ww)
    idx[big[:, :, 3] < 128] = -1
    c = scale // 2
    center = idx[c::scale, c::scale][:h, :w]
    if reduce == "nearest":
        small = center.copy()
    else:
        blocks = idx[:h * scale, :w * scale].reshape(h, scale, w, scale)
        best, best_n = np.full((h, w), -1), np.zeros((h, w))
        for k in range(-1, len(palette)):
            cnt = (blocks == k).sum(axis=(1, 3)) + 0.5 * (center == k)   # 비기면 가운데 점의 색
            better = cnt > best_n
            best[better], best_n[better] = k, cnt[better]
        small = best
    # 사방이 막힌 한 칸짜리 구멍은 이웃 색으로 막는다. 단, 4배 렌더에서도 통째로 비어 있던 칸은 원래 그림의 구멍이라 둔다
    pad = np.pad(small, 1, constant_values=-1)
    nb = np.stack([pad[:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, :-2], pad[1:-1, 2:]])
    partly = (idx[:h * scale, :w * scale].reshape(h, scale, w, scale) >= 0).any(axis=(1, 3))
    hole = (small < 0) & (nb >= 0).all(axis=0) & partly
    small[hole] = nb[0][hole]
    out = np.zeros((h, w, 4), dtype=np.uint8)
    on = small >= 0
    out[on, :3] = palette[small[on]]
    out[on, 3] = 255
    return out, int(hole.sum())


# ==========================================================================
def main():
    args = parse_args()
    t_start = time.perf_counter()
    name = args.actor
    with open(os.path.join(RIG_DIR, f"{name}_parts.json"), encoding="utf-8") as f:
        actor = json.load(f)
    work = os.path.join(os.path.abspath(args.out), "dot_rig", name)
    parts_dir = os.path.join(work, "parts")
    frames_dir = os.path.join(work, "frames")
    for d in (parts_dir, frames_dir):
        os.makedirs(d, exist_ok=True)

    def out_path(fname):
        return os.path.join(os.path.abspath(args.out), f"{args.prefix}{fname}")

    src_path = args.source or fetch(actor["source"], os.path.join(work, "source.png"))
    src = read_image(src_path)
    w, h = actor["cell"]
    if src.shape[:2] != (h, w):
        raise SystemExit(f"정지 도트 크기 {src.shape[1]}x{src.shape[0]} 가 cell {w}x{h} 와 다르다")
    opaque = src[:, :, 3] >= 128
    palette = np.unique(src[opaque][:, :3], axis=0)
    log(f"{name}: {w}x{h}, 색 {len(palette)}개, 불투명 픽셀 {int(opaque.sum())}")

    # ---- 1. 부위 자르기 ----
    parts_img, owner, stats, unowned = cut_parts(src, actor)
    debug_sheets(parts_img, actor, src, work)
    for pname, st in stats.items():
        log(f"  부위 {pname:12s} 제 픽셀 {st['own']:5d}  빌림 {st['shared']:4d}  메움 {st['filled']:4d}")
    if unowned:
        raise SystemExit(f"임자 없는 픽셀 {unowned}개 — parts 에 rest 부위가 필요하다")
    if args.parts_only:
        log(f"부위 확인용 그림: {work}/parts.png, parts_marked.png, owner.png")
        return

    # ---- 2. 판 · 뼈대 ----
    scene, arm = build_scene(actor, parts_img, parts_dir, args.scale)
    legs_geo = {k: leg_geometry(actor, k) for k in actor.get("legs", {})}

    def render_frame(path):
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        return read_image(path)

    # 가만히 선 자세가 원본과 픽셀 하나까지 같은지 먼저 본다
    t0 = time.perf_counter()
    rest_big = render_frame(os.path.join(frames_dir, "rest.png"))
    rest, _ = clean_frame(rest_big, palette, args.scale, args.reduce)
    rest_diff = int(((rest != np.where(opaque[:, :, None], src, 0)).any(axis=2)).sum())
    log(f"선 자세 렌더 ↔ 원본: 다른 픽셀 {rest_diff}개")

    # ---- 3. 동작 → 4. 렌더 · 픽셀 정리 ----
    motions, actions, report, rig_json = {}, {}, {"actor": name, "rest_diff_pixels": rest_diff, "parts": stats, "motions": {}}, {}
    for mname in actor["motions"]:
        with open(os.path.join(RIG_DIR, "motions", f"{mname}.json"), encoding="utf-8") as f:
            motions[mname] = json.load(f)
        frames, notes, stride = pose_frames(actor, motions[mname], legs_geo)
        actions[mname] = (bake_action(arm, actor, f"{name}_{mname}", frames), frames, notes, stride)
    render_secs = time.perf_counter() - t0
    face = actor.get("check_face")
    for mname, (act, frames, notes, stride) in actions.items():
        motion = motions[mname]
        n = motion["frames"]
        use_action(arm, act)
        cells, holes, lows, face_same = [], 0, [], []
        t1 = time.perf_counter()
        for f in range(1, n + 1):
            scene.frame_set(f)
            big = render_frame(os.path.join(frames_dir, f"{mname}_{f}.png"))
            cell, nh = clean_frame(big, palette, args.scale, args.reduce)
            holes += nh
            cells.append(cell)
            rows = np.nonzero(cell[:, :, 3].any(axis=1))[0]
            lows.append(int(rows.max()))
            if face:                                     # 얼굴 영역이 원본과 같은지 (몸이 오르내린 만큼 옮겨서 비교)
                x0, y0, x1, y1 = face
                off = int(round(motion.get("root", {}).get("dy", [0] * n)[f - 1] + motion.get("shift", {}).get("torso", {}).get("dy", [0] * n)[f - 1]))
                a = cell[y0 + off:y1 + off, x0:x1]
                b = src[y0:y1, x0:x1]
                face_same.append(int((a != b).any(axis=2).sum()))
        render_secs += time.perf_counter() - t1
        sheet = np.concatenate(cells, axis=1)
        sheet_name = f"{name}-{mname}-rig.png"
        write_png(out_path(sheet_name), sheet)
        colors = {tuple(c) for c in sheet[sheet[:, :, 3] > 0][:, :3]}
        pal_set = {tuple(c) for c in palette}
        entry = {"frames": n, "order": list(range(n)), "ms": motion["ms"]}
        info = {"sheet": sheet_name, "size": [sheet.shape[1], sheet.shape[0]], "lowest_row": lows, "holes_closed": holes,
                "colors_outside_palette": len(colors - pal_set), "alpha_values": sorted(int(v) for v in np.unique(sheet[:, :, 3])),
                "face_diff_pixels": face_same}
        if motion.get("legs"):
            half = sum(motion["ms"][:n // 2]) / 1000.0
            entry["speed"] = round(float(stride) / half, 1)            # 보폭 ÷ 반 주기 = 디딘 발이 미끄러지지 않는 빠르기
            entry["dy"] = [0] * n                               # 오르내림은 시트에 이미 들어 있다
            steps = {}
            for key in legs_geo:
                xs = [nt[key]["x"] for nt in notes]
                st = [nt[key]["stance"] for nt in notes]
                steps[key] = {"x": xs, "stance": st, "above_ground": [nt[key]["above_ground"] for nt in notes],
                              "reach": all(nt[key]["reach"] for nt in notes),
                              "stance_step_px": sorted({round(xs[i] - xs[(i + 1) % n], 2) for i in range(n) if st[i] and st[(i + 1) % n]})}
            info.update({"stride_px": float(stride), "half_cycle_sec": half, "legs": steps})
        rig_json[mname] = entry
        report["motions"][mname] = info
        log(f"{mname}: {n}칸 → {sheet_name}, 맨 아래 줄 {lows}, 색 목록 밖 {info['colors_outside_palette']}, 얼굴 다른 픽셀 {face_same}")
        if motion.get("legs"):
            for key, st in info["legs"].items():
                log(f"  다리 {key}: 발 x {st['x']}, 디딘 발 한 칸 이동 {st['stance_step_px']}px, 땅 위 {st['above_ground']}, 닿음 {st['reach']}")
    with open(out_path(f"{name}-rig.json"), "w", encoding="utf-8") as f:
        f.write("{\n" + ",\n".join(f'  {json.dumps(k)}: {json.dumps(v)}' for k, v in rig_json.items()) + "\n}\n")

    # ---- 미리보기(WebP): 시스템 파이썬 + Pillow 로 만든다 ----
    spec = {"actor": name, "cell": actor["cell"], "out": os.path.abspath(args.out), "prefix": args.prefix, "zoom": 2,
            "motions": {m: {"sheet": out_path(f"{name}-{m}-rig.png"), "frames": rig_json[m]["frames"], "ms": rig_json[m]["ms"]} for m in rig_json}}
    if actor.get("compare") and "walk" in rig_json:
        cmp_spec = dict(actor["compare"])
        cmp_spec["file"] = fetch(cmp_spec["file"], os.path.join(work, "compare.png"))
        spec["compare"] = cmp_spec
    spec_path = os.path.join(work, "preview_spec.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f)
    py = shutil.which("python3") or shutil.which("python")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    res = subprocess.run([py, os.path.join(RIG_DIR, "preview.py"), spec_path], capture_output=True, text=True, env=env) if py else None
    if res is None or res.returncode != 0:
        log(f"미리보기 WebP 를 만들지 못했다 (python3 + Pillow 필요): {(res.stderr.strip()[-300:] if res else 'python3 없음')}")
        report["previews"] = []
    else:
        report["previews"] = [ln for ln in res.stdout.split("\n") if ln]
        log("미리보기: " + ", ".join(os.path.basename(p) for p in report["previews"]))

    # ---- 마무리 ----
    if not args.no_blend:
        use_action(arm, actions[actor["motions"][0]][0])
        scene.frame_start, scene.frame_end = 1, motions[actor["motions"][0]]["frames"]
        scene.frame_set(1)
        scene.render.filepath = "//frame_"
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(RIG_DIR, f"{name}.blend"), compress=True)
    report["reduce"] = args.reduce
    report["times_sec"] = {"render": round(render_secs, 1), "total": round(time.perf_counter() - t_start, 1)}
    with open(os.path.join(work, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    log(f"렌더 {report['times_sec']['render']}s, 전체 {report['times_sec']['total']}s -> {os.path.abspath(args.out)}")
    if rest_diff:
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
