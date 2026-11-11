"""Headless Blender 테스트 씬.

바닥 평면 + Suzanne/구/큐브(각각 다른 머티리얼) + 태양광 + 카메라를 만들고
Cycles CPU 로 렌더링한 뒤 PNG 와 .blend 파일을 저장한다.

실행:
    ~/blender/blender --background --python scenes/test_scene.py
    # 옵션은 `--` 뒤에 넘긴다:
    ~/blender/blender -b --python scenes/test_scene.py -- --samples 64 --no-denoise
"""

import argparse
import math
import os
import sys
import time

import bpy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="test_scene.py")
    p.add_argument("--output", default=os.path.join(REPO_ROOT, "renders", "test_render.png"))
    p.add_argument("--blend", default=os.path.join(REPO_ROOT, "scenes", "test_scene.blend"))
    p.add_argument("--samples", type=int, default=32)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--no-denoise", action="store_true", help="디노이즈 끄기")
    return p.parse_args(argv)


# --------------------------------------------------------------------------
# 씬 구성
# --------------------------------------------------------------------------
def make_material(name, base_color, roughness=0.5, metallic=0.0, **extra_inputs):
    mat = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):  # 5.x 부터는 항상 노드 사용(use_nodes deprecated)
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    for key, value in extra_inputs.items():
        socket = bsdf.inputs.get(key)
        if socket is not None:
            socket.default_value = value
    mat.diffuse_color = (*base_color, 1.0)  # 뷰포트 색상
    return mat


def assign(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def build_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    # 바닥
    bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.name = "Floor"
    assign(floor, make_material("Floor_Mat", (0.55, 0.55, 0.52), roughness=0.8))

    # Suzanne: 붉은 플라스틱 + 서브디비전 + 스무스
    bpy.ops.mesh.primitive_monkey_add(size=1.6, location=(0, 0, 1.0), rotation=(0, 0, math.radians(-20)))
    suzanne = bpy.context.active_object
    suzanne.name = "Suzanne"
    suzanne.modifiers.new("Subsurf", "SUBSURF").levels = 2
    suzanne.modifiers["Subsurf"].render_levels = 2
    bpy.ops.object.shade_smooth()
    assign(suzanne, make_material("Suzanne_Mat", (0.8, 0.08, 0.05), roughness=0.35,
                                  **{"Coat Weight": 0.5}))

    # 구: 금속(골드)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.8, segments=64, ring_count=32, location=(-2.4, 0.6, 0.8))
    sphere = bpy.context.active_object
    sphere.name = "Sphere"
    bpy.ops.object.shade_smooth()
    assign(sphere, make_material("Sphere_Mat", (1.0, 0.71, 0.29), roughness=0.2, metallic=1.0))

    # 큐브: 파란 무광
    bpy.ops.mesh.primitive_cube_add(size=1.4, location=(2.4, 0.4, 0.7), rotation=(0, 0, math.radians(30)))
    cube = bpy.context.active_object
    cube.name = "Cube"
    bevel = cube.modifiers.new("Bevel", "BEVEL")
    bevel.width = 0.05
    bevel.segments = 3
    assign(cube, make_material("Cube_Mat", (0.05, 0.25, 0.8), roughness=0.6))

    # 태양광
    sun_data = bpy.data.lights.new("Sun", type="SUN")
    sun_data.energy = 4.0
    sun_data.angle = math.radians(3)
    sun = bpy.data.objects.new("Sun", sun_data)
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))
    scene.collection.objects.link(sun)

    # 카메라
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 40
    cam = bpy.data.objects.new("Camera", cam_data)
    cam.location = (0.0, -9.5, 4.2)
    scene.collection.objects.link(cam)
    track = cam.constraints.new("TRACK_TO")
    track.target = suzanne
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"
    scene.camera = cam

    # 월드(약한 하늘색 환경광)
    world = bpy.data.worlds.new("World")
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.45, 0.6, 0.85, 1.0)
    bg.inputs["Strength"].default_value = 0.6
    scene.world = world
    return scene


# --------------------------------------------------------------------------
# 렌더 설정
# --------------------------------------------------------------------------
def configure_render(scene, args):
    scene.render.engine = "CYCLES"
    cycles = scene.cycles
    cycles.device = "CPU"
    cycles.samples = args.samples
    cycles.use_adaptive_sampling = True

    try:  # CPU 디노이즈는 OpenImageDenoise 포함 빌드에서만 가능
        import _cycles
        has_oidn = bool(getattr(_cycles, "with_openimagedenoise", False))
    except ImportError:
        has_oidn = False
    denoise = not args.no_denoise and has_oidn
    cycles.use_denoising = denoise
    if denoise:
        cycles.denoiser = "OPENIMAGEDENOISE"

    r = scene.render
    r.resolution_x = args.width
    r.resolution_y = args.height
    r.resolution_percentage = 100
    r.threads_mode = "AUTO"
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGBA"
    r.image_settings.color_depth = "8"
    r.filepath = args.output
    return denoise


def main():
    args = parse_args()
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    os.makedirs(os.path.dirname(args.blend), exist_ok=True)

    scene = build_scene()
    denoise = configure_render(scene, args)
    print(f"[test_scene] Blender {bpy.app.version_string} | Cycles CPU | "
          f"{args.width}x{args.height} | {args.samples} samples | denoise={denoise} | "
          f"threads={scene.render.threads}")

    t0 = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    elapsed = time.perf_counter() - t0
    print(f"[test_scene] Render time: {elapsed:.2f}s -> {args.output}")

    bpy.ops.wm.save_as_mainfile(filepath=args.blend, compress=True)
    print(f"[test_scene] Saved blend -> {args.blend}")

    if not os.path.isfile(args.output) or os.path.getsize(args.output) == 0:
        print("[test_scene] ERROR: render output missing", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)  # 헤드리스 실행 시 실패를 종료 코드로 전달
