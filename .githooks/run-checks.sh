#!/bin/sh
# Commit check shared by pre-commit and pre-merge-commit (spec §12).
#
# Runs the whole test suite, including the architecture check, with the Python of the main
# working folder's .venv: the folder above `git rev-parse --git-common-dir`, so a linked
# worktree uses the same Python. Any failure, or no Python, blocks the commit.
# Never skip it with --no-verify.

block() {
    printf '%s\n' "$1" >&2
    exit 1
}

common_dir=$(git rev-parse --path-format=absolute --git-common-dir) ||
    block "커밋 검사를 시작하지 못해서 커밋을 막았습니다: git 저장소 위치를 찾지 못했습니다."
main_dir=$(dirname "$common_dir")

if [ -x "$main_dir/.venv/Scripts/python.exe" ]; then
    python="$main_dir/.venv/Scripts/python.exe"
elif [ -x "$main_dir/.venv/bin/python" ]; then
    python="$main_dir/.venv/bin/python"
else
    block "커밋 검사용 파이썬을 찾지 못해서 커밋을 막았습니다: $main_dir/.venv/Scripts/python.exe 와 $main_dir/.venv/bin/python 이 모두 없습니다. README의 설치 순서대로 .venv를 만든 뒤 다시 커밋하세요."
fi

top=$(git rev-parse --show-toplevel) ||
    block "커밋 검사를 시작하지 못해서 커밋을 막았습니다: 작업 폴더 위치를 찾지 못했습니다."
cd "$top" || block "커밋 검사를 시작하지 못해서 커밋을 막았습니다: $top 폴더로 이동하지 못했습니다."

# UTF-8 output: on a pipe Python would use the ANSI code page (949 here), and pytest prints a
# whole line as \uXXXX escapes when one character of it (such as the rule messages' "—") does
# not fit, so the architecture check's Korean messages would be unreadable.
PYTHONIOENCODING=utf-8 "$python" -m pytest -q -p no:cacheprovider ||
    block "구조 규칙 검사 또는 테스트가 실패해서 커밋을 막았습니다. 위 실패 내용을 고친 뒤 다시 커밋하세요."
