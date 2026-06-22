#!/usr/bin/env bash
set -euo pipefail

SOURCE="${SETUP_SKILLS_SOURCE:-/Users/cho/dev_cho/AgentRagShucle/setup-skills-and-mcp.sh}"
TARGET_NAME="setup-skills-and-mcp.sh"
COMMIT_MESSAGE="${COMMIT_MESSAGE:-잡일: setup 스크립트 동기화}"

die() {
  echo "오류: $*" >&2
  exit 1
}

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || die "git 저장소 안에서 실행해야 합니다."
TARGET="$REPO_ROOT/$TARGET_NAME"

[ -f "$SOURCE" ] || die "원본 파일을 찾을 수 없습니다: $SOURCE"

cd "$REPO_ROOT" || die "저장소 루트로 이동 실패: $REPO_ROOT"

if [ -f "$TARGET" ] && cmp -s "$SOURCE" "$TARGET"; then
  echo "변경 없음: $TARGET_NAME"
  exit 0
fi

cp -p "$SOURCE" "$TARGET" || die "파일 동기화 실패: $SOURCE -> $TARGET"
chmod +x "$TARGET" || die "실행 권한 설정 실패: $TARGET"

git add "$TARGET_NAME" || die "git add 실패: $TARGET_NAME"

if git diff --cached --quiet -- "$TARGET_NAME"; then
  echo "커밋할 변경 없음: $TARGET_NAME"
  exit 0
fi

git commit -m "$COMMIT_MESSAGE" || die "git commit 실패"
git push || die "git push 실패"

echo "동기화, 커밋, 푸시 완료: $TARGET_NAME"
