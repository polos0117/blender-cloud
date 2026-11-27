# blender-cloud

Linux 서버(GUI 없음)에서 Blender를 **headless**로 실행해 씬을 만들고 Cycles로 렌더링하는 파이프라인 테스트입니다.

![테스트 렌더](https://polos0117.github.io/blender-cloud-img/img/2026-11-11_test_render.png)

## 구성

| 파일 | 설명 |
| --- | --- |
| `setup_blender.sh` | Blender LTS(Linux x64) 다운로드·설치 스크립트. 여러 번 실행해도 안전(멱등) |
| `scenes/test_scene.py` | bpy 씬 생성 + 렌더 스크립트 |
| `scenes/test_scene.blend` | 스크립트가 저장한 씬 파일 |
| `scenes/cave_hub.py` · `cave_hub.json` | 공방 허브 「대미궁」 지도의 3D 구조 생성 + 렌더 + 좌표 출력 (아래 3절) |
| `scenes/cave_hub.blend` | 위 스크립트가 저장한 대미궁 씬 |
| `scenes/dot_rig.py` · `scenes/dot_rig/` | 도트 인물을 부위별로 잘라 뼈대로 움직이기 (아래 4절) |
| `scenes/city_globe.py` · `city_globe.json` · `city_globe_check.py` | 도시 공방 지구본 모형 셋(판타지 · 스팀펑크 · 사이버펑크) 짓기 · glb 내보내기 · 검사 (아래 5절) |
| `scenes/city_globe_<세계>.blend` | 위 스크립트가 저장한 지구본 씬 셋 |
| `renders/` | 렌더 작업 폴더(커밋 안 함) — 결과물은 그림 저장소로 옮긴다 |

Blender 바이너리는 저장소에 포함하지 않습니다(기본 설치 위치 `~/blender`, `.gitignore` 참고).

## 1. Blender 설치

```bash
./setup_blender.sh
```

동작 순서:

1. `https://download.blender.org/release/Blender5.2/` 에서 최신 패치 버전을 찾습니다(현재 **5.2.4 LTS**).
2. tarball 을 `~/.cache/blender-download/` 에 받고 공식 `.sha256` 으로 검증합니다. 캐시가 있으면 다시 받지 않습니다.
3. 임시 폴더에 푼 뒤 `~/blender` 로 교체합니다(중간에 실패해도 기존 설치는 유지).
4. `~/blender/blender --version` 이 실패하면 아래 라이브러리를 apt 로 설치하고 다시 확인합니다.
   `libxi6 libxxf86vm1 libxfixes3 libxrender1 libgl1 libxkbcommon0 libsm6`
   (root 가 아니면 `sudo` 사용)

같은 버전이 이미 설치돼 있으면 즉시 종료합니다.

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `BLENDER_SERIES` | `5.2` | LTS 시리즈 |
| `BLENDER_VERSION` | (자동) | 정확한 버전 고정, 예: `5.2.4` |
| `BLENDER_DIR` | `~/blender` | 설치 위치 |
| `BLENDER_CACHE` | `~/.cache/blender-download` | tarball 캐시 위치 |
| `FORCE` | `0` | `1` 이면 설치돼 있어도 다시 설치 |

```bash
BLENDER_VERSION=5.2.4 FORCE=1 ./setup_blender.sh
```

> 클라우드 샌드박스처럼 외부 네트워크가 제한된 환경에서는 `download.blender.org` 를 허용 호스트에 추가해야 합니다.

## 2. 씬 렌더링

저장소 루트에서 실행합니다.

```bash
~/blender/blender --background --python scenes/test_scene.py
```

결과:

- `renders/test_render.png` — 1280x720, RGBA 8bit PNG (작업 폴더, 커밋 안 함)
- `scenes/test_scene.blend` — 생성된 씬(Blender에서 열어 확인 가능)

씬 내용:

- 바닥 평면(회색 무광)
- Suzanne — 붉은 플라스틱(코트), 서브디비전 + 스무스 셰이딩
- 구 — 골드 금속
- 큐브 — 파란 무광, 베벨
- 태양광(Sun), 카메라(Suzanne 를 Track To), 하늘색 월드 환경광

렌더 설정: Cycles / CPU / 32 샘플(적응형 샘플링) / 1280x720 / OpenImageDenoise 디노이즈(빌드에 포함된 경우 자동 사용).

옵션은 `--` 뒤에 넘깁니다.

```bash
~/blender/blender -b --python scenes/test_scene.py -- --samples 128 --width 1920 --height 1080 --no-denoise --output /tmp/out.png
```

스크립트에서 오류가 나면 종료 코드 1로 끝나므로 CI 등에서 실패를 감지할 수 있습니다. 실행 로그에 Blender 버전과 렌더 시간이 출력됩니다.

```
[test_scene] Blender 5.2.4 LTS | Cycles CPU | 1280x720 | 32 samples | denoise=True | threads=4
[test_scene] Render time: 16.44s -> .../renders/test_render.png
```

참고: GPU 가 없는 서버에서는 `CUEW initialization failed` 경고가 나오지만 CUDA 탐지 실패 메시지일 뿐 CPU 렌더에는 영향이 없습니다.

## 3. 대미궁 구조 (공방 허브 지도)

`scenes/cave_hub.py`는 설정 `scenes/cave_hub.json`에서 두 층의 대미궁과 보행 그래프를 만든다.
기본 크기는 **1024×3072**. 위층의 호수·섬 신전·방 열 개는 이전 한 층 지도의 위치와 크기를 그대로 유지하고,
아래로 넓은 1.5회전 계단, 기둥이 둘러선 원형 홀, 도구 방 다섯과 왼쪽 아래 입구를 덧붙인다.
구조는 회색 점토로 렌더하며 애니 화풍 칠은 별도 작업이다.

![두 층 대미궁 구조](https://polos0117.github.io/blender-cloud-img/img/2026-11-11_cave2-structure.png)

```bash
# 이 클라우드 환경의 Blender 5.2.4
/workspace/.cloud-tools/blender/blender -b --python scenes/cave_hub.py -- --out renders/cave2 --prefix 2026-11-11_
# 기존 1024×2048 한 층 구조 (예전 입구 포함)
/workspace/.cloud-tools/blender/blender -b --python scenes/cave_hub.py -- --levels 1 --out renders/cave --no-blend
```

날짜 접두사를 제외한 파일 이름:

| 파일 | 내용 |
| --- | --- |
| `cave2-structure.png` | 점토 구조·조명·그림자 |
| `cave2-lines.png` | 흰 바탕의 검은 윤곽선 |
| `cave2-ids.png` · `cave2-ids.json` | 장소 16개의 정확한 단색과 색 → 열쇠 매핑. 나머지 검정 |
| `cave2-depth.png` | 가까울수록 밝은 0~255 깊이 그림 |
| `cave2-rooms.json` | `size`, 장소별 `poly`·`at`·`level`, 등불 길 5개의 SVG `ways` |
| `cave2-nav.json` | 24px 간격의 보행 노드, 간선, 문, 시작점, 스크롤 안내 경로 |
| `cave2-report.json` | 실제 보행면·겹침·그래프 검사와 렌더 시간. 작업 기록으로만 보관 |

두 층 씬은 `scenes/cave_hub2.blend`, 한 층 씬은 기존 `scenes/cave_hub.blend`에 저장된다.
렌더 결과는 `renders/`에서 확인한 다음 그림 저장소 `blender-cloud-img/img/`에 날짜를 붙여 올린다.

### 설정과 좌표

- `rooms`: `key`, `name`, `side`, `color`, `level`(1 위층·2 아래층). 위층의 `rise`는 계단 높이이며 2m 이상은 작은 나선 계단이다. 아래층은 `position: [x,y]`로 자리만 정하고 반지름은 모두 같다.
- `levels.count`: 1 또는 2. `--levels`로 실행할 때만 덮어쓸 수 있다.
- `levels.lower`: 홀 높이·위치·반지름, 방 반지름, 다리 폭, 입구 위치·높이·폭.
- `levels.spiral`: 중앙 계단 중심·반지름·폭·회전 수·단 수·난간 높이.
- `levels.nav_spacing_px`: 간선 접점 사이의 노드를 투영 화면에서 나눌 최대 간격(기본 24px). 접점 근처의 짧은 구간은 더 조밀하다.
- X는 좌우, +Y는 그림 위쪽, Z는 높이. 60° 정사영이며 모든 JSON 화면 좌표는 왼쪽 위가 원점인 **전체 크기 1024×3072** 기준이다. `--scale`은 그림만 줄이는 미리보기 옵션이다.

`rooms.ways` 순서는 입구→홀, 홀 둘레, 위층→아래층 나선 계단, 위층 왼쪽 둘레, 위층 오른쪽 둘레다.
`element` 색과 다각형은 이전과 같이 호수·섬·신전·다리 영역을 포함한다.

### 길 지도와 검사

`cave2-nav.json`의 `nodes`는 `xy`, `level`, `person_px`(그 자리에 서 있는 키 1.6m 사람의 투영 높이)를 가진다.
`edges`는 양방향이다. `doors`는 문턱 바로 앞의 노드(섬은 신전 앞), `start`는 맨 아래 왼쪽 동굴 입구다.
나선 계단의 화면상 교차점은 서로 다른 높이이므로 연결하지 않는다. 연결점은 실제 3D 길에서만 만든다.

`scroll`은 위층 북쪽에서 시작해 위층 방들과 섬, 나선 계단, 아래층 방들을 지나 입구에서 끝나는 **연속 보행 순서**다.
막다른 문 앞에 들렀다가 둘레 길로 돌아오므로 노드가 반복되며 y값이 단조롭게 증가하지 않는다.
스크롤 진행률을 이 경로의 누적 길이에 대응시키고, 방을 눌렀을 때는 현재 노드에서 `doors[key]`까지 최단 경로를 구하면 된다.

렌더 전에 다음을 검사하며 실패하면 종료 코드 2로 끝낸다.

- 모든 길과 **내보낸 모든 간선**을 5cm 간격으로 따라가며 바닥, 단 높이, 벽·난간 충돌을 광선으로 검사한다.
- 양층의 방 겹침을 검사한다.
- 입구에서 16개 문과 모든 노드가 도달 가능한지, `scroll`이 실제 간선만 사용하는지, 좌표가 화면 안인지 검사한다.
- `--selftest-break fav` 또는 `--selftest-break spiral`은 실제 연결 구조를 빼서 검사 실패를 확인한다. `--selftest-nav-break fav`는 내보낼 문 노드를 고립시킨다.

```bash
BLENDER_BIN=/workspace/.cloud-tools/blender/blender python3 tests/cave_hub_test.py
```

통합 검사는 기존 한 층 모드의 위층 좌표가 두 층 모드와 정확히 같은지, 모든 문·스크롤 연결, 노드 간격,
다리·나선 계단·그래프를 각각 끊은 세 경우가 실패하는지 확인한다.

선·색·깊이는 법선과 깊이/오브젝트/방 번호를 담은 2배 해상도 데이터 패스로 만든다.
색 지도는 한 픽셀에 한 색만 사용하고, 구조 이미지와 같은 카메라·지오메트리를 쓴다.

2026-11-11 검증: 319개 노드·320개 간선·16개 문 모두 연결, 전체 간선 보행면 검사 통과. 64샘플 CPU 구조 렌더 248.2초, 데이터 패스 13.6초, 전체 생성 270.8초. 네 그림은 모두 1024×3072이며 ID 지도는 장소 16색과 검정만 사용한다.

추가 옵션: `--samples N`, `--scale 0.5`, `--check-only`, `--no-blend`, `--blend 경로`, `--config 설정.json`.

### 시간대 빛 그림 (`--light`)

칠한 지도(허브 `img/cave4.webp`)는 다시 칠하지 않고, 같은 카메라로 구운 빛 그림을 그 위에 겹쳐 시간대를 바꾼다. 낮은 지금 그림 그대로라 뽑지 않는다.

```bash
~/blender/blender -b --python scenes/cave_hub.py -- --out renders/ --light all      # dawn · morning · night, 쉼표로 골라도 된다
```

시간대마다 512×1536 WebP 세 장이 나온다: `cave2-light-<시간대>-mul.webp`(곱하기), `-glow.webp`(더하기), `-preview.webp`(칠한 지도에 곱하기 → 더하기를 해 본 확인용). `--light` 를 주면 구조 그림과 `.blend` 는 건너뛴다(길 검사는 그대로 한다). 2026-11-12 기준 세 시간대 전체 94초.

- **곱하기(mul)**: 그 시간대의 자연광 ÷ 낮 조명(구조 그림의 Sun 1.4 + world)이 열린 바닥에 주는 밝기. 흰색이면 그대로, 어두울수록 어두워진다. 발광은 넣지 않는다. 낮의 해가 드리운 그림자는 칠한 지도에 없어서 기준에서 뺐다(넣으면 그 그림자가 거꾸로 밝게 찍힌다).
- **더하기(glow)**: 빛을 내는 것만. 새벽 = 호수 · 아래층 홀에 낮게 깔린 푸른 안개, 아침 = 북쪽 천장 폭포 쪽과 입구 쪽에서 들이치는 빛줄기, 밤 = 등불 기둥 · 방 문 앞의 주황 불빛과 폭포의 달빛. 검정이면 없음.
- 두 그림 모두 화면값(감마 2.2)이고, 칠한 지도와 구조가 10~25px 어긋나 있어 σ 4px(빛 그림 픽셀 = 지도 8px)로 흐렸다. 허브에서는 지도 위에 mul 을 `multiply`, 그 위에 glow 를 `screen`(또는 `plus-lighter`)으로 얹으면 된다.
- 값은 `cave_hub.json` 의 `light.times` 에 있다. `world_strength` = 그 시간대의 바탕 밝기(밤에 방이 안 보이면 이걸 올린다), `sun` = 해의 세기 · 색 · 고도 · 방위, `beams` = 들이치는 햇살, `glow.lanterns` · `doors` = 점광원 세기, `glow.fog` = 안개, `glow.shafts` = 빛줄기, `fall_light` = 폭포 달빛. 미리보기 바탕은 `light.base`(또는 `--light-base 경로`).

## 4. 도트 인물 뼈대 (종이 인형 방식)

확정된 **정지 도트 한 장**을 부위별로 잘라 Blender 뼈대로 움직인다. 모든 칸이 같은 픽셀 조각에서 나오니 얼굴 · 옷 · 키가 칸마다 흔들리지 않고, 칸 수도 늘릴 수 있다. 첫 시험은 앨리스(192×256)다.

![걷기 비교](https://polos0117.github.io/blender-cloud-img/img/2026-11-11_alice-walk-compare.webp)

```bash
~/blender/blender -b --python scenes/dot_rig.py -- --actor alice --out renders/
```

한 번에 하는 일: 부위 자르기(가려졌던 곳 메우기) → 부위마다 평면 판 + 뼈대 → 동작 → 4배 렌더 → 줄이고 색 맞추기 → 시트 · 미리보기 · 재생 설정.

| 결과 (`renders/`) | 내용 |
| --- | --- |
| `alice-walk-rig.png` | 걷기 시트. 192×256 칸 8개(1536×256), 투명 배경 |
| `alice-idle-rig.png` | 대기 시트. 칸 6개(1152×256) |
| `alice-walk-rig-preview.webp` · `alice-idle-rig-preview.webp` | 2배 확대(nearest), 회색 바탕, 무한 반복 |
| `alice-walk-compare.webp` | 왼쪽 예전 걷기(GPT 6칸) · 오른쪽 이번 걷기(8칸) |
| `alice-rig.json` | 도트 공방 재생 설정 꼴: `frames` · `order` · `ms` · `speed` · `dy` |
| `dot_rig/alice/` | 작업 기록: 부위 그림, 확인용 `parts_marked.png`(메운 곳 자홍 · 빌린 곳 청록), `report.json` |

`scenes/dot_rig/alice.blend` 에 판 · 뼈대 · 두 동작(액션)이 저장된다. 미리보기 WebP 는 시스템 `python3` 의 Pillow 로 만든다(없으면 그 단계만 건너뛴다).

### 파일

- `scenes/dot_rig/<인물>_parts.json` — 인물마다 하나. 부위 경계(`runs` = [첫 줄, 끝 줄, 첫 칸, 끝 칸], `poly` = 다각형), 층(`z`), 관절 자리, 뼈대.
  - `parts` 는 적힌 차례로 픽셀을 가져간다. `rest` 부위가 남은 픽셀을 모두 받는다.
  - `share`: 관절이 벌어지지 않게 이웃 부위 픽셀을 빌려 겹쳐 둔다(무릎).
  - `fill`: 앞 층 부위에 가려져 있던 곳을 제 픽셀의 가장 가까운 색으로 메운다. `extend`: 가려진 가장자리를 줄마다 몇 칸 잇고 윤곽선 색으로 닫는다. 둘 다 원본에 있는 색만 쓴다.
- `scenes/dot_rig/motions/walk.json` · `idle.json` — 인물과 따로 쓰는 동작 값. 다리는 발 자리(보폭 대비 앞뒤, 드는 높이)로 적고 스크립트가 인물의 다리 길이로 관절 각도를 푼다. 팔 · 치마 · 리본은 각도, 오르내림은 픽셀.
- 다른 인물: `<이름>_parts.json` 만 만들고 `--actor <이름>` 으로 돌린다. 뼈 이름(`thigh_1`, `forearm_2` …)을 같게 쓰면 같은 동작이 그대로 붙는다.

### 픽셀 정리와 검사

- 선 자세를 먼저 렌더해 **원본과 픽셀 하나까지 같은지** 본다(다르면 종료 코드 2). 부위 자르기 · 층 · 메우기가 틀리면 여기서 걸린다.
- 4배(768×1024)로 굽고 192×256 으로 줄인다. 기본은 4×4 안에서 가장 많은 색을 고르고(`--reduce mode`), `--reduce nearest` 는 가운데 한 점만 쓴다. 돌아간 부위의 윤곽선은 mode 쪽이 덜 깨진다. 돌지 않는 부위(머리 · 몸통)는 두 방식 모두 원본 그대로다.
- 색은 정지 도트의 색 목록으로 다시 맞추고, 알파는 0 아니면 255 다.
- 디딘 발은 칸마다 같은 폭으로 뒤로 간다(앨리스: 보폭 40px, 칸마다 10px). `speed` = 보폭 ÷ 반 주기. 신발이 돌아도 발바닥의 가장 낮은 점이 땅에 오게 발 높이를 맞춘다.
- 몸의 오르내림은 시트에 들어 있어 `dy` 는 0 이다. 걷는 동안 몸은 서 있을 때보다 접지 칸 2px · 사이 칸 1px 낮고 지나가는 칸에서 같은 높이다(다리가 곧게 펴진 길이를 넘지 않게).
- 두 발의 땅 높이는 정지 도트 그대로다. 앨리스는 화면 왼쪽 발이 1px 높다(3/4 시점의 원근).

옵션: `--prefix 2026-11-11_`, `--parts-only`(부위 자르기와 확인용 그림까지만), `--source 파일.png`(받지 않고 로컬 파일 사용), `--scale 4`, `--no-blend`.

2026-11-11 기준(4 vCPU): 칸 15장 렌더 32초, 전체 35초.

## 5. 도시 공방 지구본 (모형 셋)

도시 공방(city-atelier) 첫 화면의 회전 무대 위 지구본을 Blender 모형으로 — 판타지 · 스팀펑크 · 사이버펑크(2026-11-27).
웹은 three.js r170 의 `GLTFLoader` 로 `models/globe/<세계>.glb` 를 읽고, 도시 점은 그 위에 SVG 로 겹쳐 그린다. 그래서 **좌표 약속이 1도라도 어긋나면 점이 엉뚱한 나라 위에 뜬다.**

```bash
~/blender/blender -b -P scenes/city_globe.py -- --world fantasy     # 하나
~/blender/blender -b -P scenes/city_globe.py -- --world all         # 셋 + 비교판(약 2분, 4 vCPU)
#   --no-render(내보내기 · 검사만) · --samples N · --board-only(있는 정지 그림으로 비교판만)
python3 scenes/city_globe_check.py                                  # 검사만(Pillow 가 없으면 blender -b -P scenes/city_globe_check.py -- <glb>)
```

작업 폴더 `renders/city-globe/`(올리지 않는다): `<세계>.glb` · `<세계>-still.webp`(1024×1024, 투명 배경, 웹이 서울을 가운데 둘 때의 모습) · `compare.webp`(위 줄 어두운 화면 · 아래 줄 밝은 화면의 바탕색 위) · `tex/` · `report.json`. `.blend` 는 `scenes/city_globe_<세계>.blend`(텍스처를 싸 넣었다).
색 · 굵기 · 거칠기는 `scenes/city_globe.json`, 받침의 꼴은 `city_globe.py` 의 `stand_<세계>()`.

### 좌표 약속 (어기면 안 됨)

| | |
| --- | --- |
| 웹(three, Y-up) | `latLonToVec3(lat, lon) = [cos(lat)·sin(lon), sin(lat), cos(lat)·cos(lon)]` — 경도 0 = +Z(카메라 쪽) · 동경 90 = +X · 북극 = +Y · 반지름 1 |
| Blender(Z-up) | glTF +Y Up 내보내기는 `three (x, y, z) = Blender (x, z, −y)` → `x = cos(lat)·sin(lon)`, `y = −cos(lat)·cos(lon)`, `z = sin(lat)` — 경도 0 = −Y(정면) · 동경 90 = +X · 북극 = +Z |
| UV | 꼭짓점 위치에서 바로: `u = (lon + 180) / 360`, `v = (lat + 90) / 180`. 경도 ±180 이음매는 꼭짓점을 둘로(u = 0 과 1), 극은 칸마다 꼭짓점 하나(u = 그 칸의 가운데). glb 안의 glTF UV 는 위아래가 뒤집힌다(`v = 1 − Blender v`) |
| 텍스처 | 정방형 도법 2048×1024 — 왼쪽 끝 경도 −180, 위 끝 북위 90 |
| 노드 | `Globe`(구 — 이것과 그 자식만 돈다) · `Stand`(받침 · 고리 · 장식 — `Globe` 의 자식이 아니다). 웹이 매 프레임 `Globe.rotation.set(lat, −lon, 0, 'XYZ')` 로 덮어쓰니 둘 다 회전 0 · 크기 1 · 위치 0, 방향은 메시 데이터에 |
| 화면 자리 | 직교 카메라가 −Y 쪽에서 본다. 받침은 위 z ≤ 1.15 · 양옆 \|x\| ≤ 1.2 · 아래 z ≥ −1.39 안, 구를 파고들지 않는다 |

### 땅

world-atlas `land-50m.json`(Natural Earth, 공유 저작물 — SVG 지구본과 같은 데이터)의 TopoJSON 을 직접 푼다(arcs 델타 복원 + transform). jsDelivr 가 막힌 곳에서는 npm 꾸러미(`world-atlas@2.0.2`)에서 같은 파일을 받는다.

world-atlas 는 경도 −180 과 180 을 한 점으로 꿰어 둔다 — 추코트카(유라시아) · 피지 · 남극의 고리 다섯은 날짜변경선을 넘어가고, 그중 둘(남극)은 극을 한 바퀴 돈다. 그대로 칠하면 가로로 번져서: 고리마다 경도를 이어 붙이고, 극을 도는 고리는 극을 거쳐 닫고, 세 배 폭에 칠한 뒤 접어 겹친다(`raster_land`). 해안선은 흐린 땅 몫이 0.5 를 지나는 띠라서 이음매에 선이 서지 않는다.

### 웹의 빛에 맞춘 재질

웹에는 환경맵이 없다 — AmbientLight 1.2 + DirectionalLight 1.6(Blender (−2, −5, 3)에서). 미리보기도 같게: World 는 확산에만(세기 1.2 / π, 반사 · 카메라에는 안 보임), Sun 1.6, 그림자 · 가림 · 되튐 없음, 보기 변환 Standard.
같은 glb 를 three.js r170 에 같은 빛 · 같은 카메라로 띄워 견줬더니 정지 그림과 화소값 차이가 몇 단계 안이었다(2026-11-27).

- 금속은 metallic 0.3~0.45(1 이면 웹에서 까맣다), 바탕색은 밝게(`gain`).
- 거칠기를 0.4 아래로 내리면 방향광이 화면 가운데 왼쪽 위 — 도시 점이 서는 자리 — 에 하얗게 맺힌다. 바다 · 땅 모두 0.48 이상.
- 재질은 Principled BSDF + 이미지 텍스처만(바탕 · 거칠기/금속 · 노멀 · 발광). 잡음(그을음 · 녹청)은 구 위의 3D 값 잡음을 이미지로 구웠다.
- 옅은 선(경위선 · 해안선)은 화면값(sRGB)에서 얹는다 — CSS 의 `rgb(… / .3)` 과 같은 셈. 선형에서 섞으면 어두운 바다 위에서 훨씬 밝아진다.
- Blender 메모: `images.new` 로 만든 그림은 색 공간을 화소보다 **먼저** 정한다(뒤에 바꾸면 그림이 비워져 검게 저장된다).

### 검사 (`scenes/city_globe_check.py` — 내보낸 뒤 바로 돈다)

glb 를 파이썬으로 열어(JSON + 바이너리) glb 안의 값으로 본다: ① `Globe` · `Stand` 가 있고 `Stand` 는 자식이 아님 ② `Globe` 회전 0 · 크기 1 · 위치 0 ③ 반지름 1 ± 0.02 ④ ≤ 1.5MB · Draco / meshopt 없음 · 카메라 · 빛 · 애니메이션 없음 · 삼각형(Globe ≤ 1만 · Stand ≤ 3만) ⑤ (0,0,1) 의 u = 0.5 · (1,0,0) 의 u = 0.75 · 북극의 glTF v = 0 · 이음매의 u 는 0 과 1 ⑥ 바탕 텍스처에서 서울 · 파리 · 리우는 땅, 태평양 한가운데 · 남대서양은 바다 ⑦ `latLonToVec3(서울)` ≈ (0.633, 0.610, −0.477), 도시 아홉 방향이 지나는 삼각형의 UV 가 식과 같고(차 0.2화소 안) 서울 화소가 땅 ⑧ 받침이 화면 여백 안 · 구를 파고들지 않음.

## 테스트 결과 (참고)

| 항목 | 값 |
| --- | --- |
| Blender | 5.2.4 LTS (build hash `cd5dc428cce1`) |
| OS | Ubuntu 24.04, 4 vCPU (x86_64) |
| 렌더 시간 | 약 14~16초 (32 샘플 + OIDN 디노이즈), Blender 전체 실행 약 17초 |
| 설치 시간 | 첫 설치 약 38초(다운로드 포함), 재실행 시 1초 미만 |

## 결과물과 갤러리

결과물은 이 저장소가 아니라 그림 저장소 [blender-cloud-img](https://github.com/polos0117/blender-cloud-img) 의 `img/` 에 둔다(다른 공방의 `*-img` 저장소와 같은 방식). `renders/` 는 작업 폴더라 커밋하지 않는다.

1. 렌더 → `renders/` 에 저장
2. 웹에 쓸 것만 `blender-cloud-img/img/<날짜>_<이름>.<확장자>` 로 옮겨 push (예: `2026-11-11_room.webp`)
   - 클라우드 세션: 그림 저장소에 쓰기 권한을 붙이고 `/home/user/blender-cloud-img` 에 클론해 거기서 커밋·push 한다(2026-11-11 확인)
3. 갤러리 https://polos0117.github.io/blender-cloud/ (공방 모음 허브 맨 아래 「3D 공방」)에 바로 뜬다 — `index.html` 이 그림 저장소의 `img/` 를 GitHub API 로 읽는다. 목록 파일은 없다.

웹용 권장: 투명 배경 PNG/WebP, 2~4초 루프 영상(1MB 안팎), 압축한 .glb. iPhone Safari 는 투명 WebM 을 못 튼다.
