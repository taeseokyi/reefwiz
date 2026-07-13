#!/usr/bin/env python3
"""Windows 원본 dkh.dat·measure_kh.log 을 저장소로 동기화하고 GitHub 에 push한다.

측정(하루 3회 05/13/21시)이 끝나면 Windows 래퍼 run_measure_and_sync.py 가 wsl.exe 로
곧바로 이 스크립트를 호출한다(2026-07-05, 예전 WSL cron '30 6,14,22 * * *' 방식은 폐지
— 측정이 1.5h 를 넘기면 부분 데이터가 올라가던 문제 해소). 변경이 없으면 아무것도
커밋하지 않는다(내용이 같으면 push만 시도 — 이전 실행이 네트워크 문제로 push 실패했을
때 재시도 역할, 다음 측정 회차에 자동 재시도되는 셈).
dkh.dat 그래프(PNG) 렌더링은 여기서 하지 않는다 — push되면 GitHub Actions(plot-dkh.yml)가
data/dkh.dat 변경을 감지해 그린다. measure_kh.log(마지막 측정의 평탄 추종 곡선)는 원본이
Windows에만 있어 Actions가 못 보므로, 여기서 파싱까지 끝내 docs/dkh_plateau_history.json에
바로 커밋한다(이 산출물은 추가 렌더링이 필요 없어 Actions를 거칠 이유가 없음).
원본 로그는 1MB 넘으면 비워져 과거 실행이 사라지므로, 이 이력 파일이 유일한 누적 저장소다
— 마지막 실행만 파싱해 run_started 기준으로 upsert(진행 중 스냅샷은 완료본으로 교체)하고
최근 MAX_RUNS(42회=14일×3회)만 남긴다. 대시보드의 "최근 14일 평탄 추종 조회"가 이걸 읽는다.
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
PLATEAU_DST = os.path.join(REPO_DIR, "docs", "dkh_plateau_history.json")
DOSER_SRC = "/mnt/c/dkh/work/doser_history.json"   # doser_adjust.py 가 남기는 조정 이력
DOSER_DST = os.path.join(REPO_DIR, "docs", "doser_history.json")
MAX_RUNS = 42  # 14일 × 하루 3회 — 대시보드 조회 범위
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


def copy_if_changed(src, dst):
    """src(읽기 전용)를 dst 로 복사. 내용이 같으면 False."""
    with open(src, "rb") as f:
        src_bytes = f.read()
    if os.path.exists(dst) and open(dst, "rb").read() == src_bytes:
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(src_bytes)
    return True


def sync_dat():
    if not os.path.exists(DAT_SRC):
        log.warning("원본 없음: %s (Windows 드라이브 마운트 확인 필요)", DAT_SRC)
        return False
    return copy_if_changed(DAT_SRC, DAT_DST)


def sync_doser():
    # 도저 조정 이력 — 도입(2026-07-06) 전이나 아직 조정이 한 번도 안 돌았으면 원본이
    # 없는 게 정상이라 경고 없이 무동작.
    if not os.path.exists(DOSER_SRC):
        return False
    return copy_if_changed(DOSER_SRC, DOSER_DST)


def sync_plateau():
    if not os.path.exists(PLATEAU_SRC):
        log.warning("원본 없음: %s", PLATEAU_SRC)
        return False
    with open(PLATEAU_SRC, encoding="utf-8", errors="replace") as f:
        text = f.read()
    result = parse_plateau_log.parse_last_run(text)
    if not result or not (result["tank"] or result["ref"]):
        return False

    history = []
    if os.path.exists(PLATEAU_DST):
        try:
            with open(PLATEAU_DST) as f:
                history = json.load(f)
        except (OSError, ValueError):
            history = []
    if not isinstance(history, list):
        history = []

    # CO₂ 편향 의심 필드 lazy 백필(2026-07-13 도입) — 필드가 없는 과거 항목에만
    # 판정을 소급 적용한다. 도입 후 첫 sync 한 번으로 보관분 전체가 필드를 갖게 돼
    # 소비자(대시보드·make_dkh_json)가 규칙을 중복 구현할 필요가 없어진다.
    backfilled = False
    for r in history:
        if "co2_suspect" not in r:
            r["co2_suspect"], r["ref_net_mph"] = parse_plateau_log.classify_co2_suspect(r)
            backfilled = True

    # 같은 실행(run_started)은 교체(진행 중 스냅샷 → 완료본 갱신), 새 실행은 뒤에 추가.
    # run_started 단독 dedup으로 스킵하면 안 되는 이유는 parse_plateau_log.py 참조
    # (긴 측정의 첫 스냅샷이 영구 고정되는 버그, 2026-07-01).
    idx = next((i for i, r in enumerate(history)
                if r.get("run_started") == result["run_started"]), None)
    if idx is None:
        history.append(result)
    elif history[idx] == result and not backfilled:
        return False
    else:
        history[idx] = result
    history = history[-MAX_RUNS:]

    os.makedirs(os.path.dirname(PLATEAU_DST), exist_ok=True)
    with open(PLATEAU_DST, "w") as f:
        json.dump(history, f, ensure_ascii=False)
    return True


def sync_with_remote():
    """GitHub Actions(plot-dkh.yml)가 렌더링 결과를 origin에 직접 push하므로,
    이 로컬 저장소를 먼저 최신으로 맞추지 않으면 다음 로컬 커밋이 origin과
    갈라져 이후 push가 전부 실패한다(2026-07-01 밤 실제 발생, 약 7시간 방치).
    건드리는 파일이 서로 겹치지 않아(로컬=data/dkh.dat·dkh_plateau_history.json·
    doser_history.json, Actions=images/*·dkh_latest.json·dkh_series.json) rebase
    충돌은 나지 않는 게 정상.

    --autostash: 이 저장소에서 사람이 작업 중이라 커밋 전 변경이 있으면 rebase가
    거부돼 sync 가 사이클을 통째로 건너뛰었다(2026-07-06 13:54 실제 발생 — 대시보드가
    반나절 옛 데이터로 남음). autostash 는 그 변경을 stash→rebase→자동 복원한다.
    복원 충돌 시 git 이 stash 를 보존한 채 남기므로 유실은 없다."""
    fetch = run_git("fetch", "origin", "master")
    if fetch.returncode != 0:
        log.warning("fetch 실패(네트워크?): %s", fetch.stderr.strip())
        return False
    rebase = run_git("rebase", "--autostash", "origin/master")
    if rebase.returncode != 0:
        log.error("rebase 실패, 수동 확인 필요: %s", rebase.stderr.strip())
        run_git("rebase", "--abort")
        return False
    return True


def main():
    if not sync_with_remote():
        return  # 이번 사이클은 포기 — 로컬 상태는 안전하게 보존, 다음 사이클에 재시도

    changed_dat = sync_dat()
    changed_plateau = sync_plateau()
    changed_doser = sync_doser()

    paths = []
    if changed_dat:
        paths.append("data/dkh.dat")
    if changed_plateau:
        paths.append("docs/dkh_plateau_history.json")
    if changed_doser:
        paths.append("docs/doser_history.json")

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
