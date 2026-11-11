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
