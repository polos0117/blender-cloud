#!/usr/bin/env bash
# Headless Blender 설치 스크립트 (Linux x64, 멱등).
#
# - download.blender.org 에서 LTS tarball 을 받아 $BLENDER_DIR 에 풉니다.
# - 이미 같은 버전이 설치돼 있고 실행되면 아무것도 하지 않습니다.
# - `blender --version` 이 실패하면 필요한 공유 라이브러리를 apt 로 설치합니다.
#
# 환경 변수:
#   BLENDER_SERIES   LTS 시리즈 (기본 5.2)
#   BLENDER_VERSION  정확한 버전 (기본: 시리즈의 최신 패치 버전을 자동 탐색)
#   BLENDER_DIR      설치 위치 (기본 ~/blender)
#   BLENDER_CACHE    tarball 캐시 위치 (기본 ~/.cache/blender-download)
#   FORCE=1          설치돼 있어도 다시 설치
set -euo pipefail

BLENDER_SERIES="${BLENDER_SERIES:-5.2}"
BLENDER_DIR="${BLENDER_DIR:-$HOME/blender}"
BLENDER_CACHE="${BLENDER_CACHE:-$HOME/.cache/blender-download}"
BASE_URL="https://download.blender.org/release/Blender${BLENDER_SERIES}"
APT_PACKAGES=(libxi6 libxxf86vm1 libxfixes3 libxrender1 libgl1 libxkbcommon0 libsm6)

log() { printf '[setup_blender] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

[[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] || die "Linux x86_64 전용 스크립트입니다."
for cmd in curl tar xz sha256sum; do
  command -v "$cmd" >/dev/null || die "'$cmd' 명령이 필요합니다."
done

# 1) 버전 결정 ---------------------------------------------------------------
if [[ -z "${BLENDER_VERSION:-}" ]]; then
  log "Blender ${BLENDER_SERIES} 최신 패치 버전 조회 중..."
  BLENDER_VERSION="$(curl -fsSL --retry 3 "${BASE_URL}/" \
    | grep -oE "blender-${BLENDER_SERIES//./\\.}\.[0-9]+-linux-x64\.tar\.xz" \
    | sed -E 's/^blender-(.*)-linux-x64\.tar\.xz$/\1/' \
    | sort -uV | tail -n1)"
  [[ -n "$BLENDER_VERSION" ]] || die "${BASE_URL}/ 에서 Linux tarball 을 찾지 못했습니다."
fi
TARBALL="blender-${BLENDER_VERSION}-linux-x64.tar.xz"
log "대상 버전: ${BLENDER_VERSION} -> ${BLENDER_DIR}"

blender_ok() { "${BLENDER_DIR}/blender" --factory-startup --version >/dev/null 2>&1; }

install_libs() {
  log "필요한 라이브러리 apt 설치: ${APT_PACKAGES[*]}"
  local sudo=""
  if [[ "$(id -u)" -ne 0 ]]; then
    command -v sudo >/dev/null || die "root 권한(또는 sudo)이 필요합니다."
    sudo="sudo"
  fi
  $sudo apt-get update -qq
  DEBIAN_FRONTEND=noninteractive $sudo apt-get install -y -qq --no-install-recommends "${APT_PACKAGES[@]}"
}

# 2) 이미 설치돼 있으면 건너뛰기 --------------------------------------------
if [[ "${FORCE:-0}" != "1" && -x "${BLENDER_DIR}/blender" \
      && "$(cat "${BLENDER_DIR}/.installed_version" 2>/dev/null)" == "${BLENDER_VERSION}" ]]; then
  if blender_ok || { install_libs && blender_ok; }; then
    log "이미 설치됨: $("${BLENDER_DIR}/blender" --factory-startup --version 2>/dev/null | head -n1)"
    exit 0
  fi
  log "설치본이 실행되지 않아 다시 설치합니다."
fi

# 3) 다운로드 + 체크섬 검증 (캐시 재사용) ----------------------------------
mkdir -p "$BLENDER_CACHE"
SUMS="$(curl -fsSL --retry 3 "${BASE_URL}/blender-${BLENDER_VERSION}.sha256")" \
  || die "체크섬 파일을 받지 못했습니다."
EXPECTED="$(awk -v f="$TARBALL" '$2 == f {print $1}' <<<"$SUMS")"
[[ -n "$EXPECTED" ]] || die "체크섬 목록에 ${TARBALL} 이 없습니다."

verify() { [[ -f "$1" ]] && [[ "$(sha256sum "$1" | awk '{print $1}')" == "$EXPECTED" ]]; }

if verify "${BLENDER_CACHE}/${TARBALL}"; then
  log "캐시된 tarball 사용: ${BLENDER_CACHE}/${TARBALL}"
else
  log "다운로드: ${BASE_URL}/${TARBALL}"
  curl -fL --retry 3 --progress-bar -o "${BLENDER_CACHE}/${TARBALL}.part" "${BASE_URL}/${TARBALL}"
  mv "${BLENDER_CACHE}/${TARBALL}.part" "${BLENDER_CACHE}/${TARBALL}"
  verify "${BLENDER_CACHE}/${TARBALL}" || { rm -f "${BLENDER_CACHE}/${TARBALL}"; die "SHA256 불일치"; }
fi

# 4) 임시 폴더에 풀고 교체 (중간에 실패해도 기존 설치가 깨지지 않음) ------
STAGE="$(mktemp -d "${BLENDER_DIR%/}.stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
log "압축 해제 중..."
tar -xJf "${BLENDER_CACHE}/${TARBALL}" -C "$STAGE" --strip-components=1
echo "$BLENDER_VERSION" > "${STAGE}/.installed_version"
rm -rf "$BLENDER_DIR"
mv "$STAGE" "$BLENDER_DIR"
trap - EXIT

# 5) 실행 확인, 실패 시 라이브러리 설치 후 재시도 --------------------------
if ! blender_ok; then
  log "blender --version 실패 -> 라이브러리 설치 후 재시도"
  install_libs
  blender_ok || { "${BLENDER_DIR}/blender" --factory-startup --version || true; die "Blender 실행 실패"; }
fi
log "설치 완료: $("${BLENDER_DIR}/blender" --factory-startup --version 2>/dev/null | head -n1)"
