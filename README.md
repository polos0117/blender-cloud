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

공방 모음 허브 첫 화면의 세로 지도(1024×2048)를 그리기 위한 **정확한 3D 구조**를 만든다. 가운데 지하 호수와 바위섬 신전, 호수를 한 바퀴 도는 둘레 길, 양쪽 벽의 둥근 방들, 아래쪽 절벽의 입구 계단으로 된 산속 드워프 대미궁이다. 화풍 칠은 여기서 하지 않고, 뽑은 구조 그림을 이미지 모델에 첨부해 입힌다.

![대미궁 구조](https://polos0117.github.io/blender-cloud-img/img/2026-11-11_cave-structure.png)

```bash
~/blender/blender -b --python scenes/cave_hub.py -- --out renders/
```

`renders/` 에 아래 파일이 나오고 `scenes/cave_hub.blend` 가 저장된다.

| 파일 | 내용 |
| --- | --- |
| `cave-structure.png` | 구조 그림. 회색 점토 재질(지은 것은 밝게, 바위는 중간, 물은 어둡게) + 그림자 |
| `cave-lines.png` | 선 그림. 흰 바탕에 검은 윤곽선 |
| `cave-ids.png` · `cave-ids.json` | 방 색깔 지도(안티에일리어싱 없음, 나머지는 검정)와 색 ↔ 방 열쇠 표 |
| `cave-depth.png` | 깊이 그림. 가까울수록 밝은 8비트 회색조 |
| `cave-rooms.json` | 허브용 좌표: 방마다 바닥 다각형 `poly`(8각) · 이름표 자리 `at`, 길 셋 `ways`(SVG path) |
| `cave-report.json` | 검사 결과와 렌더 시간 (올리지 않는 작업 기록) |

그림 저장소에 올릴 때는 날짜를 붙여 복사한다.

```bash
for f in cave-structure.png cave-lines.png cave-ids.png cave-ids.json cave-depth.png cave-rooms.json; do cp renders/$f ../blender-cloud-img/img/$(date +%F)_$f; done
```

### 방 넣고 빼기

방 목록과 배치 값은 모두 `scenes/cave_hub.json` 에 있다. `rooms` 에 한 줄을 넣고 다시 돌리면 그 벽의 방 수로 세로를 다시 나누고(자리가 모자라면 모든 방의 반지름을 같이 줄인다), 복도 · 난간 틈 · 좌표 · 색 표가 다시 나온다.

```json
{ "key": "newroom", "name": "새 방", "side": "left", "rise": 1.0 }
```

- 순서: 각 벽(`left` / `right`)에서 적힌 차례가 위 → 아래. `island` 는 가운데 섬(다각형은 호수 영역).
- `rise`: 둘레 길보다 높은 정도. 2.0 이상이면 나선 계단, 그보다 낮으면 곧은 계단으로 잇는다.
- `color`: 색깔 지도의 색. 안 적으면 `palette` 에서 남는 색을 받는다(기존 방의 색은 그대로).

### 구조와 검사

- 좌표계: X = 좌우, +Y = 그림 위쪽, Z = 높이(호수 수면 0, 둘레 길 1.0, 방 바닥 1.4~3.4, 입구 −2.6). 카메라는 남쪽 위 60°에서 내려다보는 정사영이라 투영이 단순하다.
- 길 검사: 방마다 둘레 길 → 다리 → 계단(또는 나선 계단) → 발코니 → 문 → 방 가운데까지 가운뎃줄을 5cm 간격으로 따라가며 아래로 광선을 쏴서, 걷는 면이 이어지는지 · 한 단보다 높은 턱이 없는지 · 난간이나 벽에 막히지 않는지 본다. 둘레 길 한 바퀴, 입구 계단, 섬 다리도 같이 본다. 하나라도 끊기면 종료 코드 2.
- 겹침 검사: 방끼리, 방과 둘레 길, 방과 입구 골짜기 사이 간격을 계산한다.
- 검사가 실제로 잡아내는지는 `--selftest-break city` 로 본다(그 방의 연결로를 빼고 만들면 실패해야 정상).
- 선 그림은 Freestyle 대신 1샘플 데이터 패스(법선 · 깊이 · 오브젝트 번호)를 2배 크기로 구워 경계를 찾는다. 서로 파고든 덩어리의 경계와 물가 선도 빠지지 않는다. 색깔 지도도 같은 패스의 방 번호에서 만들어 색이 정확히 떨어진다.
- 색깔 지도에서 `element`(섬) 색은 허브 다각형과 맞춰 호수 수면 · 섬 · 신전 · 다리 · 호수 바위를 덮는다. 방 색은 바닥 · 벽 · 문을 덮는다.

옵션: `--prefix 2026-11-11_`(파일 이름 앞), `--samples N`, `--scale 0.5`(미리보기), `--check-only`(렌더 없이 검사와 JSON 만), `--no-blend`, `--config 다른설정.json`.

2026-11-11 기준(4 vCPU, GPU 없음): 구조 그림 98초(64 샘플 + 디노이즈), 데이터 패스 5초, 전체 107초. 삼각형 약 25만 개.

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
