#!/usr/bin/env python3
"""측정 → GitHub Pages 동기화 연쇄 실행 래퍼 (Windows 스케줄러 "Measure KH" 진입점).

measure_kh_once.py 를 예전과 똑같이 실행하고(측정 로직·인자 무수정), 끝나면 곧바로
WSL 쪽 sync_dkh_dat.py 를 호출해 dkh.dat·평탄 이력을 저장소에 커밋/push 한다
(push 되면 GitHub Actions 가 렌더링 → Pages 갱신).
월·목 13시 회차는 측정과 동기화 사이에 doser_adjust.py(AFR 도저 자동 조정)를 끼워
실행한다(2026-07-06) — 조정 이력(doser_history.json)이 같은 사이클의 동기화로
대시보드에 올라가도록 sync 앞에 둔다. 조정 실패·타임아웃은 동기화를 막지 않는다.

예전에는 WSL cron(30 6,14,22 * * *)이 측정 시작 +1.5h 에 돌았는데, 측정이 1.5h 를
넘기면 그 회차가 부분 데이터로 올라가는 문제가 있어 완료 직후 실행으로 바꿈(2026-07-05).
동기화는 측정이 완전히 끝난 뒤에만 시작되고, 실패해도 측정 결과(dkh.dat·로그)에는
아무 영향이 없다 — push 실패분은 다음 측정 회차의 push 재시도로 복구된다(sync 쪽 로직).

이 파일의 원본은 저장소 bin/ 이고 배포본은 C:\\dkh\\work\\ (수정 시 재복사 필수).
동기화 호출 결과는 C:\\dkh\\sync_trigger.log 에, sync 자체 로그는 WSL ~/dkh_sync.log 에 남는다.
"""
import datetime
import os
import subprocess
import sys

MEASURE_SCRIPT = r"C:\dkh\work\measure_kh_once.py"
ADJUST_SCRIPT = r"C:\dkh\work\doser_adjust.py"
ADJUST_WEEKDAYS = (0, 3)   # 월, 목 — 13시 측정 종료 후 도저 조정(주 2회)
ADJUST_TIMEOUT_S = 5 * 60
WSL_EXE = r"C:\Windows\System32\wsl.exe"
SYNC_CMD = [
    WSL_EXE, "-d", "Ubuntu", "--",
    "/home/tsyi/miniconda3/envs/openclaw/bin/python3",
    "/home/tsyi/work/reefwiz/bin/sync_dkh_dat.py",
]
SYNC_TIMEOUT_S = 15 * 60  # git fetch/push 포함 넉넉히. 스케줄러 상한(5h)은 측정 4h 뒤라 여유 있음
TRIGGER_LOG = r"C:\dkh\sync_trigger.log"
LOG_MAX_BYTES = 256 * 1024
CREATE_NO_WINDOW = 0x08000000  # pythonw 환경이지만 자식(wsl.exe)이 콘솔을 새로 띄우지 않게


def log(msg):
    try:
        if os.path.exists(TRIGGER_LOG) and os.path.getsize(TRIGGER_LOG) > LOG_MAX_BYTES:
            os.replace(TRIGGER_LOG, TRIGGER_LOG + ".old")
        with open(TRIGGER_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass  # 로그 실패가 흐름을 막으면 안 됨


def is_adjust_slot(start):
    """월·목 13시 측정 회차인가(도저 조정 실행 여부). 시작 시각 기준으로 판정."""
    return start.weekday() in ADJUST_WEEKDAYS and 12 <= start.hour <= 14


def main():
    start = datetime.datetime.now()

    # 1) 측정 — 예전 스케줄러가 하던 것과 동일한 실행(같은 pythonw, 같은 인자, 같은 cwd)
    measure = subprocess.run(
        [sys.executable, MEASURE_SCRIPT],
        cwd=r"C:\dkh\work",
        creationflags=CREATE_NO_WINDOW,
    )
    log(f"측정 종료 exit={measure.returncode} → 동기화 시작")

    # 1.5) 도저 조정(월·목 13시 회차만) — sync 앞에 두어 조정 이력이 같은 사이클에
    #      대시보드로 올라가게 한다. 실패해도 sync 를 막지 않는다.
    if is_adjust_slot(start):
        try:
            adj = subprocess.run(
                [sys.executable, ADJUST_SCRIPT],
                cwd=r"C:\dkh\work",
                capture_output=True,
                timeout=ADJUST_TIMEOUT_S,
                creationflags=CREATE_NO_WINDOW,
            )
            log(f"도저 조정 종료 exit={adj.returncode} (상세는 doser_adjust.log)")
        except subprocess.TimeoutExpired:
            log(f"도저 조정 타임아웃({ADJUST_TIMEOUT_S}s) — 도저는 기존 설정으로 계속 동작")
        except OSError as e:
            log(f"도저 조정 실행 실패: {e}")

    # 2) 동기화 — 측정 성공/실패와 무관하게 실행(에러 래치·미평탄 음수도 대시보드에 올라가야 함)
    try:
        sync = subprocess.run(
            SYNC_CMD,
            capture_output=True,
            timeout=SYNC_TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
        )
        out = (sync.stdout + sync.stderr).decode("utf-8", errors="replace").strip()
        log(f"동기화 종료 exit={sync.returncode}" + (f" | {out}" if out else ""))
    except subprocess.TimeoutExpired:
        log(f"동기화 타임아웃({SYNC_TIMEOUT_S}s) — 다음 회차에 재시도됨")
    except OSError as e:
        log(f"동기화 실행 실패: {e}")


if __name__ == "__main__":
    main()
