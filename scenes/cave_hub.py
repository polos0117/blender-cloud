"""공방 허브 「대미궁」 지도의 3D 구조 (headless Blender).

scenes/cave_hub.json 을 읽어 산속 드워프 대미궁 구조를 만들고, 한 번에
구조 그림 · 선 그림 · 방 색깔 지도 · 깊이 그림 · 허브용 좌표(JSON)를 뽑는다.

실행:
    ~/blender/blender -b --python scenes/cave_hub.py -- --out renders/
    # 날짜를 붙여 그림 저장소에 바로 쓰기
    ~/blender/blender -b --python scenes/cave_hub.py -- --out ../blender-cloud-img/img --prefix 2026-11-11_

좌표계: X = 그림의 좌우, +Y = 그림의 위쪽(동굴 안쪽), Z = 높이. 호수 수면이 z=0.
카메라는 남쪽 위에서 북쪽을 내려다보는 정사영이다.
"""

import argparse
import copy
from collections import deque
from types import SimpleNamespace
import json
import math
import os
import struct
import sys
import time
import zlib

import bmesh
import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TAU = math.tau
OFF = 0.043   # 길 검사 광선이 면의 대칭축(삼각형 분할 모서리)을 타지 않게 옆으로 비키는 거리
EPS = 0.012   # 겹쳐 닿는 윗면끼리 같은 높이가 되지 않게 살짝 낮춘다


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="cave_hub.py")
    p.add_argument("--out", default=os.path.join(REPO, "renders"), help="그림·JSON 을 쓸 폴더")
    p.add_argument("--prefix", default="", help="파일 이름 앞에 붙일 글자 (예: 2026-11-11_)")
    p.add_argument("--config", default=os.path.join(HERE, "cave_hub.json"))
    p.add_argument("--blend", default=os.path.join(HERE, "cave_hub.blend"))
    p.add_argument("--samples", type=int, default=None, help="구조 그림 샘플 수 (기본: 설정 파일)")
    p.add_argument("--scale", type=float, default=1.0, help="미리보기용 해상도 배율 (0.5 = 512x1024)")
    p.add_argument("--check-only", action="store_true", help="구조 생성과 검사만 하고 렌더는 건너뛴다")
    p.add_argument("--no-blend", action="store_true", help=".blend 를 저장하지 않는다")
    p.add_argument("--selftest-break", default=None, metavar="KEY", help="검사 자체를 시험: 이 방의 연결로를 빼고 만든다 (길 검사가 실패해야 정상)")
    p.add_argument("--levels", type=int, choices=(1, 2), default=None)
    p.add_argument("--selftest-nav-break", metavar="KEY", help="이 문 노드를 고립시켜 그래프 검사를 시험")
    p.add_argument("--light", default=None, metavar="TIME",
                   help="시간대 빛 그림만 뽑는다: dawn · morning · night, 쉼표로 여럿, all 은 전부 (구조 그림 · .blend 는 건너뛴다)")
    p.add_argument("--light-base", default=None, help="미리보기에 쓸 칠한 지도(파일 경로나 주소). 기본은 설정의 light.base")
    return p.parse_args(argv)


def log(msg):
    print(f"[cave_hub] {msg}", flush=True)


# ==========================================================================
# 작은 수학 도구
# ==========================================================================
def v_add(a, b, s=1.0):
    return (a[0] + b[0] * s, a[1] + b[1] * s)


def v_len(a):
    return math.hypot(a[0], a[1])


def v_norm(a):
    n = v_len(a)
    return (a[0] / n, a[1] / n) if n > 1e-12 else (0.0, 0.0)


def rot90(a):
    return (-a[1], a[0])


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def circle(cx, cy, r, n, a0=0.0, a1=TAU, close=False):
    m = n + 1 if close else n
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(m)]


def resample(points, spacing):
    """3D 꺾은선을 일정 간격으로 다시 찍는다."""
    out = [points[0]]
    for a, b in zip(points[:-1], points[1:]):
        d = math.dist(a, b)
        k = max(1, int(math.ceil(d / spacing)))
        for i in range(1, k + 1):
            t = i / k
            out.append(tuple(a[j] + (b[j] - a[j]) * t for j in range(3)))
    return out


class Oval:
    """호수 윤곽(초타원)과 그 바깥으로 t 만큼 넓힌 곡선들. t=0 이 물가."""

    def __init__(self, a, b, n):
        self.a, self.b, self.n = a, b, n

    def pt(self, t, th):
        c, s = math.cos(th), math.sin(th)
        e = 2.0 / self.n
        return ((self.a + t) * math.copysign(abs(c) ** e, c), (self.b + t) * math.copysign(abs(s) ** e, s))

    def hw(self, t, y):
        r = min(abs(y) / (self.b + t), 1.0)
        return (self.a + t) * (1.0 - r ** self.n) ** (1.0 / self.n)

    def tcoord(self, x, y):
        """점(배열)이 어느 t 곡선 위에 있는지 (이분법)."""
        x = np.abs(np.asarray(x, dtype=np.float64))
        y = np.abs(np.asarray(y, dtype=np.float64))
        lo = np.full(x.shape, -self.a + 0.05)
        hi = np.full(x.shape, 90.0)
        for _ in range(36):
            mid = (lo + hi) * 0.5
            inside = (x / (self.a + mid)) ** self.n + (y / (self.b + mid)) ** self.n < 1.0
            hi = np.where(inside, mid, hi)
            lo = np.where(inside, lo, mid)
        return (lo + hi) * 0.5

    def thetas(self, t, spacing):
        """t 곡선을 호 길이로 고르게 나눈 각도들."""
        th = np.linspace(0.0, TAU, 6001)
        pts = np.array([self.pt(t, a) for a in th])
        cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        m = max(8, int(round(cum[-1] / spacing)))
        return np.interp(np.linspace(0.0, cum[-1], m, endpoint=False), cum, th), cum[-1]


# ==========================================================================
# 메시 쌓기
# ==========================================================================
class Geo:
    """꼭짓점·면을 모았다가 오브젝트 하나로 만든다."""

    counter = 0

    def __init__(self):
        self.v, self.f = [], []

    def _add(self, verts, faces):
        b = len(self.v)
        self.v.extend(verts)
        self.f.extend(tuple(b + i for i in f) for f in faces)

    def ribbon2(self, left, right, ztop, zbot, closed=False):
        """왼쪽·오른쪽 가장자리 사이를 채운 띠 모양 덩어리. 같은 자리에 점을 두 번 두면 계단 단이 된다."""
        n = len(left)
        zt = list(ztop) if hasattr(ztop, "__len__") else [ztop] * n
        zb = list(zbot) if hasattr(zbot, "__len__") else [zbot] * n
        verts, faces = [], []
        for i in range(n):
            (lx, ly), (rx, ry) = left[i], right[i]
            verts += [(lx, ly, zt[i]), (rx, ry, zt[i]), (lx, ly, zb[i]), (rx, ry, zb[i])]
        for i in (range(n) if closed else range(n - 1)):
            j = (i + 1) % n
            a, b = 4 * i, 4 * j
            if math.dist(left[i], left[j]) + math.dist(right[i], right[j]) > 1e-7:
                faces += [(a + 1, b + 1, b, a), (a, b, b + 2, a + 2), (b + 1, a + 1, a + 3, b + 3), (a + 2, b + 2, b + 3, a + 3)]
            else:
                if abs(zt[i] - zt[j]) > 1e-7:
                    faces.append((a + 1, b + 1, b, a))
                if abs(zb[i] - zb[j]) > 1e-7:
                    faces.append((a + 2, b + 2, b + 3, a + 3))
        if not closed:
            e = 4 * (n - 1)
            faces += [(0, 2, 3, 1), (e + 1, e + 3, e + 2, e)]
        self._add(verts, faces)

    @staticmethod
    def _dir(xy, i, sgn, closed):
        n = len(xy)
        j = i
        for _ in range(n):
            j += sgn
            if closed:
                j %= n
            elif j < 0 or j >= n:
                return None
            d = (xy[j][0] - xy[i][0], xy[j][1] - xy[i][1])
            if v_len(d) > 1e-7:
                d = v_norm(d)
                return (d[0] * sgn, d[1] * sgn)
        return None

    def ribbon(self, xy, ztop, zbot, width, offset=0.0, closed=False):
        """가운뎃줄을 따라가는 띠. offset 은 진행 방향 왼쪽(+)으로 민다."""
        left, right = [], []
        for i in range(len(xy)):
            tp, tn = self._dir(xy, i, -1, closed), self._dir(xy, i, 1, closed)
            tp, tn = tp or tn, tn or tp
            t = v_norm((tp[0] + tn[0], tp[1] + tn[1]))
            if v_len(t) < 1e-6:
                t = tn
            nrm = rot90(t)
            k = 1.0 / max(0.5, nrm[0] * rot90(tn)[0] + nrm[1] * rot90(tn)[1])
            left.append(v_add(xy[i], nrm, (offset + width / 2) * k))
            right.append(v_add(xy[i], nrm, (offset - width / 2) * k))
        self.ribbon2(left, right, ztop, zbot, closed)

    def prism(self, poly, zbot, ztop):
        n = len(poly)
        verts = [(x, y, ztop) for x, y in poly] + [(x, y, zbot) for x, y in poly]
        faces = [tuple(range(n)), tuple(range(2 * n - 1, n - 1, -1))]
        faces += [(i, i + n, (i + 1) % n + n, (i + 1) % n) for i in range(n)]
        self._add(verts, faces)

    def box(self, cx, cy, half, zbot, ztop, half_y=None):
        hy = half if half_y is None else half_y
        self.prism([(cx - half, cy - hy), (cx + half, cy - hy), (cx + half, cy + hy), (cx - half, cy + hy)], zbot, ztop)

    def cone(self, cx, cy, r, z0, z1, segs=8, tip=(0.0, 0.0), a0=0.0):
        verts = [(x, y, z0) for x, y in circle(cx, cy, r, segs, a0, a0 + TAU)] + [(cx + tip[0], cy + tip[1], z1)]
        faces = [(i, (i + 1) % segs, segs) for i in range(segs)] + [tuple(range(segs - 1, -1, -1))]
        self._add(verts, faces)

    def profile(self, poly_uz, origin, lat, dirv, s0, s1, zbase):
        """(가로 u, 높이 z) 단면을 dirv 방향으로 s0~s1 만큼 밀어낸 덩어리 (아치 문 등)."""
        n = len(poly_uz)
        verts = []
        for s in (s0, s1):
            for u, z in poly_uz:
                verts.append((origin[0] + lat[0] * u + dirv[0] * s, origin[1] + lat[1] * u + dirv[1] * s, zbase + z))
        faces = [tuple(range(n)), tuple(range(2 * n - 1, n - 1, -1))]
        faces += [(i, i + n, (i + 1) % n + n, (i + 1) % n) for i in range(n)]
        self._add(verts, faces)

    def obj(self, name, mat, coll, room=0, walk=False, smooth=False, fix=True):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(self.v, [], self.f)
        if fix:
            bm = bmesh.new()
            bm.from_mesh(mesh)
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            bm.to_mesh(mesh)
            bm.free()
        if smooth:
            mesh.shade_smooth()
        mesh.materials.append(mat)
        ob = bpy.data.objects.new(name, mesh)
        coll.objects.link(ob)
        Geo.counter += 1
        ob.pass_index = Geo.counter
        ob.color = (room / 64.0, 0.0, 0.0, 1.0)  # 방 번호 (데이터 패스에서 읽는다)
        ob["walk"] = bool(walk)
        return ob


def arch_profile(half_open, height, jamb, flat_top=None, segs=12, rough=None):
    """아치 문 단면. flat_top 을 주면 바깥 윗변이 그 높이의 평평한 문루가 된다."""
    spring = height - half_open
    ho = half_open + jamb
    pts = [(-ho, 0.0)]
    if flat_top is not None:
        pts += [(-ho, flat_top), (ho, flat_top)]
    else:
        for i in range(segs + 1):
            a = math.pi * (1 - i / segs)
            r = ho * (1.0 + (rough[i % len(rough)] if rough else 0.0))
            pts.append((r * math.cos(a), spring + r * math.sin(a)))
    pts += [(ho, 0.0), (half_open, 0.0)]
    for i in range(segs + 1):
        a = math.pi * i / segs
        pts.append((half_open * math.cos(a), spring + half_open * math.sin(a)))
    pts.append((-half_open, 0.0))
    return pts


# ==========================================================================
# 배치 계산
# ==========================================================================
class Plan:
    def __init__(self, cfg):
        self.cfg = cfg
        self.two = cfg.get("levels", {}).get("count", 1) == 2
        lk, rg, rl = cfg["lake"], cfg["ring"], cfg["rooms_layout"]
        self.oval = Oval(lk["half_width"], lk["half_length"], lk["exponent"])
        self.g = lk["shore"]                    # 물가 ~ 둘레 길 안쪽
        self.t_in = self.g
        self.t_out = self.g + rg["width"]       # 둘레 길 바깥쪽
        self.t_mid = self.g + rg["width"] / 2
        self.t_wall = self.t_out + rg["shelf_width"]
        self.zr = rg["z"]                       # 둘레 길 높이
        self.ground = self.zr - rg["shelf_drop"]
        self.b_out = self.oval.b + self.t_out
        self.rooms = self._rooms(cfg, rl)
        self._entrance(cfg["entrance"])
        self._north(cfg["north"])
        # Preserve the original upper-floor projection exactly; extend only below.
        legacy_image = dict(cfg["image"], height=cfg["image"]["width"] * 2)
        self._camera(cfg["camera"], legacy_image if self.two else cfg["image"])
        if self.two:
            self.ortho *= cfg["image"]["height"] / legacy_image["height"]
            self.v_min = self.v_max - self.ortho
            self.fill["ortho"] = round(self.ortho, 2)
            self.lower = cfg["levels"]["lower"]
            self.spiral = cfg["levels"]["spiral"]
            self.lower_rooms = []
            low = self.lower
            for source in cfg["rooms"]:
                if source.get("level", 1) != 2:
                    continue
                r = dict(source)
                cx, cy = source["position"]
                d = v_norm((cx, cy - low["hall_y"]))
                P = (d[0] * low["path_radius"], low["hall_y"] + d[1] * low["path_radius"])
                length = math.dist(P, (cx, cy)) - low["room_radius"] - self.wall
                r.update(c=(cx, cy), zf=low["z"], P=P, d=d, n=rot90(d), L=length,
                         rise=0, link="bridge", index=len(self.lower_rooms), level=2)
                self.lower_rooms.append(r)
            self.y_cliff = low["entrance"][1] - 2.0
            self.z_entry = low["entrance"][2]


    # -- 방: 한쪽 벽의 방 수로 세로를 나누고, 문에서 둘레 길까지 복도를 잇는다
    def _rooms(self, cfg, rl):
        sides = {"left": [], "right": []}
        self.island_room = None
        for r in cfg["rooms"]:
            if r.get("level", 1) != 1:
                continue
            if r["side"] == "island":
                self.island_room = r
            else:
                sides[r["side"]].append(r)
        lo, hi = rl["span"][0] * self.b_out, rl["span"][1] * self.b_out
        pitches = [(hi - lo) / len(v) for v in sides.values() if v]
        radius = min(rl["radius"], (min(pitches) - rl["gap"]) / 2 - rl["wall"])  # 모든 방 같은 크기
        self.R, self.wall = radius, rl["wall"]
        beta = math.radians(rl["door_yaw_deg"])
        out = []
        for side, lst in sides.items():
            sgn = -1.0 if side == "left" else 1.0
            for i, r in enumerate(lst):
                pitch = (hi - lo) / len(lst)
                yc = hi - (i + 0.5) * pitch
                rise = r.get("rise", rl["rise_pattern"][i % len(rl["rise_pattern"])])
                link = r.get("link") or ("spiral" if rise >= rl["spiral_min_rise"] else "stairs")
                if link == "spiral":
                    steps = rl["spiral_steps"]
                    low = 1.6
                    length = low + 1.6
                else:
                    steps = max(1, int(round(rise / rl["step_rise"])))
                    low = None
                    length = max(rl["corridor"], rl["balcony_depth"] + steps * rl["step_run"] + 0.35)
                d = (sgn * math.cos(beta), math.sin(beta))      # 둘레 길 → 방 방향
                reach = length + self.wall + radius
                y_p = yc - reach * d[1]
                p = (sgn * self.oval.hw(self.t_out, y_p), y_p)  # 둘레 길 바깥 가장자리의 이음점
                out.append({
                    "level": 1, "color": r.get("color"), "key": r["key"], "name": r.get("name", r["key"]), "side": side, "index": i,
                    "c": v_add(p, d, reach), "zf": self.zr + rise, "rise": rise, "link": link, "steps": steps,
                    "P": p, "d": d, "n": rot90(d), "L": length, "low": low,
                })
        return out

    def _entrance(self, en):
        """입구 계단: 둘레 길 남쪽 끝에서 남쪽(그림 아래)으로 곧게 내려간다. (s, z) 단면을 만든다."""
        y0 = -self.b_out + 0.35                 # 둘레 길 안으로 조금 겹친다
        prof = [(0.0, self.zr), (0.55, self.zr)]
        s, z = 0.55, self.zr
        marks = {}
        for fi, nsteps in enumerate(en["flights"]):
            for _ in range(nsteps):
                z -= en["step_rise"]
                prof.append((s, z))
                s += en["step_run"]
                prof.append((s, z))
            if fi < len(en["flights"]) - 1:
                marks[f"landing{fi}"] = (s, s + en["landing"], z)
                s += en["landing"]
                prof.append((s, z))
        marks["terrace"] = (s, s + en["terrace"], z)
        s += en["terrace"]
        prof.append((s, z))
        self.stair_prof, self.stair_marks = prof, marks
        self.stair_y0, self.stair_len, self.z_entry = y0, s, z
        self.y_mouth = y0 - s + 0.75            # 동굴 입구 아치의 자리
        self.y_cliff = y0 - s - 0.5             # 절벽 끝

    def stair_z(self, y):
        s = self.stair_y0 - np.asarray(y, dtype=np.float64)
        ss = np.array([p[0] for p in self.stair_prof])
        zz = np.array([p[1] for p in self.stair_prof])
        return np.interp(s, ss, zz)

    def _north(self, no):
        self.pool_y = self.b_out + no["pool_gap"] + no["pool_radius"]
        self.pool_r = no["pool_radius"]
        self.canopy_zb, self.canopy_zt = no["canopy_z"]
        self.canopy_y = self.pool_y + 1.0       # 천장 조각의 앞 가장자리 (평균)

    def canopy_front(self, x):
        return self.canopy_y + 0.5 * np.sin(0.9 * np.asarray(x) + 1.0) + 0.3 * np.sin(2.3 * np.asarray(x))

    def _camera(self, cam, img):
        elev = math.radians(cam["elevation_deg"])
        self.sv, self.cv = math.sin(elev), math.cos(elev)     # 화면 세로 = y*sv + z*cv
        self.elev = elev
        u_half = max(abs(r["c"][0]) + self.R + self.wall for r in self.rooms) + cam["side_pad"]
        v_top = self.sv * (self.canopy_y - 0.8) + self.cv * (self.canopy_zt + 0.4)
        v_bot = self.sv * self.y_cliff + self.cv * self.z_entry - cam["cliff_show"]
        aspect = img["height"] / img["width"]
        mt, mb = cam["margin_top"], cam["margin_bottom"]
        s_v = (v_top - v_bot) / (1 - mt - mb)
        s_u = 2 * u_half * aspect
        self.ortho = max(s_v, s_u)
        slack = self.ortho * (1 - mt - mb) - (v_top - v_bot)
        self.v_min = v_bot - mb * self.ortho - 0.5 * slack      # 남는 세로는 위아래로 나눈다
        self.v_max = self.v_min + self.ortho
        self.u_half_view = self.ortho / aspect / 2
        self.fill = {"need_v": round(s_v, 2), "need_u": round(s_u, 2), "ortho": round(self.ortho, 2)}

    # 허브 지도용 길 (3D 가운뎃줄)
    def ring_side(self, side, n=17):
        ths, _ = self.oval.thetas(self.t_mid, 0.05)
        ths = np.array(sorted(ths))
        if side == "left":
            sel = ths[(ths >= math.pi / 2) & (ths <= 1.5 * math.pi)][::-1]   # 270° → 90°
            sel = np.concatenate([[1.5 * math.pi], sel, [math.pi / 2]])
        else:
            sel = np.concatenate([ths[ths >= 1.5 * math.pi] - TAU, ths[ths <= math.pi / 2]])
            sel = np.concatenate([[-math.pi / 2], sel, [math.pi / 2]])
        pick = np.linspace(0, len(sel) - 1, n).round().astype(int)
        return [(*self.oval.pt(self.t_mid, float(sel[i])), self.zr) for i in pick]


# ==========================================================================
# 지형 (높이장)
# ==========================================================================
def fbm(x, y, seed, scale, octaves=4):
    rng = np.random.default_rng(seed)
    out = np.zeros_like(x, dtype=np.float64)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        grid = rng.random((67, 67))
        f = scale * (2 ** o)
        gx, gy = (x * f + 13.7 * o) % 64.0, (y * f + 7.3 * o) % 64.0
        ix, iy = np.floor(gx).astype(int), np.floor(gy).astype(int)
        fx, fy = gx - ix, gy - iy
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        val = (grid[iy, ix] * (1 - fx) + grid[iy, ix + 1] * fx) * (1 - fy) + (grid[iy + 1, ix] * (1 - fx) + grid[iy + 1, ix + 1] * fx) * fy
        out += amp * (val - 0.5)
        tot += amp
        amp *= 0.5
    return out / tot * 2.0


def seg_dist(x, y, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    t = np.clip(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
    return np.hypot(x - (ax + t * dx), y - (ay + t * dy))


def carve(h, dist, floor, wall_w):
    """dist<0 인 곳을 floor 까지 파고, wall_w 에 걸쳐 원래 높이로 돌아간다."""
    return np.minimum(h, floor + (h - floor) * smoothstep(0.0, wall_w, dist))


def wall_height(rl, phi):
    """방 벽 높이: 카메라 쪽(남)은 낮고 안쪽(북)은 높다."""
    return rl["wall_front"] + (rl["wall_back"] - rl["wall_front"]) * smoothstep(0.0, 1.0, (np.sin(phi) + 1.0) / 2.0)


def terrain_height(plan, x, y):
    cfg = plan.cfg
    tr, rl, en = cfg["terrain"], cfg["rooms_layout"], cfg["entrance"]
    seed = tr["seed"]
    t = plan.oval.tcoord(x, y)
    rough = fbm(x, y, seed, 0.11)
    plateau = (tr["plateau_z"] + tr["plateau_slope"] * np.clip(t - plan.t_wall, 0.0, 30.0) + tr["noise"] * rough
               + tr["back_wall_rise"] * smoothstep(plan.b_out + 1.5, plan.pool_y + plan.pool_r + 2.0, y))
    shelf = plan.ground + 0.05 * fbm(x, y, seed + 1, 0.6, 2)
    h = shelf + (plateau - shelf) * smoothstep(plan.t_wall, plan.t_wall + 0.9, t)
    # 호수 바닥과 물가
    bed = cfg["lake"]["bed_z"]
    lake = np.interp(t, [-1.6, 0.0, plan.g], [bed, -0.12, plan.ground])
    h = np.where(t < plan.g, lake, h)
    # 북쪽 폭포 웅덩이와 둘레 길 아래로 호수에 닿는 물길
    pool_d = np.hypot((x) / 1.15, y - plan.pool_y) - plan.pool_r
    h = carve(h, pool_d, bed + 0.2, 0.9)
    chan = np.maximum(np.abs(x) - 1.0, np.maximum(plan.oval.b - 1.0 - y, y - plan.pool_y))
    h = carve(h, chan, bed + 0.3, 0.5)
    # 방 자리(통)와 둘레 바위 높이 맞추기, 복도
    for r in plan.rooms:
        cx, cy = r["c"]
        dist = np.hypot(x - cx, y - cy) - (plan.R + plan.wall)
        cap = r["zf"] + wall_height(rl, np.arctan2(y - cy, x - cx)) - 0.12 + 3.0 * smoothstep(0.4, 2.2, dist)
        h = np.where(dist < 2.2, np.minimum(h, cap), h)
        h = carve(h, dist + 0.33, plan.ground, 0.2)      # 벽 두께 안에서만 높이가 바뀌게 (방 안으로 바위가 나오지 않는다)
        door = v_add(r["P"], r["d"], r["L"])
        h = carve(h, seg_dist(x, y, r["P"], door) - 1.25, plan.ground, 0.7)
        if r["link"] == "spiral":
            cx2, cy2 = r["tower"]
            h = carve(h, np.hypot(x - cx2, y - cy2) - 1.75, plan.ground, 0.7)
    if plan.two:
        return lower_terrain(plan, x, y, h)
    # 입구 계단 골짜기
    half = en["stair_width"] / 2 + 0.75
    canyon = np.maximum(np.abs(x) - half, y - (plan.stair_y0 - 0.6))
    floor = plan.stair_z(y) - 0.3
    h = np.minimum(h, floor + (h - floor) * smoothstep(0.0, 0.6, canyon))
    # 입구 옆 폭포가 나오는 홈
    fx, fw = en["fall_x"], en["fall_width"] / 2
    notch = np.maximum(np.abs(x - fx) - fw, y - (plan.y_cliff + 3.4))
    h = carve(h, notch, -0.95, 0.5)
    # 남쪽 절벽
    y_c = plan.y_cliff + 0.7 * np.sin(0.45 * x + 0.6) * smoothstep(3.5, 6.0, np.abs(x)) - 0.35 * np.sin(1.3 * x) * smoothstep(3.5, 6.0, np.abs(x))
    drop = smoothstep(0.0, 1.1, y_c - y)
    h = h + (plan.cliff_base - h) * drop
    return h + 0.04 * fbm(x, y, seed + 2, 1.7, 2) * (1.0 - drop)


def build_terrain(plan, mats, coll):
    tr = plan.cfg["terrain"]
    step = tr["step"]
    xs = np.arange(-plan.u_half_view - 1.0, plan.u_half_view + 1.0 + step, step)
    ys = np.arange(plan.y_cliff - 3.0, plan.canopy_y + 4.0, step)
    gx, gy = np.meshgrid(xs, ys)
    gz = terrain_height(plan, gx, gy)
    nx, ny = len(xs), len(ys)
    verts = np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])
    i, j = np.meshgrid(np.arange(nx - 1), np.arange(ny - 1))
    a = (j * nx + i).ravel()
    faces = np.column_stack([a, a + 1, a + nx + 1, a + nx])
    mesh = bpy.data.meshes.new("Terrain")
    mesh.from_pydata(verts.tolist(), [], faces.tolist())
    mesh.shade_smooth()
    mesh.materials.append(mats["rock"])
    ob = bpy.data.objects.new("Terrain", mesh)
    coll.objects.link(ob)
    Geo.counter += 1
    ob.pass_index = Geo.counter
    ob.color = (0, 0, 0, 1)
    ob["walk"] = False
    return ob


# ==========================================================================
# 구조물
# ==========================================================================
def build_water(plan, mats, coll):
    g = Geo()
    ths, _ = plan.oval.thetas(0.42, 0.4)
    g.prism([plan.oval.pt(0.42, float(a)) for a in ths], -0.6, 0.0)
    lake = g.obj("Water_lake", mats["water"], coll, room=plan.island_code)
    g = Geo()
    y0 = plan.oval.b + 0.5
    g.box(0.0, (y0 + plan.pool_y + plan.pool_r + 1.0) / 2, plan.pool_r * 1.15 + 1.0, -0.6, 0.0, half_y=(plan.pool_y + plan.pool_r + 1.0 - y0) / 2)
    en = plan.cfg["entrance"]
    if plan.two:
        en = dict(en, fall_x=plan.lower["fall_x"])
    pool_top = plan.z_entry + 0.4 if plan.two else -0.45
    g.box(en["fall_x"], plan.y_cliff + 1.55, en["fall_width"] / 2 + 0.6, pool_top - 0.6, pool_top, half_y=2.95)
    g.obj("Water_pool", mats["water"], coll)
    # 폭포: 북쪽 천장에서 둘, 남쪽 절벽에서 하나
    g = Geo()
    no = plan.cfg["north"]
    for fx in no["fall_x"]:
        yf = float(plan.canopy_front(fx)) - 0.12
        g.box(fx, yf, no["fall_width"] / 2, -0.05, plan.canopy_zb + 0.3, half_y=0.13)
        g.prism(circle(fx, yf - 0.15, no["fall_width"] / 2 + 0.45, 20), -0.05, 0.05)      # 물보라 자리
    g.box(en["fall_x"], plan.y_cliff - 1.25, en["fall_width"] / 2 - 0.15, plan.cliff_base, plan.z_entry + 0.4 if plan.two else -0.5, half_y=0.16)
    g.obj("Waterfalls", mats["fall"], coll)
    return lake


def rail_runs(pts, gaps):
    """난간 줄: 틈(gaps) 에 걸리는 점을 빼고 이어진 구간들로 나눈다 (닫힌 곡선)."""
    n = len(pts)
    ok = [all(math.dist(p, c) > r for c, r in gaps) for p in pts]
    if all(ok):
        return [pts + [pts[0]]]
    start = next(i for i in range(n) if not ok[i])
    runs, cur = [], []
    for k in range(1, n + 1):
        i = (start + k) % n
        if ok[i]:
            cur.append(pts[i])
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def build_ring(plan, mats, coll):
    rg, o = plan.cfg["ring"], plan.oval
    ths, total = o.thetas(plan.t_mid, 0.25)
    inner = [o.pt(plan.t_in, float(a)) for a in ths]
    outer = [o.pt(plan.t_out, float(a)) for a in ths]
    zbot = []
    for a in ths:                         # 북쪽 끝은 물길 위를 건너는 아치
        x, y = o.pt(plan.t_mid, float(a))
        zbot.append(0.12 + 0.6 * math.cos(math.pi * x / 2.6) if (y > 0 and abs(x) < 1.3) else plan.ground - 0.35)
    g = Geo()
    g.ribbon2(inner, outer, plan.zr, zbot, closed=True)
    ring = g.obj("Ring_path", mats["built"], coll, walk=True)

    # 난간(안쪽·바깥쪽)과 등불 기둥
    isl = plan.cfg["island"]
    sgn = 1.0 if isl["bridge_side"] == "right" else -1.0
    gaps_in = [((sgn * (o.a + plan.t_in), isl["y"]), isl["bridge_width"] / 2 + 0.2)]
    gaps_out = [((0.0, -plan.b_out), plan.cfg["entrance"]["stair_width"] / 2 + 0.15)]
    gaps_out += [(r["P"], plan.cfg["rooms_layout"]["deck_width"] / 2 + 0.3) for r in plan.rooms]
    rh, k = rg["rail_height"], max(1, int(round(rg["post_spacing"] / 0.25)))
    g = Geo()
    for t, gaps in ((plan.t_in + 0.1, gaps_in), (plan.t_out - 0.1, gaps_out)):
        pts = [o.pt(t, float(a)) for a in ths]
        for run in rail_runs(pts, gaps):
            g.ribbon(run, plan.zr + rh, plan.zr + rh - 0.11, 0.09)
            for i in list(range(0, len(run), k)) + [len(run) - 1]:
                g.box(run[i][0], run[i][1], 0.06, plan.zr, plan.zr + rh - 0.05)
    g.obj("Ring_rails", mats["built"], coll)

    g = Geo()
    lths, _ = o.thetas(plan.t_out - 0.1, total / max(1, round(total / rg["lantern_spacing"])))
    lh = rg["lantern_height"]
    count = 0
    for a in lths:
        x, y = o.pt(plan.t_out - 0.1, float(a))
        if any(math.dist((x, y), c) < r + 0.45 for c, r in gaps_out):
            continue
        lantern(g, x, y, plan.zr, lh)
        count += 1
    for sx in (() if plan.two else (-1, 1)):  # 한 층 모드의 입구 등불
        hw = plan.cfg["entrance"]["stair_width"] / 2 + 0.12
        for name, (s0, s1, z) in plan.stair_marks.items():
            lantern(g, sx * hw, plan.stair_y0 - (s0 + 0.35 if name == "terrace" else s1 - 0.3), z + 0.55, lh - 0.5)
    g.obj("Lanterns", mats["built"], coll)
    return ring, count


LANTERNS = []   # 등불 기둥의 불 자리 (빛 그림에서 점광원을 둔다)


def lantern(g, x, y, z, h):
    LANTERNS.append((x, y, z + h + 0.19))
    g.box(x, y, 0.07, z, z + h)
    g.box(x, y, 0.2, z + h, z + h + 0.38)
    g.cone(x, y, 0.3, z + h + 0.38, z + h + 0.62, segs=4, a0=math.pi / 4)


def build_room(plan, r, mats, coll):
    rl = plan.cfg["rooms_layout"]
    R, wall, zf = plan.R, plan.wall, r["zf"]
    cx, cy = r["c"]
    d, n = r["d"], r["n"]
    base = plan.ground - 0.3
    g = Geo()
    g.prism(circle(cx, cy, R + 0.03, 72), base, zf)                       # 바닥(통)
    phi_d = math.atan2(-d[1], -d[0])                                      # 방 가운데 → 문
    jamb, dw, dh = rl["door_jamb"], rl["door_width"], rl["door_height"]
    delta = math.asin((dw / 2 + jamb * 0.6) / (R + wall / 2))
    m = 84
    angs = [phi_d + delta + (TAU - 2 * delta) * i / m for i in range(m + 1)]
    inner = [(cx + R * math.cos(a), cy + R * math.sin(a)) for a in angs]
    outer = [(cx + (R + wall) * math.cos(a), cy + (R + wall) * math.sin(a)) for a in angs]
    g.ribbon2(inner, outer, [zf + float(wall_height(rl, a)) for a in angs], base)
    # 아치 문과 문지방
    door_out = v_add(r["P"], d, r["L"])
    g.profile(arch_profile(dw / 2, dh, jamb), door_out, n, d, -0.2, wall + 0.1, zf)
    lat = [(-dw / 2, -0.06), (dw / 2, -0.06), (dw / 2, wall + 0.3), (-dw / 2, wall + 0.3)]
    g.prism([(door_out[0] + n[0] * u + d[0] * s, door_out[1] + n[1] * u + d[1] * s) for u, s in lat], zf - 0.2, zf + 0.02)
    return g.obj(f"Room_{r['key']}", mats["built"], coll, room=r["code"], walk=True)


def build_link(plan, r, mats, coll, skip=False):
    """둘레 길 → (다리) → 계단 또는 나선 계단 → 발코니 → 문. 걸어가는 가운뎃줄도 함께 돌려준다."""
    rl = plan.cfg["rooms_layout"]
    P, d, n, L, zf = r["P"], r["d"], r["n"], r["L"], r["zf"]
    z0, base, dkw = plan.zr, plan.ground - 0.3, rl["deck_width"]

    def at(s, u=0.0):
        return (P[0] + d[0] * s + n[0] * u, P[1] + d[1] * s + n[1] * u)

    def arch_deck(g, s_a, s_b):
        """둘레 길 높이의 짧은 다리 (아래는 얕은 아치)."""
        k = 8 if s_b - s_a > 1.0 else 1
        ss = [s_a + (s_b - s_a) * i / k for i in range(k + 1)]
        zb = [base + (z0 - 0.28 - base) * math.sin(math.pi * i / k) for i in range(k + 1)] if k > 1 else base
        g.ribbon([at(s) for s in ss], z0 - EPS, zb, dkw)
        for side in (-1, 1):
            g.ribbon([at(max(s, 0.25)) for s in ss], z0 + 0.5, z0 - 0.02, 0.1, offset=side * (dkw / 2 - 0.05))

    g = Geo()
    center = r["c"]
    if r["link"] == "stairs":
        bd, bw = rl["balcony_depth"], rl["balcony_width"]
        s_bal = L - bd
        s_st = s_bal - r["steps"] * rl["step_run"]
        arch_deck(g, -0.35, s_st)
        prof = [(s_st, z0)]
        s, z = s_st, z0
        for _ in range(r["steps"]):
            z += rl["step_rise"]
            prof.append((s, z))
            s += rl["step_run"]
            prof.append((s, z))
        xy = [at(s) for s, _ in prof]
        zt = [z for _, z in prof]
        g.ribbon(xy, zt, base, dkw)
        for side in (-1, 1):
            g.ribbon(xy, [z + 0.5 for z in zt], [z - 0.02 for z in zt], 0.1, offset=side * (dkw / 2 - 0.05))
        c = 0.35                                # 발코니 (모서리를 깎은 단)
        poly = [(s_bal, -bw / 2 + c), (s_bal + c, -bw / 2), (L + 0.1, -bw / 2), (L + 0.1, bw / 2), (s_bal + c, bw / 2), (s_bal, bw / 2 - c)]
        g.prism([at(s, u) for s, u in poly], base, zf)
        for side in (-1, 1):
            e = bw / 2 - 0.06
            line = [(s_bal + 0.06, side * (dkw / 2 + 0.02)), (s_bal + 0.06, side * (e - c)), (s_bal + c + 0.03, side * e), (L + 0.12, side * e)]
            g.ribbon([at(s, u) for s, u in line], zf + 0.5, zf - 0.02, 0.1)
        route = [(*at(-0.75, OFF), z0), (*at(s_st, OFF), z0), (*at(s_bal, OFF), zf), (*at(L + plan.wall + 0.6, OFF), zf), (*center, zf)]
    else:
        s_a = r["low"]
        sig, cc = r["sigma"], r["tower"]
        r_in, r_out, m = rl["spiral_r_in"], rl["spiral_r_out"], r["steps"]
        r_mid = (r_in + r_out) / 2
        rise = r["rise"] / m

        def helix(phi, rad):
            c, s = math.cos(phi), math.sin(phi)
            return (cc[0] + rad * (-sig * n[0] * c + d[0] * s), cc[1] + rad * (-sig * n[1] * c + d[1] * s))

        arch_deck(g, -0.35, s_a + 0.15)
        for i in range(m):                      # 부채꼴 디딤판
            a0, a1 = TAU * i / m, TAU * (i + 1) / m
            arc = [a0 + (a1 - a0) * k / 4 for k in range(5)]
            poly = [helix(a, r_out) for a in arc] + [helix(a, r_in) for a in reversed(arc)]
            top = z0 + (i + 1) * rise
            g.prism(poly, top - 0.18, top)
            px, py = helix((a0 + a1) / 2, r_out - 0.07)
            g.box(px, py, 0.05, top, top + 0.55)
        g.prism(circle(cc[0], cc[1], r_in + 0.07, 16), base, zf + 0.9)      # 가운데 기둥
        g.cone(cc[0], cc[1], r_in + 0.16, zf + 0.9, zf + 1.35, segs=16)
        hw = 0.8                                # 위층 발코니와 받침
        g.prism([at(s_a, -hw), at(L + 0.1, -hw), at(L + 0.1, hw), at(s_a, hw)], zf - 0.22, zf)
        g.prism([at(L - 0.75, -hw), at(L + 0.1, -hw), at(L + 0.1, hw), at(L - 0.75, hw)], base, zf - 0.22)
        for side in (-1, 1):
            g.ribbon([at(s_a + 0.05, side * (hw - 0.06)), at(L + 0.12, side * (hw - 0.06))], zf + 0.5, zf - 0.02, 0.1)
        route = [(*at(-0.75, OFF), z0), (*at(s_a), z0)]
        route += [(*helix(TAU * k / 96, r_mid), z0 + r["rise"] * k / 96) for k in range(1, 97)]
        route += [(*at(L + plan.wall + 0.6, OFF), zf), (*center, zf)]
    if not skip:
        g.obj(f"Link_{r['key']}", mats["built"], coll, walk=True)
    return route


def build_island(plan, mats, coll):
    isl, o = plan.cfg["island"], plan.oval
    cy, rp, zp = isl["y"], isl["platform_radius"], isl["platform_z"]
    bed = plan.cfg["lake"]["bed_z"]
    code = plan.island_code
    # 바위섬
    rings, segs = 14, 56
    verts, faces = [(0.0, cy, zp - 0.04)], []
    for k in range(1, rings + 1):
        rad = (rp + 1.5) * k / rings
        for j in range(segs):
            a = TAU * j / segs
            wob = 1.0 + 0.09 * math.sin(3 * a + 0.5) + 0.06 * math.sin(7 * a + 1.9)
            fall = float(smoothstep(rp * wob, (rp + 1.45) * wob, rad))
            z = (zp - 0.04) + (bed - zp) * fall ** 0.75 + 0.1 * math.sin(5 * a + rad * 3.0) * fall
            verts.append((rad * math.cos(a), cy + rad * math.sin(a), z))
    for j in range(segs):
        faces.append((0, 1 + j, 1 + (j + 1) % segs))
    for k in range(rings - 1):
        for j in range(segs):
            a, b = 1 + k * segs + j, 1 + k * segs + (j + 1) % segs
            faces.append((a, a + segs, b + segs, b))
    g = Geo()
    g._add(verts, faces)
    g.obj("Island_rock", mats["rock"], coll, room=code, smooth=True, fix=False)
    # 신전: 단 · 기둥 · 고리 들보 · 수정 첨탑
    g = Geo()
    g.prism(circle(0, cy, rp - 0.15, 32), zp - 0.6, zp)
    g.prism(circle(0, cy, 1.25, 32), zp, zp + 0.2)
    g.prism(circle(0, cy, 0.95, 32), zp + 0.2, zp + 0.4)
    top = zp + 0.4
    for j in range(6):
        a = TAU * (j + 0.5) / 6
        g.prism(circle(0.78 * math.cos(a), cy + 0.78 * math.sin(a), 0.1, 10), top, top + 1.45)
    g.ribbon(circle(0, cy, 0.78, 32), top + 1.7, top + 1.45, 0.3, closed=True)
    sh = isl["spire_height"]
    g.prism(circle(0, cy, 0.3, 6), top, top + sh * 0.62)
    g.cone(0, cy, 0.3, top + sh * 0.62, top + sh, segs=6)
    for j in range(5):
        a = TAU * j / 5 + 0.4
        px, py = 0.42 * math.cos(a), cy + 0.42 * math.sin(a)
        g.cone(px, py, 0.17, top, top + 1.0 + 0.25 * (j % 3), segs=6, tip=(0.28 * math.cos(a), 0.28 * math.sin(a)))
    g.obj("Island_temple", mats["built"], coll, room=code, walk=True)
    # 아치 다리: 섬 ↔ 둘레 길
    sgn = 1.0 if isl["bridge_side"] == "right" else -1.0
    x0, x1 = rp - 0.35, o.a + plan.t_in + 0.15
    k = 16
    xy, zt, zb = [], [], []
    for i in range(k + 1):
        f = i / k
        xy.append((sgn * (x0 + (x1 - x0) * f), cy))
        zt.append(zp + (plan.zr - zp) * f + isl["bridge_hump"] * math.sin(math.pi * f) - EPS)
        zb.append(min(zt[-1] - 0.22, -0.3 + 1.25 * math.sin(math.pi * min(1.0, max(0.0, (f - 0.08) / 0.84))) ** 0.6))
    g = Geo()
    bw = isl["bridge_width"]
    g.ribbon(xy, zt, zb, bw)
    for side in (-1, 1):
        g.ribbon(xy[1:-1], [z + 0.5 for z in zt[1:-1]], [z - 0.02 for z in zt[1:-1]], 0.1, offset=side * (bw / 2 - 0.05))
    g.obj("Island_bridge", mats["built"], coll, room=code, walk=True)
    yy = cy + OFF
    return [(sgn * (o.a + plan.t_mid), yy, plan.zr), (sgn * x1, yy, plan.zr), (sgn * (x0 + x1) / 2, yy, (zp + plan.zr) / 2 + isl["bridge_hump"]),
            (sgn * x0, yy, zp), (sgn * 1.4, yy, zp)]


def build_entrance(plan, mats, coll):
    en = plan.cfg["entrance"]
    sw = en["stair_width"]
    y0 = plan.stair_y0
    xy = [(0.0, y0 - s) for s, _ in plan.stair_prof]
    zt = [z for _, z in plan.stair_prof]
    g = Geo()
    g.ribbon(xy, [z - EPS for z in zt], [z - 0.45 for z in zt], sw)
    for side in (-1, 1):                      # 계단 옆 낮은 벽
        g.ribbon(xy[1:], [z + 0.55 for z in zt[1:]], [z - 0.45 for z in zt[1:]], 0.24, offset=side * (sw / 2 + 0.12))
    g.obj("Entrance_stairs", mats["built"], coll, walk=True)
    # 대문 아치 (계단참)
    g = Geo()
    s0, s1, z = plan.stair_marks["landing0"]
    gy = y0 - (s0 + s1) / 2
    gh = en["gate_height"]
    g.profile(arch_profile(1.25, gh - 0.95, sw / 2 + 0.24 - 1.25, flat_top=gh), (0.0, gy), (1.0, 0.0), (0.0, 1.0), -0.3, 0.3, z)
    for sx in (-1, 1):                        # 문루 양 끝의 작은 탑
        tx = sx * (sw / 2 - 0.15)
        g.box(tx, gy, 0.36, z + gh, z + gh + 0.45)
        g.cone(tx, gy, 0.52, z + gh + 0.45, z + gh + 1.0, segs=4, a0=math.pi / 4)
    for k in range(-2, 3):                    # 문루 위 총안
        g.box(k * 0.5, gy, 0.14, z + gh, z + gh + 0.22, half_y=0.3)
    g.obj("Entrance_gate", mats["built"], coll)
    # 동굴 입구 (거친 바위 아치)
    g = Geo()
    rng = np.random.default_rng(plan.cfg["terrain"]["seed"] + 5)
    rough = list(rng.uniform(-0.07, 0.09, 13))
    g.profile(arch_profile(1.55, en["mouth_height"], sw / 2 + 0.75 - 1.55 + 0.3, rough=rough), (0.0, plan.y_mouth), (1.0, 0.0), (0.0, 1.0), -0.6, 0.6, plan.z_entry - 0.3)
    # 입구 옆 폭포가 나오는 굴
    g.profile(arch_profile(0.8, 1.5, 0.45, rough=rough), (en["fall_x"], plan.y_cliff + 3.6), (1.0, 0.0), (0.0, 1.0), -0.4, 0.5, -0.95)
    g.obj("Entrance_mouth", mats["rock"], coll)
    return [(OFF, plan.y_mouth - 0.45, plan.z_entry)] + [(OFF, y0 - s, z) for s, z in reversed(plan.stair_prof)] + [(OFF, -(plan.oval.b + plan.t_mid), plan.zr)]


def build_north(plan, mats, coll):
    """북쪽 끝의 천장 조각(앞 가장자리) · 종유석."""
    no = plan.cfg["north"]
    rng = np.random.default_rng(plan.cfg["terrain"]["seed"] + 9)
    xs = np.arange(-plan.u_half_view - 1.0, plan.u_half_view + 1.25, 0.25)
    yf = plan.canopy_front(xs)
    y_back = (plan.v_max + 2.0 - plan.cv * plan.canopy_zt) / plan.sv
    zt = plan.canopy_zt + 0.35 * np.sin(0.7 * xs) + 0.2 * np.sin(1.9 * xs + 1.0)
    zb = plan.canopy_zb + 0.25 * np.sin(1.1 * xs + 2.0)
    g = Geo()
    left = [(float(x), float(y_back)) for x in xs]
    right = [(float(x), float(y)) for x, y in zip(xs, yf)]
    g.ribbon2(right, left, [float(z) for z in zt], [float(z) for z in zb])
    for _ in range(no["stalactites"]):
        x = float(rng.uniform(xs[0] + 0.5, xs[-1] - 0.5))
        if any(abs(x - fx) < no["fall_width"] / 2 + 0.45 for fx in no["fall_x"]):
            continue
        y = float(plan.canopy_front(x)) + float(rng.uniform(0.12, 0.7))
        rad, ln = float(rng.uniform(0.2, 0.48)), float(rng.uniform(1.4, 3.6))
        zb_here = plan.canopy_zb + 0.25 * math.sin(1.1 * x + 2.0)
        g.cone(x, y, rad, zb_here + 0.15, zb_here - ln, segs=8)
    g.obj("Ceiling", mats["rock"], coll, smooth=False)


def build_rocks(plan, mats, coll):
    """호수의 바위 송곳들(석순)."""
    tr, isl = plan.cfg["terrain"], plan.cfg["island"]
    rng = np.random.default_rng(tr["seed"] + 3)
    g = Geo()
    made, tries = 0, 0
    while made < tr["lake_rocks"] and tries < 4000:
        tries += 1
        x = float(rng.uniform(-plan.oval.a, plan.oval.a))
        y = float(rng.uniform(-plan.oval.b, plan.oval.b))
        if float(plan.oval.tcoord(np.array([x]), np.array([y]))[0]) > -0.9:
            continue
        if math.hypot(x, y - isl["y"]) < isl["platform_radius"] + 2.2 or (abs(y - isl["y"]) < 1.6 and abs(x) < plan.oval.a + 1):
            continue
        rad, hh = float(rng.uniform(0.3, 0.6)), float(rng.uniform(0.9, 2.3))
        g.cone(x, y, rad, -0.5, hh, segs=7, tip=(float(rng.uniform(-0.12, 0.12)), float(rng.uniform(-0.12, 0.12))))
        if rng.random() < 0.6:
            g.cone(x + rad * 0.9, y - rad * 0.4, rad * 0.6, -0.5, hh * 0.5, segs=6)
        made += 1
    g.obj("Lake_rocks", mats["rock"], coll, room=plan.island_code)


# ==========================================================================
# 재질 · 조명 · 카메라
# ==========================================================================
def principled(name, color, rough):
    mat = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    mat.diffuse_color = (*color, 1.0)
    return mat


def emission_material(name, build):
    """뷰 레이어 전체에 덮어씌울 방출 재질 (데이터 패스용)."""
    mat = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    build(nt, em)
    return mat


def make_materials():
    return {
        "built": principled("Clay_built", (0.72, 0.72, 0.72), 0.85),
        "rock": principled("Clay_rock", (0.42, 0.42, 0.43), 0.95),
        "water": principled("Clay_water", (0.2, 0.25, 0.31), 0.18),
        "fall": principled("Clay_fall", (0.82, 0.86, 0.9), 0.4),
    }


def setup_camera(plan, scene):
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = plan.ortho
    cam_data.clip_start, cam_data.clip_end = 1.0, 600.0
    cam = bpy.data.objects.new("Camera", cam_data)
    v_c = (plan.v_min + plan.v_max) / 2
    look = Vector((0.0, plan.cv, -plan.sv))             # 보는 방향
    cam.location = Vector((0.0, v_c / plan.sv, 0.0)) - look * 200.0
    cam.rotation_euler = (math.pi / 2 - plan.elev, 0.0, 0.0)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def setup_light(scene):
    sun_data = bpy.data.lights.new("Sun", type="SUN")
    sun_data.energy = 1.4
    sun_data.angle = math.radians(7)
    sun = bpy.data.objects.new("Sun", sun_data)
    sun.rotation_euler = Vector((0.5, 0.42, -0.78)).to_track_quat("-Z", "Y").to_euler()   # 왼쪽 앞 위에서
    scene.collection.objects.link(sun)
    world = bpy.data.worlds.new("World")
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.9, 0.92, 1.0, 1.0)
    bg.inputs["Strength"].default_value = 0.42
    scene.world = world
    return world


# ==========================================================================
# 검사: 걸어서 닿는지, 겹치는지
# ==========================================================================
def walk_test(scene, deps, name, coarse, first_obj=None):
    pts = resample(coarse, 0.05)
    prev = None
    for i, (x, y, z) in enumerate(pts):
        for jx, jy in ((0.0, 0.0), (0.011, 0.007), (-0.009, 0.012)):   # 두 덩어리가 맞닿은 이음매 위에 정확히 떨어지면 조금 비켜 다시 쏜다
            ok, loc, _n, _i, ob, _m = scene.ray_cast(deps, Vector((x + jx, y + jy, z + 0.5)), Vector((0, 0, -1)), distance=1.3)
            if ok:
                break
        where = f"({x:.2f}, {y:.2f}, {z:.2f})"
        if not ok:
            return {"ok": False, "why": f"{where} 아래에 바닥이 없다"}
        if not ob.get("walk"):
            return {"ok": False, "why": f"{where} 에서 걷는 면이 아닌 {ob.name} 을 밟는다"}
        if i == 0 and first_obj and ob.name != first_obj:
            return {"ok": False, "why": f"출발점이 {first_obj} 이 아니라 {ob.name}"}
        if abs(loc.z - z) > 0.32:
            return {"ok": False, "why": f"{where} 바닥 높이가 {loc.z:.2f} (예상 {z:.2f})"}
        if prev is not None:
            if abs(loc.z - prev.z) > 0.27:
                return {"ok": False, "why": f"{where} 에서 {abs(loc.z - prev.z):.2f} 높이 턱"}
            seg = loc - prev
            if seg.length > 1e-4:
                for hgt in (0.38, 0.66, 1.3):
                    hit, hl, _n2, _i2, hob, _m2 = scene.ray_cast(deps, prev + Vector((0, 0, hgt)), seg.normalized(), distance=seg.length)
                    if hit:
                        return {"ok": False, "why": f"{where} 높이 {hgt} 에서 {hob.name} 에 막힌다"}
        prev = loc
    return {"ok": True, "samples": len(pts), "length": round(sum(math.dist(a, b) for a, b in zip(coarse[:-1], coarse[1:])), 2)}


def overlap_checks(plan):
    out = {}
    rr = plan.R + plan.wall
    gaps = []
    for i, a in enumerate(plan.rooms):
        for b in plan.rooms[i + 1:]:
            gaps.append((round(math.dist(a["c"], b["c"]) - 2 * rr, 3), a["key"], b["key"]))
    gaps.sort()
    out["room_room_min_gap"] = {"gap": gaps[0][0], "pair": [gaps[0][1], gaps[0][2]]}
    ring_gap = []
    for r in plan.rooms:
        t = float(plan.oval.tcoord(np.array([r["c"][0]]), np.array([r["c"][1]]))[0])
        ring_gap.append((round(t - plan.t_out - rr, 3), r["key"]))
    ring_gap.sort()
    out["room_ring_min_gap"] = {"gap": ring_gap[0][0], "room": ring_gap[0][1]}
    isl = plan.cfg["island"]
    out["island_water_gap"] = round(plan.oval.a - (isl["platform_radius"] + 0.6), 3)
    half = plan.cfg["entrance"]["stair_width"] / 2 + 0.75
    canyon = min(max(abs(r["c"][0]) - half, r["c"][1] - plan.stair_y0) - rr for r in plan.rooms)
    out["room_entrance_min_gap"] = round(canyon, 3)
    out["ok"] = gaps[0][0] > 0.05 and ring_gap[0][0] > 0.3 and canyon > 0.3 and out["island_water_gap"] > 0.5
    return out


# ==========================================================================
# 렌더 · 파일 쓰기
# ==========================================================================
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


def load_float(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    bpy.data.images.remove(img)
    return buf.reshape(h, w, 4)[::-1]


def render(scene, path):
    t0 = time.perf_counter()
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return time.perf_counter() - t0


def render_data_pass(scene, mat, path, scale):
    """방출 재질을 덮어씌워 1샘플 · 필터 없음으로 32비트 EXR 을 굽는다 (픽셀 하나 = 면 하나)."""
    r, c = scene.render, scene.cycles
    bpy.context.view_layer.material_override = mat
    c.samples, c.use_adaptive_sampling, c.use_denoising = 1, False, False
    c.pixel_filter_type, c.filter_width = "BOX", 0.01
    c.max_bounces = 0
    r.resolution_percentage = int(round(100 * scale))
    r.image_settings.file_format = "OPEN_EXR"
    r.image_settings.color_mode = "RGB"
    r.image_settings.color_depth = "32"
    secs = render(scene, path)
    bpy.context.view_layer.material_override = None
    return secs


# ==========================================================================
# 시간대 빛 그림 (--light): 칠한 지도 위에 겹쳐 시간대를 바꾸는 곱하기 · 더하기 그림
# ==========================================================================
def gaussian_blur(img, sigma):
    if sigma <= 0:
        return img
    r = int(math.ceil(sigma * 3))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    out = img.astype(np.float64)
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (r, r)
        src = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        for i, kv in enumerate(k):
            sl = [slice(None)] * out.ndim
            sl[axis] = slice(i, i + out.shape[axis])
            acc += kv * src[tuple(sl)]
        out = acc
    return out


def save_webp(path, rgb, target_kb=None):
    """(H, W, 3) 0~1 화면값 → WebP. target_kb 를 주면 그 크기에 들어오는 가장 높은 품질을 고른다."""
    h, w = rgb.shape[:2]
    img = bpy.data.images.new("webp_out", w, h, alpha=False, float_buffer=False)
    buf = np.ones((h, w, 4), dtype=np.float32)
    buf[:, :, :3] = np.clip(rgb[::-1], 0.0, 1.0)
    img.pixels.foreach_set(buf.ravel())
    img.file_format = "WEBP"
    quality = 90
    for quality in (96, 93, 90, 86, 82, 78, 72, 66, 58, 50, 40):
        img.save(filepath=path, quality=quality)
        if target_kb is None or os.path.getsize(path) <= target_kb * 1024:
            break
    bpy.data.images.remove(img)
    return quality, os.path.getsize(path)


def load_display(path_or_url, cache_dir):
    """칠한 지도(파일이나 주소) → (H, W, 3) 0~1 화면값."""
    path = path_or_url
    if path_or_url.startswith("http"):
        import urllib.request
        path = os.path.join(cache_dir, os.path.basename(path_or_url.split("?")[0]))
        if not os.path.isfile(path):
            req = urllib.request.Request(path_or_url, headers={"User-Agent": "cave-hub"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(path, "wb") as f:
                f.write(resp.read())
    return load_float(path)[:, :, :3].astype(np.float64)


def light_dir(elevation_deg, azimuth_deg):
    """빛이 오는 쪽(고도, 방위: +X 에서 반시계)으로 빛이 나아가는 방향 벡터."""
    el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
    return Vector((-math.cos(el) * math.cos(az), -math.cos(el) * math.sin(az), -math.sin(el)))


def add_light(scene, kind, name, loc, color, energy, radius=0.2, target=None, cone_deg=30.0, blend=0.6):
    data = bpy.data.lights.new(name, type=kind)
    data.energy, data.color, data.shadow_soft_size = energy, color, radius
    if kind == "SPOT":
        data.spot_size, data.spot_blend = math.radians(cone_deg), blend
    ob = bpy.data.objects.new(name, data)
    ob.location = loc
    if target is not None:
        ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(ob)
    ob.hide_render = True
    return ob


def render_light(plan, scene, cam, cfg, args, out_path, report):
    """시간대마다 곱하기(mul) · 더하기(glow) · 미리보기를 굽는다.

    mul  = 그 시간대의 자연광 ÷ 낮 조명(구조 그림의 Sun + world)이 열린 바닥에 주는 밝기. 흰색 = 그대로.
           낮의 해가 드리운 그림자는 칠한 지도에 없으므로 기준에 넣지 않는다(넣으면 그림자가 거꾸로 찍힌다).
    glow = 빛을 내는 것만: 등불 · 문 앞 불빛 · 달빛(밤), 빛줄기(아침), 낮게 깔린 안개(새벽). 검정 = 없음.
    두 그림 모두 화면값(감마 2.2)이라 칠한 지도에 곱하고 → 더하면 된다.
    """
    lc = cfg["light"]
    names = list(lc["times"]) if args.light == "all" else [n.strip() for n in args.light.split(",")]
    for n in names:
        if n not in lc["times"]:
            raise SystemExit(f"--light {n}: 설정에 없는 시간대 (있는 것: {', '.join(lc['times'])})")
    W_full, H_full = cfg["image"]["width"], cfg["image"]["height"]
    scale = lc["width"] / W_full
    w, h = lc["width"], int(round(H_full * scale))
    albedo, sigma = lc["albedo"], lc["blur_px"]
    tmp = os.path.join(bpy.app.tempdir, "cave_light")
    os.makedirs(tmp, exist_ok=True)
    r, cyc = scene.render, scene.cycles
    r.engine, cyc.device = "CYCLES", "CPU"
    scene.view_settings.view_transform, scene.view_settings.look = "Standard", "None"
    sun = bpy.data.objects["Sun"]
    bg = scene.world.node_tree.nodes["Background"]

    # 낮 기준: 구조 그림의 조명이 가리는 것 없는 수평 바닥에 주는 밝기 (해는 세기 × sin(고도) ÷ π)
    day_dir = sun.rotation_euler.to_matrix() @ Vector((0.0, 0.0, -1.0))
    day_world = np.array(bg.inputs["Color"].default_value[:3]) * bg.inputs["Strength"].default_value
    k_day = day_world + np.array(sun.data.color[:]) * sun.data.energy * abs(day_dir.z) / math.pi
    log(f"빛 그림: {names}, {w}x{h}, 낮 기준 밝기 {np.round(k_day, 3).tolist()}")

    # (0) 위치 패스: 픽셀마다 세계 좌표 (안개 · 확인용)
    def mat_position(nt, em):
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        mad = nt.nodes.new("ShaderNodeVectorMath")
        mad.operation = "MULTIPLY_ADD"
        mad.inputs[1].default_value = (1 / 240.0, 1 / 240.0, 1 / 80.0)
        mad.inputs[2].default_value = (0.5, 0.5, 0.5)
        nt.links.new(geo.outputs["Position"], mad.inputs[0])
        nt.links.new(mad.outputs["Vector"], em.inputs["Color"])

    saved_strength = bg.inputs["Strength"].default_value
    bg.inputs["Strength"].default_value = 0.0
    render_data_pass(scene, emission_material("Pass_position", mat_position), os.path.join(tmp, "pos.exr"), scale)
    pos = load_float(os.path.join(tmp, "pos.exr"))[:, :, :3].astype(np.float64)
    hit = pos.sum(axis=2) > 1e-4
    wx, wy, wz = (pos[:, :, 0] - 0.5) * 240.0, (pos[:, :, 1] - 0.5) * 240.0, (pos[:, :, 2] - 0.5) * 80.0

    # 조명 패스 공통 설정: 회색 무광 점토, 32비트 EXR
    clay = principled("Light_clay", (albedo, albedo, albedo), 1.0)
    black = principled("Light_black", (0.0, 0.0, 0.0), 1.0)
    for mat in (clay, black):
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        if "Specular IOR Level" in bsdf.inputs:
            bsdf.inputs["Specular IOR Level"].default_value = 0.0
    r.resolution_percentage = int(round(100 * scale))
    r.image_settings.file_format, r.image_settings.color_mode, r.image_settings.color_depth = "OPEN_EXR", "RGB", "32"
    cyc.samples, cyc.use_adaptive_sampling = lc["samples"], True
    cyc.pixel_filter_type, cyc.filter_width = "BLACKMAN_HARRIS", 1.5
    cyc.max_bounces, cyc.volume_bounces = 4, 0
    try:
        import _cycles
        cyc.use_denoising = bool(getattr(_cycles, "with_openimagedenoise", False))
    except ImportError:
        cyc.use_denoising = False

    def shot(tag, lights, surface=None, bounces=4):
        """주어진 광원만 켜고, 모든 표면을 한 재질(기본 회색 점토)로 바꿔 굽는다 → (H, W, 3) 선형 밝기."""
        for ob in scene.objects:
            if ob.type == "LIGHT":
                ob.hide_render = ob not in lights
        meshes = [ob for ob in scene.objects if ob.type == "MESH" and ob.name != "Light_haze"]
        kept = [ob.data.materials[0] for ob in meshes]
        for ob in meshes:
            ob.data.materials[0] = surface or clay
        cyc.max_bounces = bounces
        path = os.path.join(tmp, f"{tag}.exr")
        secs = render(scene, path)
        for ob, mat in zip(meshes, kept):
            ob.data.materials[0] = mat
        return load_float(path)[:, :, :3].astype(np.float64), secs

    # 빛줄기용 엷은 연무 상자 (아침): 표면은 검게, 빛이 연무에 흩어진 것만 남긴다
    haze_mat = bpy.data.materials.new("Light_haze")
    if bpy.app.version < (5, 0, 0):
        haze_mat.use_nodes = True
    nt = haze_mat.node_tree
    nt.nodes.clear()
    scatter = nt.nodes.new("ShaderNodeVolumeScatter")
    nt.links.new(scatter.outputs["Volume"], nt.nodes.new("ShaderNodeOutputMaterial").inputs["Volume"])
    xs, ys, zs = wx[hit], wy[hit], wz[hit]
    g = Geo()
    g.box((xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2, (xs.max() - xs.min()) / 2 + 3, zs.min() - 1, zs.max() + 8,
          half_y=(ys.max() - ys.min()) / 2 + 3)
    haze = g.obj("Light_haze", haze_mat, scene.collection)
    haze.hide_render = True

    falls = bpy.data.objects.get("Waterfalls")
    fall_pts = np.array([falls.matrix_world @ v.co for v in falls.data.vertices]) if falls else np.zeros((0, 3))
    low = plan.lower if plan.two else None
    doors = [(v_add(v_add(rm["P"], rm["d"], rm["L"]), rm["d"], -0.7), rm["zf"] + 1.5) for rm in plan.rooms + (plan.lower_rooms if plan.two else [])]
    base_img = None
    out = report.setdefault("light", {})

    for name in names:
        tc = lc["times"][name]
        t0 = time.perf_counter()
        made = []
        # ---- 곱하기: 자연광(해 · 달 · 들이치는 햇살)이 표면에 주는 밝기 ÷ 낮 기준 ----
        sc = tc.get("sun")
        natural = []
        if sc:
            sun.data.energy, sun.data.color = sc["energy"], sc["color"]
            sun.data.angle = math.radians(sc.get("angle_deg", 7))
            sun.rotation_euler = light_dir(sc["elevation_deg"], sc["azimuth_deg"]).to_track_quat("-Z", "Y").to_euler()
            natural.append(sun)
        beams = []
        for k, bc in enumerate(tc.get("beams", [])):            # 들이치는 빛: 구조에서 자리를 잡는다
            if bc["where"] == "north":                           # 북쪽 천장 폭포 쪽에서 호수로
                x = bc["x"]
                src = (x * 0.6, plan.canopy_y + bc.get("back", 3.0), plan.canopy_zt + bc.get("up", 5.0))
                dst = (x, plan.oval.b - bc["reach"], 0.0)
            elif bc["where"] == "entrance" and low:              # 입구(왼쪽 아래 폭포 옆)에서 홀로
                ex, ey, ez = low["entrance"]
                src = (ex - 3.0 + bc["x"], ey - 4.0, ez + bc.get("up", 9.0))
                dst = (bc["x"], low["hall_y"] + bc.get("reach", 0.0), low["z"])
            else:
                continue
            beams.append(add_light(scene, "SPOT", f"beam_{name}_{k}", src, bc["color"], bc["power"], radius=0.6,
                                   target=dst, cone_deg=bc["cone_deg"], blend=bc.get("blend", 0.7)))
        spots = []
        for k, sp in enumerate(tc.get("fall_light", [])):        # 폭포를 비추는 빛(달빛): 폭포 조각마다 하나
            if not len(fall_pts):
                break
            groups = [fall_pts[(fall_pts[:, 1] > 0) & (fall_pts[:, 0] < 0)], fall_pts[(fall_pts[:, 1] > 0) & (fall_pts[:, 0] >= 0)], fall_pts[fall_pts[:, 1] <= 0]]
            for j, pts in enumerate(groups):
                if not len(pts):
                    continue
                c = pts.mean(axis=0)
                top = pts[:, 2].max()
                low_z = pts[:, 2].min()
                src = (c[0], c[1] - 11.0, top + 6.0)
                spots.append(add_light(scene, "SPOT", f"fall_{name}_{k}_{j}", src, sp["color"], sp["power"], radius=1.0,
                                       target=(c[0], c[1], max(low_z, top - 9.0) + 2.5), cone_deg=sp["cone_deg"], blend=0.9))
        direct = np.zeros((h, w, 3))
        secs = 0.0
        if natural or beams or (spots and tc.get("fall_in_mul", True)):
            direct, secs = shot(f"{name}_mul", natural + beams + (spots if tc.get("fall_in_mul", True) else []))
        ambient = np.array(tc["world"]) * tc["world_strength"]
        ratio = (ambient[None, None, :] + direct / albedo) / k_day[None, None, :]
        mul = np.clip(ratio, 0.0, 1.0) ** (1 / 2.2)
        mul[~hit] = np.clip(ambient / k_day, 0, 1) ** (1 / 2.2)
        mul = gaussian_blur(mul, sigma)

        # ---- 더하기: 빛을 내는 것만 ----
        glow_lin = np.zeros((h, w, 3))
        gc = tc.get("glow", {})
        emitters = []
        if gc.get("lanterns"):
            lcfg = gc["lanterns"]
            emitters += [add_light(scene, "POINT", f"lantern_{name}_{k}", (x, y, z + lcfg.get("lift", 0.6)), lcfg["color"], lcfg["power"], radius=0.25)
                         for k, (x, y, z) in enumerate(LANTERNS)]
        if gc.get("doors"):
            dcfg = gc["doors"]
            emitters += [add_light(scene, "POINT", f"door_{name}_{k}", (x, y, z), dcfg["color"], dcfg["power"], radius=0.3)
                         for k, ((x, y), z) in enumerate(doors)]
        if gc.get("fall_gain") and spots:
            for ob in spots:
                ob.data.energy *= gc["fall_gain"]
            emitters += spots
        if emitters:
            lit, s2 = shot(f"{name}_glow", emitters, bounces=gc.get("bounces", 1))
            glow_lin += lit / albedo * gc.get("gain", 1.0)
            secs += s2
        if gc.get("shafts") and beams:                           # 빛줄기: 연무에 흩어진 빛
            sh = gc["shafts"]
            scatter.inputs["Density"].default_value = sh["density"]
            scatter.inputs["Anisotropy"].default_value = sh.get("anisotropy", 0.3)
            haze.hide_render = False
            saved = cyc.samples
            cyc.samples = sh.get("samples", lc["samples"])
            lit, s2 = shot(f"{name}_shafts", beams, surface=black, bounces=0)
            cyc.samples = saved
            haze.hide_render = True
            glow_lin += lit * sh.get("gain", 1.0)
            secs += s2
        if gc.get("fog"):                                        # 낮게 깔린 안개: 수면 · 아래층 바닥에서의 높이로 셈한다
            fc = gc["fog"]
            wisp = np.clip(0.5 + 0.8 * fbm(wx + 517.0, wy + 731.0, fc.get("seed", 11), fc.get("scale", 0.16), 3), 0.0, 1.0)   # 결이 있는 안개
            t_lake = plan.oval.tcoord(wx, wy)
            near_lake = 1.0 - smoothstep(0.2, fc.get("spill", 2.2), t_lake)
            dens = near_lake * np.exp(-np.clip(wz, 0.0, None) / fc["height"]) * (wz > -2.0)
            pool = 1.0 - smoothstep(plan.pool_r, plan.pool_r + 2.0, np.hypot(wx, wy - plan.pool_y))
            dens = np.maximum(dens, pool * np.exp(-np.clip(wz, 0.0, None) / fc["height"]) * (wz > -2.0))
            if low:
                near_hall = 1.0 - smoothstep(low["hall_radius"] + 1.0, low["hall_radius"] + fc.get("hall_spill", 7.0), np.hypot(wx, wy - low["hall_y"]))
                dens = np.maximum(dens, near_hall * np.exp(-np.clip(wz - low["z"], 0.0, None) / fc["height"]) * (wz > low["z"] - 1.5))
            glow_lin += (dens * wisp * hit)[:, :, None] * np.array(fc["color"])[None, None, :] * fc["strength"]
        # 화면값으로: 어두운 바탕 위에 더하므로 희미한 빛이 뿌옇게 번지지 않게 바닥(floor)을 빼고 감마를 조금 낮춘다
        floor = gc.get("floor", 0.0)
        glow = np.clip((glow_lin - floor) / (1.0 - floor), 0.0, 1.0) ** (1 / gc.get("gamma", 2.2))
        glow = gaussian_blur(glow, sigma)
        for lname in {ob.name for ob in beams + spots + emitters}:   # 다음 시간대에 남지 않게 지운다
            data = bpy.data.objects[lname].data
            bpy.data.objects.remove(bpy.data.objects[lname])
            bpy.data.lights.remove(data)

        info = {"render_sec": round(secs, 1)}
        for kind, arr in (("mul", mul), ("glow", glow)):
            path = out_path(f"cave-light-{name}-{kind}.webp")
            q, size = save_webp(path, arr, lc["target_kb"])
            info[kind] = {"file": os.path.basename(path), "quality": q, "kb": round(size / 1024, 1),
                          "mean": [round(float(v), 3) for v in arr.reshape(-1, 3).mean(axis=0)]}
            made.append(path)
        # 확인용 미리보기: 칠한 지도에 곱하고 → 더한다
        base_src = args.light_base or lc.get("base")
        if base_src:
            if base_img is None:
                full = load_display(base_src, tmp)
                fh, fw = full.shape[:2]
                if fw % w or fh % h or fw // w != fh // h:
                    raise SystemExit(f"칠한 지도 크기 {fw}x{fh} 가 빛 그림 {w}x{h} 의 정수배가 아니다")
                f = fw // w
                base_img = full.reshape(h, f, w, f, 3).mean(axis=(1, 3))
            path = out_path(f"cave-light-{name}-preview.webp")
            q, size = save_webp(path, np.clip(base_img * mul + glow, 0.0, 1.0), lc.get("preview_kb"))
            info["preview"] = {"file": os.path.basename(path), "quality": q, "kb": round(size / 1024, 1)}
        info["total_sec"] = round(time.perf_counter() - t0, 1)
        out[name] = info
        log(f"  {name}: mul 평균 {info['mul']['mean']} ({info['mul']['kb']}KB q{info['mul']['quality']}), "
            f"glow 평균 {info['glow']['mean']} ({info['glow']['kb']}KB q{info['glow']['quality']}), 렌더 {secs:.1f}s")
    bg.inputs["Strength"].default_value = saved_strength


def cubic_path(points):
    """점들을 지나는 매끄러운 SVG path (Catmull-Rom → 3차 베지어)."""
    p = [np.array(q, dtype=float) for q in points]
    parts = [f"M {p[0][0]:.1f} {p[0][1]:.1f} C"]
    for i in range(len(p) - 1):
        a, b = p[i], p[i + 1]
        before = p[i - 1] if i > 0 else a
        after = p[i + 2] if i + 2 < len(p) else b
        c1, c2 = a + (b - before) / 6.0, b - (after - a) / 6.0
        parts.append(f"{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {b[0]:.1f} {b[1]:.1f}")
    return " ".join(parts)


# ==========================================================================
# 두 층: 공유 배치로 지형, 실제 보행면, 내보낼 경로를 함께 만든다
# ==========================================================================
def lower_entrance(plan):
    low = plan.lower
    a = math.radians(low["entrance_angle_deg"])
    end = (low["path_radius"] * math.cos(a), low["hall_y"] + low["path_radius"] * math.sin(a), low["z"])
    start = tuple(low["entrance"])
    d = v_norm((end[0] - start[0], end[1] - start[1]))
    length = math.dist(start[:2], end[:2])
    steps = int(round((end[2] - start[2]) / 0.2))
    route = [start, (*v_add(start, d, 1.4), start[2])]
    for i in range(steps):
        s0 = 1.4 + (length - 1.4) * i / steps
        s1 = 1.4 + (length - 1.4) * (i + 1) / steps
        z = start[2] + (end[2] - start[2]) * (i + 1) / steps
        route.extend([(*v_add(start, d, s0), z), (*v_add(start, d, s1), z)])
    route[-1] = end
    return route


def spiral_points(plan):
    sp = plan.spiral
    return [(sp["radius"] * math.cos(math.pi / 2 - sp["turns"] * TAU * i / (sp["steps"] * 4)),
             sp["center_y"] + sp["radius"] * math.sin(math.pi / 2 - sp["turns"] * TAU * i / (sp["steps"] * 4)),
             plan.zr + (plan.lower["z"] - plan.zr) * i / (sp["steps"] * 4))
            for i in range(sp["steps"] * 4 + 1)]


def lower_terrain(plan, x, y, upper):
    low, sp = plan.lower, plan.spiral
    floor = low["z"] - 0.45
    lower = low["z"] + 1.9 + 0.28 * fbm(x, y, 37, 0.15)
    mix = smoothstep(-36, -25, y)
    h = lower * (1 - mix) + upper * mix
    h = carve(h, np.hypot(x, y - low["hall_y"]) - low["hall_radius"], floor, 1.2)
    for r in plan.lower_rooms:
        h = carve(h, np.hypot(x - r["c"][0], y - r["c"][1]) - (low["room_radius"] + plan.wall - 0.05), floor, 0.5)
        h = carve(h, seg_dist(x, y, r["P"], r["c"]) - low["bridge_width"] / 2 - 0.1, floor, 0.45)
    h = carve(h, np.hypot(x, y - sp["center_y"]) - sp["radius"] - sp["width"] / 2 - 0.4, floor - 1, 1.0)
    h = carve(h, np.maximum(np.abs(x) - sp["width"] / 2 - 0.25,
                           np.maximum(y - (-plan.b_out + 0.1), low["hall_y"] - y)), floor - 0.3, 0.5)
    en = lower_entrance(plan)
    a, b = en[0], en[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = np.clip(((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy), 0, 1)
    h = carve(h, seg_dist(x, y, a[:2], b[:2]) - low["entrance_width"] / 2 - 0.35,
              a[2] + t * (b[2]-a[2]) - 0.55, 0.65)
    h = carve(h, np.maximum(np.abs(x-low["fall_x"])-1.1, y-plan.y_cliff-3.5), plan.z_entry+0.3, 0.4)
    drop = smoothstep(0, 1.1, plan.y_cliff + 0.3*np.sin(x) - y)
    return h + (plan.cliff_base - h) * drop


def build_lower(plan, mats, colls, broken):
    low, sp = plan.lower, plan.spiral
    z, hy, radius = low["z"], low["hall_y"], low["path_radius"]
    g = Geo()
    g.prism(circle(0, hy, low["hall_radius"], 144), z-0.65, z)
    g.obj("Lower_hall", mats["built"], colls["Ring"], walk=True)
    g = Geo()
    for r in (1.4, 2.2, 2.5):
        g.ribbon(circle(0, hy, r, 96), z+0.045, z+0.01, 0.07, closed=True)
    for i in range(10):
        a = TAU*i/10
        x,y = 4.5*math.cos(a), hy+4.5*math.sin(a)
        g.prism(circle(x,y,0.29,12), z, z+0.18)
        g.prism(circle(x,y,0.20,12), z+0.18, z+2.4)
        g.prism(circle(x,y,0.31,12), z+2.4, z+2.65)
    g.obj("Hall_columns_and_rings", mats["built"], colls["Ring"])
    loop = [(*p, z) for p in circle(0,hy,radius,200)]
    entrance = lower_entrance(plan)
    gaps = [(r["P"], 1.6) for r in plan.lower_rooms] + [(entrance[-1][:2],2.3), ((0,hy+radius),2.0)]
    g = Geo()
    for run in rail_runs(circle(0,hy,low["hall_radius"]-0.12,240), gaps):
        g.ribbon(run,z+0.65,z+0.53,0.1)
        for x,y in run[::5]:
            g.box(x,y,0.055,z,z+0.6)
    for i in range(12):
        a=TAU*i/12
        x,y=(radius+0.8)*math.cos(a),hy+(radius+0.8)*math.sin(a)
        if all(math.dist((x,y),p)>r+0.3 for p,r in gaps):
            lantern(g,x,y,z,1.6)
    g.obj("Hall_rails_lanterns",mats["built"],colls["Ring"])
    room_plan = SimpleNamespace(cfg=plan.cfg,R=low["room_radius"],wall=plan.wall,ground=z-0.45)
    routes={}
    for r in plan.lower_rooms:
        build_room(room_plan,r,mats,colls["Rooms"])
        door=v_add(r["P"],r["d"],r["L"])
        g=Geo()
        g.ribbon([r["P"],v_add(door,r["d"],0.1)],z-EPS,z-0.3,low["bridge_width"])
        if r["key"] != broken:
            g.obj("Link_"+r["key"],mats["built"],colls["Links"],walk=True)
        g=Geo()
        for side in (-1,1):
            g.ribbon([v_add(r["P"],r["d"],1.0),door],z+0.55,z-0.03,0.1,offset=side*(low["bridge_width"]/2-0.05))
        g.obj("Rails_"+r["key"],mats["built"],colls["Links"])
        routes[r["key"]]=[(*r["P"],z),(*v_add(door,r["d"],0.7),z),(*r["c"],z)]
    # A 1.5 turn staircase: opposite ends, sufficient headroom at the crossing.
    helix=spiral_points(plan)
    g=Geo()
    for i in range(sp["steps"]):
        aa=[math.pi/2-sp["turns"]*TAU*(i+j/4)/sp["steps"] for j in range(5)]
        outer=[((sp["radius"]+sp["width"]/2)*math.cos(a),sp["center_y"]+(sp["radius"]+sp["width"]/2)*math.sin(a)) for a in aa]
        inner=[((sp["radius"]-sp["width"]/2)*math.cos(a),sp["center_y"]+(sp["radius"]-sp["width"]/2)*math.sin(a)) for a in aa]
        top=plan.zr+(z-plan.zr)*(i+1)/sp["steps"]
        g.prism(outer+inner[::-1],top-0.22,top)
    upper=(0,-(plan.oval.b+plan.t_mid),plan.zr)
    lower=(0,hy+radius,z)
    for a,b in ((upper,helix[0]),(helix[-1],lower)):
        g.ribbon([a[:2],b[:2]],a[2]-EPS,a[2]-0.28,sp["width"])
    if broken != "spiral":
        g.obj("Grand_spiral_steps",mats["built"],colls["Links"],walk=True)
    g=Geo()
    for radial in (sp["radius"]-sp["width"]/2+0.05,sp["radius"]+sp["width"]/2-0.05):
        pts=[(p[0]*radial/sp["radius"],sp["center_y"]+(p[1]-sp["center_y"])*radial/sp["radius"]) for p in helix]
        zz=[p[2] for p in helix]
        if radial > sp["radius"]:
            pts, zz = pts[10:-10], zz[10:-10]  # radial landing openings
        g.ribbon(pts,[v+sp["rail_height"] for v in zz],[v+sp["rail_height"]-0.1 for v in zz],0.09)
        for i in range(0,len(pts),6):
            g.box(*pts[i],0.05,zz[i]-0.1,zz[i]+sp["rail_height"])
        if radial>sp["radius"]:
            for i in range(12,len(pts),32):
                lantern(g,*pts[i],zz[i],1.4)
    g.obj("Grand_spiral_rails",mats["built"],colls["Links"])
    # Entrance: broad stairs rise diagonally from the bottom-left cliff.
    g=Geo()
    xy=[p[:2] for p in entrance]; zz=[p[2] for p in entrance]
    g.ribbon(xy,[v-EPS for v in zz],[v-0.4 for v in zz],low["entrance_width"])
    g.obj("Lower_entrance_stairs",mats["built"],colls["Entrance"],walk=True)
    g=Geo()
    for side in (-1,1):
        rail = [p for p in entrance if math.dist(p[:2], entrance[-1][:2]) > 2.5]
        g.ribbon([p[:2] for p in rail],[p[2]+0.55 for p in rail],[p[2]-0.2 for p in rail],0.16,offset=side*(low["entrance_width"]/2))
    start=entrance[0]
    d=v_norm((entrance[-1][0]-start[0],entrance[-1][1]-start[1]))
    g.profile(arch_profile(1.15,2.8,0.55),v_add(start,d,0.6),rot90(d),d,-0.25,0.25,start[2])
    g.obj("Lower_entrance_arch_rails",mats["built"],colls["Entrance"])
    return routes,loop,[upper]+helix+[lower],entrance


def build_navigation(plan, px, ring, routes, island, hall, lower_routes, spiral, entrance, broken):
    """Explicit 3D junctions, never screen-space proximity (spiral crossings are not junctions)."""
    nodes, edges, positions, lookup = {}, set(), {}, {}
    spacing=plan.cfg["levels"].get("nav_spacing_px",24)
    def node(p,level):
        key=tuple(round(float(x),5) for x in p)
        if key not in lookup:
            n=f"n{len(nodes)}"; lookup[key]=n; positions[n]=tuple(p)
            foot,head=px(p),px((p[0],p[1],p[2]+1.6))
            nodes[n]={"xy":foot,"level":level,"person_px":round(math.dist(foot,head),2)}
        return lookup[key]
    def line(points,level):
        # Arc length in image pixels; sample independently between forced junctions.
        screen=[px(p) for p in points]
        cum=np.concatenate([[0],np.cumsum([math.dist(a,b) for a,b in zip(screen,screen[1:])])])
        count=max(1,int(math.ceil(cum[-1]/spacing)))
        pts=[]
        for dist in np.linspace(0,cum[-1],count+1):
            j=min(len(points)-2,max(0,int(np.searchsorted(cum,dist,side="right")-1)))
            t=(dist-cum[j])/max(cum[j+1]-cum[j],1e-12)
            pts.append(tuple(points[j][k]+(points[j+1][k]-points[j][k])*t for k in range(3)))
        pts[0],pts[-1]=points[0],points[-1]
        ids=[node(p,level if not callable(level) else level(p)) for p in pts]
        for a,b in zip(ids,ids[1:]):
            if a!=b: edges.add(tuple(sorted((a,b))))
        return ids
    def attach_loop(loop,branches,level):
        # Include exact cardinal points and all branch intersections before resampling.
        anchors={}
        for key,route in branches.items():
            i=min(range(len(loop)),key=lambda j: math.dist(loop[j],route[0]))
            anchors[key]=i
        cuts=sorted(set(anchors.values()))
        for k,i in enumerate(cuts):
            j=cuts[(k+1)%len(cuts)]
            segment=(loop[i:j+1] if j>i else loop[i:]+loop[:j+1])
            line(segment,level)
        endpoints={}
        for key,route in branches.items():
            join=loop[anchors[key]]
            points=[join]+route if math.dist(join,route[0])>1e-5 else route
            if len(points)>1:
                endpoints[key]=line(points,level)[-1]
            else:
                endpoints[key]=node(join,level)
        return endpoints
    def door_route(r,route):
        door=v_add(r["P"],r["d"],r["L"]-0.28)
        return route[:-2]+[(*door,r["zf"])]
    upper={r["key"]:door_route(r,routes[r["key"]]) for r in plan.rooms}
    if island: upper[plan.island_room["key"]]=island
    upper["_south"]=[spiral[0]]
    north=max(ring,key=lambda p:p[1]); upper["_north"]=[north]
    upper_nodes=attach_loop(ring,upper,1)
    lower={r["key"]:door_route(r,lower_routes[r["key"]]) for r in plan.lower_rooms}
    lower["_spiral"]=[spiral[-1]]
    lower["_entry"]=list(reversed(entrance))
    lower_nodes=attach_loop(hall,lower,2)
    line(spiral,lambda p:1 if p[2]>(plan.zr+plan.lower["z"])/2 else 2)
    doors={k:v for k,v in {**upper_nodes,**lower_nodes}.items() if not k.startswith("_")}
    start=lower_nodes["_entry"]
    if broken:
        victim=doors[broken]; edges={e for e in edges if victim not in e}
    adjacency={n:[] for n in nodes}
    for a,b in sorted(edges): adjacency[a].append(b); adjacency[b].append(a)
    def search(source,target=None):
        prev={source:None}; q=deque([source])
        while q:
            a=q.popleft()
            if a==target: break
            for b in adjacency[a]:
                if b not in prev: prev[b]=a; q.append(b)
        if target is None: return prev
        if target not in prev: return []
        out=[target]
        while out[-1]!=source: out.append(prev[out[-1]])
        return out[::-1]
    reachable=search(start)
    missing=[k for k,n in doors.items() if n not in reachable]
    # One continuous walk with short out-and-back visits to the dead-end doors.
    upper_order=sorted(plan.rooms,key=lambda r:(math.atan2(r["P"][1],r["P"][0])-math.pi/2)%TAU)
    targets=[upper_nodes[r["key"]] for r in upper_order]
    if island: targets.insert(next(i for i,r in enumerate(upper_order) if r["side"]=="right"),doors[plan.island_room["key"]])
    targets += [upper_nodes["_north"],upper_nodes["_south"],lower_nodes["_spiral"]]
    lower_order=sorted(plan.lower_rooms,key=lambda r:(math.atan2(r["P"][1]-plan.lower["hall_y"],r["P"][0])-math.pi/2)%TAU)
    targets += [doors[r["key"]] for r in lower_order]+[start]
    scroll=[upper_nodes["_north"]]
    for target in targets:
        segment=search(scroll[-1],target)
        if not segment: break
        scroll.extend(segment[1:])
    bounds=all(0<=v["xy"][0]<=plan.cfg["image"]["width"] and 0<=v["xy"][1]<=plan.cfg["image"]["height"] for v in nodes.values())
    ok=not missing and len(reachable)==len(nodes) and bounds and scroll[-1]==start and set(doors.values())<=set(scroll)
    report={"ok":ok,"nodes":len(nodes),"edges":len(edges),"doors":len(doors),"reachable_doors":len(doors)-len(missing),"unreachable":missing,"all_nodes_reachable":len(reachable)==len(nodes),"in_bounds":bounds,"scroll_steps":len(scroll)}
    nav={"size":[plan.cfg["image"]["width"],plan.cfg["image"]["height"]],"nodes":nodes,"edges":[list(e) for e in sorted(edges)],"doors":doors,"start":start,"scroll":scroll}
    # Check the exported chords too: their interpolation must stay on actual floors.
    deps=bpy.context.evaluated_depsgraph_get()
    checks=[walk_test(bpy.context.scene,deps,"nav",[positions[a],positions[b]]) for a,b in edges]
    failures=[c for c in checks if not c["ok"]]
    report["walkable_edges"]=len(edges)-len(failures)
    report["edge_failures"]=failures[:8]
    report["ok"] &= not failures
    return nav,report


# ==========================================================================
def main():
    args = parse_args()
    t_start = time.perf_counter()
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg = copy.deepcopy(cfg)
    if args.levels is not None:
        cfg.setdefault("levels", {})["count"] = args.levels
    cfg["image"]["height"] = cfg["image"]["width"] * (3 if cfg.get("levels", {}).get("count", 1) == 2 else 2)
    os.makedirs(args.out, exist_ok=True)

    def out_path(name):
        if cfg.get("levels", {}).get("count", 1) == 2:
            name = name.replace("cave-", "cave2-", 1)
        return os.path.join(os.path.abspath(args.out), f"{args.prefix}{name}")

    plan = Plan(cfg)
    rl = cfg["rooms_layout"]
    palette = cfg["palette"]
    keyed = plan.rooms + ([plan.island_room] if plan.island_room else []) + (plan.lower_rooms if plan.two else [])
    if len(keyed) > min(len(palette), 63):
        raise SystemExit(f"palette 색({len(palette)})이 방 수({len(keyed)})보다 적다")
    used = {r["color"].lower() for r in keyed if r.get("color")}
    spare = [c.lower() for c in palette if c.lower() not in used]   # 색을 안 적은 방은 남는 색을 차례로 받는다
    for i, r in enumerate(keyed):
        r["code"] = i + 1
        r["color"] = r["color"].lower() if r.get("color") else spare.pop(0)
    if len({r["color"] for r in keyed}) != len(keyed):
        raise SystemExit("방 색이 겹친다 (cave_hub.json 의 color)")
    plan.island_code = plan.island_room["code"] if plan.island_room else 0
    for r in plan.rooms:                        # 나선 계단 탑 자리 (복도의 북쪽)
        if r["link"] == "spiral":
            r_mid = (rl["spiral_r_in"] + rl["spiral_r_out"]) / 2
            r["sigma"] = 1.0 if r["n"][1] > 0 else -1.0
            a_pt = v_add(r["P"], r["d"], r["low"])
            r["tower"] = v_add(a_pt, r["n"], r["sigma"] * r_mid)
    plan.cliff_base = (plan.v_min - plan.sv * (plan.y_cliff - 1.5)) / plan.cv - 3.0
    log(f"방 반지름 {plan.R:.2f}, 화면 {plan.fill}, 방 {len(plan.rooms)}개 + 섬")

    # ---- 씬 만들기 ----
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    mats = make_materials()
    colls = {}
    for name in ("Terrain", "Water", "Ring", "Rooms", "Links", "Island", "Entrance", "North"):
        colls[name] = bpy.data.collections.new(name)
        scene.collection.children.link(colls[name])
    Geo.counter = 0
    terrain_idx = build_terrain(plan, mats, colls["Terrain"]).pass_index
    build_rocks(plan, mats, colls["Terrain"])
    build_water(plan, mats, colls["Water"])
    _ring, lantern_count = build_ring(plan, mats, colls["Ring"])
    routes = {}
    for r in plan.rooms:
        build_room(plan, r, mats, colls["Rooms"])
        routes[r["key"]] = build_link(plan, r, mats, colls["Links"], skip=(r["key"] == args.selftest_break))
    island_route = build_island(plan, mats, colls["Island"]) if plan.island_room else None
    if plan.two:
        lower_routes, hall_loop, spiral_route, entrance_route = build_lower(plan, mats, colls, args.selftest_break)
    else:
        entrance_route = build_entrance(plan, mats, colls["Entrance"])
    build_north(plan, mats, colls["North"])
    cam = setup_camera(plan, scene)
    setup_light(scene)
    W, H = cfg["image"]["width"], cfg["image"]["height"]
    scene.render.resolution_x, scene.render.resolution_y = W, H
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    tris = sum(len(p.vertices) - 2 for o in scene.objects if o.type == "MESH" for p in o.data.polygons)
    log(f"오브젝트 {len(scene.objects)}개, 삼각형 약 {tris:,}개, 등불 {lantern_count}개 ({time.perf_counter() - t_start:.1f}s)")

    # ---- 검사 ----
    ths, _ = plan.oval.thetas(plan.t_mid, 0.2)
    ring_loop = [(*plan.oval.pt(plan.t_mid, float(a)), plan.zr) for a in ths]
    report = {"blender": bpy.app.version_string, "rooms": {}, "overlap": overlap_checks(plan)}
    report["ring"] = walk_test(scene, deps, "ring", ring_loop + ring_loop[:1], "Ring_path")
    report["entrance"] = walk_test(scene, deps, "entrance", entrance_route)
    if island_route:
        report["island_bridge"] = walk_test(scene, deps, "island", island_route, "Ring_path")
    for r in plan.rooms:
        res = walk_test(scene, deps, r["key"], routes[r["key"]], "Ring_path")
        res.update({"link": r["link"], "rise": r["rise"], "side": r["side"], "slot": r["index"] + 1})
        report["rooms"][r["key"]] = res
    walks = [report["ring"], report["entrance"]] + list(report["rooms"].values()) + ([report["island_bridge"]] if island_route else [])
    if plan.two:
        report["hall"] = walk_test(scene, deps, "hall", hall_loop + hall_loop[:1])
        report["spiral"] = walk_test(scene, deps, "spiral", spiral_route)
        report["lower_rooms"] = {r["key"]: walk_test(scene, deps, r["key"], lower_routes[r["key"]]) for r in plan.lower_rooms}
        walks += [report["hall"], report["spiral"]] + list(report["lower_rooms"].values())
        all_rooms = [(r, plan.R) for r in plan.rooms] + [(r, plan.lower["room_radius"]) for r in plan.lower_rooms]
        gaps = [math.dist(a["c"], b["c"]) - ra - rb - 2 * plan.wall
                for i, (a, ra) in enumerate(all_rooms) for b, rb in all_rooms[i+1:]]
        report["overlap"]["all_floors_room_gap"] = round(min(gaps), 3)
        report["overlap"]["ok"] &= min(gaps) > 0.05
        for name in ("hall", "spiral", "lower_rooms"):
            log(f"  {name}: {report[name]}")
    report["all_connected"] = all(w["ok"] for w in walks)
    for k, v in report["rooms"].items():
        log(f"  길 검사 {k:8s} {'OK ' if v['ok'] else 'FAIL'} {v.get('why', '')}{v['link']} +{v['rise']}")
    log(f"  둘레 길 {report['ring']['ok']}, 입구 계단 {report['entrance']['ok']}, 섬 다리 {report.get('island_bridge', {}).get('ok')}, 겹침 검사 {report['overlap']}")

    # ---- 허브용 좌표 ----
    def px(p):
        co = world_to_camera_view(scene, cam, Vector(p))
        return [round(co.x * W, 1), round((1.0 - co.y) * H, 1)]

    rooms_json = {}
    for r in plan.rooms + (plan.lower_rooms if plan.two else []):
        cx, cy = r["c"]
        radius = plan.lower["room_radius"] if r["level"] == 2 else plan.R
        ring8 = circle(cx, cy, radius * 1.04, 8, math.pi / 8, math.pi / 8 + TAU)
        rooms_json[r["key"]] = {"poly": [px((x, y, r["zf"])) for x, y in ring8][::-1], "at": px((cx, cy, r["zf"])), "level": r["level"]}
    if plan.island_room:
        o, b = plan.oval, plan.oval.b
        ys = (0.95 * b, 0.55 * b, -0.55 * b, -0.95 * b)
        lake8 = [(o.hw(0, y) * 1.04, y) for y in ys] + [(-o.hw(0, y) * 1.04, y) for y in reversed(ys)]
        isl = cfg["island"]
        rooms_json[plan.island_room["key"]] = {"poly": [px((x, y, 0.0)) for x, y in lake8], "at": px((0.0, isl["y"], isl["platform_z"])), "level": 1}
    y_a, y_b = entrance_route[0][1], -(plan.oval.b + plan.t_mid)      # ① 입구 계단 → 호수 아래 끝
    stair_way = [(0.0, y_a, plan.z_entry), (0.0, (y_a + y_b) / 2, (plan.z_entry + plan.zr) / 2), (0.0, y_b, plan.zr)]
    ways3d = [stair_way, plan.ring_side("left"), plan.ring_side("right")]   # ② 둘레 길 왼쪽 ③ 오른쪽 (아래 끝 → 위 끝)
    if plan.two:
        ways3d = [entrance_route, hall_loop + hall_loop[:1], spiral_route,
                  plan.ring_side("left", 65), plan.ring_side("right", 65)]
        nav, nav_report = build_navigation(plan, px, ring_loop, routes, island_route,
                                           hall_loop, lower_routes, spiral_route, entrance_route,
                                           args.selftest_nav_break)
        report["navigation"] = nav_report
        report["all_connected"] &= nav_report["ok"]
        with open(out_path("cave-nav.json"), "w", encoding="utf-8") as f:
            json.dump(nav, f, ensure_ascii=False, indent=1)
        log(f"  navigation: {nav_report}")
    hub = {"size": [W, H], "rooms": rooms_json, "ways": [cubic_path([px(p) for p in way]) for way in ways3d]}
    with open(out_path("cave-rooms.json"), "w", encoding="utf-8") as f:      # 방 하나에 한 줄
        rows = ",\n".join(f'    {json.dumps(k)}: {json.dumps(v)}' for k, v in rooms_json.items())
        ways = ",\n".join(f"    {json.dumps(w)}" for w in hub["ways"])
        f.write(f'{{\n  "size": {json.dumps(hub["size"])},\n  "rooms": {{\n{rows}\n  }},\n  "ways": [\n{ways}\n  ]\n}}\n')
    ids_json = {r["color"]: r["key"] for r in keyed}
    with open(out_path("cave-ids.json"), "w", encoding="utf-8") as f:
        json.dump(ids_json, f, ensure_ascii=False, indent=1)

    if not (report["all_connected"] and report["overlap"]["ok"]):
        with open(out_path("cave-report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        raise SystemExit(2)

    if args.light:                      # 시간대 빛 그림만 뽑고 끝낸다
        render_light(plan, scene, cam, cfg, args, out_path, report)
        report["times_sec"] = {"total": round(time.perf_counter() - t_start, 1)}
        with open(out_path("cave-light-report.json"), "w", encoding="utf-8") as f:
            json.dump({"light": report["light"], "times_sec": report["times_sec"]}, f, ensure_ascii=False, indent=1)
        log(f"빛 그림 전체 {report['times_sec']['total']}s -> {os.path.abspath(args.out)}")
        return

    # ---- 렌더 ----
    times = {}
    rcfg = cfg["render"]
    scene.render.engine = "CYCLES"
    cyc = scene.cycles
    cyc.device = "CPU"
    scene.render.threads_mode = "AUTO"
    scene.render.dither_intensity = 0.0
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.resolution_percentage = int(round(100 * args.scale))
    if not args.check_only:
        # (1) 구조 그림: 회색 점토
        cyc.samples = args.samples or rcfg["samples"]
        cyc.use_adaptive_sampling = True
        try:
            import _cycles
            cyc.use_denoising = bool(rcfg["denoise"] and getattr(_cycles, "with_openimagedenoise", False))
        except ImportError:
            cyc.use_denoising = False
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGB"
        scene.render.image_settings.color_depth = "8"
        times["structure"] = render(scene, out_path("cave-structure.png"))
        log(f"구조 그림 {times['structure']:.1f}s ({cyc.samples} 샘플, 디노이즈 {cyc.use_denoising})")

        # (2) 데이터 패스 두 장: 법선 / (가까움, 오브젝트 번호, 방 번호)
        verts = np.concatenate([np.array([o.matrix_world @ v.co for v in o.data.vertices]) for o in scene.objects if o.type == "MESH"])
        look = np.array([0.0, plan.cv, -plan.sv])
        depth = (verts - np.array(cam.location)) @ look
        near, far = float(depth.min()) - 0.5, float(depth.max()) + 0.5

        def mat_normal(nt, em):
            geo = nt.nodes.new("ShaderNodeNewGeometry")
            mul = nt.nodes.new("ShaderNodeVectorMath")
            mul.operation = "MULTIPLY_ADD"
            mul.inputs[1].default_value = (0.5, 0.5, 0.5)
            mul.inputs[2].default_value = (0.5, 0.5, 0.5)
            nt.links.new(geo.outputs["Normal"], mul.inputs[0])
            nt.links.new(mul.outputs["Vector"], em.inputs["Color"])

        def mat_data(nt, em):
            camd = nt.nodes.new("ShaderNodeCameraData")
            rng = nt.nodes.new("ShaderNodeMapRange")
            rng.inputs["From Min"].default_value, rng.inputs["From Max"].default_value = near, far
            rng.inputs["To Min"].default_value, rng.inputs["To Max"].default_value = 1.0, 0.0
            info = nt.nodes.new("ShaderNodeObjectInfo")
            div = nt.nodes.new("ShaderNodeMath")
            div.operation = "DIVIDE"
            div.inputs[1].default_value = 1024.0
            sep = nt.nodes.new("ShaderNodeSeparateXYZ")
            comb = nt.nodes.new("ShaderNodeCombineXYZ")
            nt.links.new(camd.outputs["View Z Depth"], rng.inputs["Value"])
            nt.links.new(info.outputs["Object Index"], div.inputs[0])
            nt.links.new(info.outputs["Color"], sep.inputs[0])
            nt.links.new(rng.outputs["Result"], comb.inputs["X"])
            nt.links.new(div.outputs["Value"], comb.inputs["Y"])
            nt.links.new(sep.outputs["X"], comb.inputs["Z"])
            nt.links.new(comb.outputs["Vector"], em.inputs["Color"])

        ds = int(rcfg["data_scale"])
        tmp = os.path.join(bpy.app.tempdir, "cave_hub")
        os.makedirs(tmp, exist_ok=True)
        saved_world = scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value
        scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0
        t_n = render_data_pass(scene, emission_material("Pass_normal", mat_normal), os.path.join(tmp, "normal.exr"), args.scale * ds)
        t_d = render_data_pass(scene, emission_material("Pass_data", mat_data), os.path.join(tmp, "data.exr"), args.scale * ds)
        scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = saved_world
        times["data_passes"] = t_n + t_d
        t0 = time.perf_counter()
        nrm = load_float(os.path.join(tmp, "normal.exr"))[:, :, :3] * 2.0 - 1.0
        dat = load_float(os.path.join(tmp, "data.exr"))
        nearness, obj_idx, room_idx = dat[:, :, 0], np.rint(dat[:, :, 1] * 1024).astype(np.int32), np.rint(dat[:, :, 2] * 64).astype(np.int32)
        hh, ww = nearness.shape

        # 선 그림: 오브젝트 경계 · 꺾인 면 · 깊이가 끊기는 곳을 선으로
        dep = nearness.astype(np.float64) * (far - near)
        edge = np.zeros((hh, ww), dtype=bool)
        cos_thr = math.cos(math.radians(rcfg["edge_normal_deg"]))
        for axis in (0, 1):
            a_sl = [slice(None), slice(None)]
            b_sl = [slice(None), slice(None)]
            a_sl[axis], b_sl[axis] = slice(0, -1), slice(1, None)
            a_sl, b_sl = tuple(a_sl), tuple(b_sl)
            diff = (obj_idx[a_sl] != obj_idx[b_sl]) | ((nrm[a_sl] * nrm[b_sl]).sum(axis=2) < cos_thr)
            both_rock = (obj_idx[a_sl] == terrain_idx) & (obj_idx[b_sl] == terrain_idx)   # 바위 벽의 가장자리
            diff |= both_rock & ((nrm[a_sl][:, :, 2] < rcfg["edge_steep"]) != (nrm[b_sl][:, :, 2] < rcfg["edge_steep"]))
            edge[a_sl] |= diff
            c_sl = [slice(None), slice(None)]
            lo_sl, hi_sl = list(c_sl), list(c_sl)
            c_sl[axis], lo_sl[axis], hi_sl[axis] = slice(1, -1), slice(0, -2), slice(2, None)
            lap = np.abs(dep[tuple(lo_sl)] - 2 * dep[tuple(c_sl)] + dep[tuple(hi_sl)])
            edge[tuple(c_sl)] |= lap > rcfg["edge_depth"]
        for _ in range(int(rcfg["line_width"])):
            grown = edge.copy()
            grown[1:, :] |= edge[:-1, :]
            grown[:-1, :] |= edge[1:, :]
            grown[:, 1:] |= edge[:, :-1]
            grown[:, :-1] |= edge[:, 1:]
            edge = grown
        h1, w1 = hh // ds, ww // ds
        cover = edge[:h1 * ds, :w1 * ds].reshape(h1, ds, w1, ds).mean(axis=(1, 3))
        write_png(out_path("cave-lines.png"), np.rint(255 * (1.0 - cover)))

        # 방 색깔 지도: 블록의 한 점만 집어 안티에일리어싱 없이
        lut = np.zeros((65, 3), dtype=np.uint8)
        for r in keyed:
            lut[r["code"]] = [int(r["color"][i:i + 2], 16) for i in (1, 3, 5)]
        ids_small = room_idx[:h1 * ds:ds, :w1 * ds:ds]
        write_png(out_path("cave-ids.png"), lut[np.clip(ids_small, 0, 64)])
        area = {r["key"]: int((ids_small == r["code"]).sum()) for r in keyed}
        report["id_pixels"] = area
        report["ids_all_visible"] = all(v > 0 for v in area.values())

        # 깊이 그림: 가까울수록 밝게, 0~1
        near_small = nearness[:h1 * ds, :w1 * ds].reshape(h1, ds, w1, ds).mean(axis=(1, 3))
        lo, hi = float(near_small.min()), float(near_small.max())
        write_png(out_path("cave-depth.png"), np.rint(255 * (near_small - lo) / max(hi - lo, 1e-9)))
        times["post"] = time.perf_counter() - t0
        log(f"데이터 패스 {times['data_passes']:.1f}s, 후처리 {times['post']:.1f}s")

    # ---- 마무리: .blend 는 구조 그림 설정으로 되돌려 저장 ----
    scene.render.resolution_percentage = 100
    cyc.samples, cyc.use_adaptive_sampling, cyc.max_bounces = rcfg["samples"], True, 12
    cyc.pixel_filter_type, cyc.filter_width = "BLACKMAN_HARRIS", 1.5
    cyc.use_denoising = bool(rcfg["denoise"])
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = "//../renders/" + ("cave2" if plan.two else "cave") + "-structure.png"
    if not args.no_blend:
        os.makedirs(os.path.dirname(os.path.abspath(args.blend)), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.blend if not plan.two or args.blend != os.path.join(HERE, "cave_hub.blend") else os.path.join(HERE, "cave_hub2.blend")), compress=True)
    times["total"] = time.perf_counter() - t_start
    report["times_sec"] = {k: round(v, 1) for k, v in times.items()}
    report["layout"] = {"room_radius": round(plan.R, 3), "ortho_scale": round(plan.ortho, 3), "fill": plan.fill,
                        "rooms": {r["key"]: {"center": [round(c, 2) for c in r["c"]], "floor_z": r["zf"]} for r in plan.rooms}}
    with open(out_path("cave-report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    log(f"모든 방 연결: {report['all_connected']}, 겹침 없음: {report['overlap']['ok']}, 전체 {times['total']:.1f}s -> {os.path.abspath(args.out)}")
    if not (report["all_connected"] and report["overlap"]["ok"]):
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
