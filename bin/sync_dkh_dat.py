#!/usr/bin/env python3
"""Windows 원본 dkh.dat·measure_kh.log 을 저장소로 동기화하고 GitHub 에 push한다.

cron으로 주기 실행(예: */20 * * * *). 변경이 없으면 아무것도 커밋하지 않는다
(내용이 같으면 push만 시도 — 이전 실행이 네트워크 문제로 push 실패했을 때 재시도 역할).
dkh.dat 그래프(PNG) 렌더링은 여기서 하지 않는다 — push되면 GitHub Actions(plot-dkh.yml)가
data/dkh.dat 변경을 감지해 그린다. measure_kh.log(마지막 측정의 평탄 추종 곡선)는 원본이
Windows에만 있어 Actions가 못 보므로, 여기서 파싱까지 끝내 docs/dkh_plateau.json으로
바로 커밋한다(이 산출물은 추가 렌더링이 필요 없어 Actions를 거칠 이유가 없음).
"""
import fcntl
import json
import logging
import os
import subprocess
import sys

import parse_plateau_log

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAT_SRC = "/mnt/c/dkh/work/dkh.dat"
DAT_DST = os.path.join(REPO_DIR, "data", "dkh.dat")
PLATEAU_SRC = "/mnt/c/dkh/measure_kh.log"
PLATEAU_DST = os.path.join(REPO_DIR, "docs", "dkh_plateau.json")
LOCK_FILE = "/tmp/dkh_sync.lock"
LOG_FILE = os.path.expanduser("~/dkh_sync.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def run_git(*args):
    return subprocess.run(
        ["git", "-C", REPO_DIR, *args],
        capture_output=True, text=True,
    )


def sync_dat():
    if not os.path.exists(DAT_SRC):
        log.warning("원본 없음: %s (Windows 드라이브 마운트 확인 필요)", DAT_SRC)
        return False
    with open(DAT_SRC, "rb") as f:
        src_bytes = f.read()
    if os.path.exists(DAT_DST) and open(DAT_DST, "rb").read() == src_bytes:
        return False
    os.makedirs(os.path.dirname(DAT_DST), exist_ok=True)
    with open(DAT_DST, "wb") as f:
        f.write(src_bytes)
    return True


def sync_plateau():
    if not os.path.exists(PLATEAU_SRC):
        log.warning("원본 없음: %s", PLATEAU_SRC)
        return False
    with open(PLATEAU_SRC, encoding="utf-8", errors="replace") as f:
        text = f.read()
    result = parse_plateau_log.parse_last_run(text)
    if not result or not (result["tank"] or result["ref"]):
        return False

    prev_run = None
    if os.path.exists(PLATEAU_DST):
        try:
            with open(PLATEAU_DST) as f:
                prev_run = json.load(f).get("run_started")
        except (OSError, ValueError):
            prev_run = None
    if result["run_started"] == prev_run:
        return False

    os.makedirs(os.path.dirname(PLATEAU_DST), exist_ok=True)
    with open(PLATEAU_DST, "w") as f:
        json.dump(result, f, ensure_ascii=False)
    return True


def main():
    changed_dat = sync_dat()
    changed_plateau = sync_plateau()

    paths = []
    if changed_dat:
        paths.append("data/dkh.dat")
    if changed_plateau:
        paths.append("docs/dkh_plateau.json")

    if paths:
        run_git("add", *paths)
        commit = run_git("commit", "-m", f"data: 측정 동기화 (자동, {', '.join(paths)})")
        if commit.returncode == 0:
            log.info("커밋됨: %s", commit.stdout.strip().splitlines()[0] if commit.stdout else "")
        else:
            log.warning("커밋 실패/변경없음: %s", commit.stderr.strip())

    push = run_git("push")
    if push.returncode == 0:
        if paths:
            log.info("push 완료")
    else:
        log.error("push 실패: %s", push.stderr.strip())


if __name__ == "__main__":
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)  # 이전 실행이 아직 진행 중 — 조용히 종료
    try:
        main()
    except Exception:
        log.exception("동기화 중 예외")
