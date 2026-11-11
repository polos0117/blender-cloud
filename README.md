# blender-cloud

Linux 서버(GUI 없음)에서 Blender를 **headless**로 실행해 씬을 만들고 Cycles로 렌더링하는 파이프라인 테스트입니다.

![테스트 렌더](https://polos0117.github.io/blender-cloud-img/img/2026-11-11_test_render.png)

## 구성

| 파일 | 설명 |
| --- | --- |
| `setup_blender.sh` | Blender LTS(Linux x64) 다운로드·설치 스크립트. 여러 번 실행해도 안전(멱등) |
| `scenes/test_scene.py` | bpy 씬 생성 + 렌더 스크립트 |
| `scenes/test_scene.blend` | 스크립트가 저장한 씬 파일 |
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
