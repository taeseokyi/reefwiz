#!/usr/bin/env python3
"""평탄 추종 궤적의 무기한 보관소 (append-only JSONL).

docs/dkh_plateau_history.json 은 대시보드 조회 범위(sync_dkh_dat.KEEP_DAYS=14일, 회차
수가 아니라 날짜 기준)에 맞춰 잘라내므로 14일 지난 회차의 궤적은 파일에서 사라진다. 지금까지는 sync 가 회차마다
커밋을 남긴 덕에 git 이력에서 되살릴 수 있었지만(2026-08-04 기준 91회/34일 복원),
이력 재작성 전력이 있는 저장소라 git 스냅샷에만 기대는 건 위험하고 복원 비용도 크다.

쌓아야 하는 이유는 검정력이다 — 측정오차(이웃 회차 평균 대비 잔차)를 설명변수로
회귀했을 때 보관분 42회로는 어떤 변수도 유의하지 않았는데, git 에서 복원한 91회로
늘리자 ref net8 이 t=3.08 로 떠올랐다(2026-08-04 분석, 계수 +3.4 m-dKH/mpH).
표본외 재검증에는 60회쯤이 더 필요하고, 그 다음 과제인 05시 잔여 노이즈
(잔차 σ 0.107 vs 13·21시 0.063~0.067)는 표본이 더 있어야 손댈 수 있다.

형식은 JSONL(한 줄 = 한 회차). 이유:
  - append 만 하므로 커밋 diff 가 '추가된 줄' 뿐이다. 전체를 재작성하는 형식(단일
    JSON 배열)이면 회차마다 파일 전체가 diff 로 잡혀 저장소가 빠르게 부푼다.
  - 같은 run_started 가 갱신되면(진행 중 스냅샷 → 완료본) 줄이 하나 더 붙는다.
    읽을 때 뒤쪽 줄이 이긴다(load 가 처리) — 재작성 없이 upsert 를 흉내낸다.

두는 위치는 data/ 다. docs/ 는 GitHub Pages 로 배포되는 디렉터리라 여기에 두면
대시보드가 쓰지도 않는 파일이 배포 산출물에 실린다. 이 파일은 순수하게 사후 분석용
자산이고 대시보드·측정 어느 쪽도 읽지 않는다.

용량은 회차당 약 1.3KB, 하루 3회 → 연 1.4MB 수준이라 잘라낼 이유가 없다.
"""
import json
import os

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_PATH = os.path.join(REPO_DIR, "data", "dkh_plateau_archive.jsonl")

# 마지막 줄만 확인하면 되므로 끝에서 이만큼만 읽는다. 가장 긴 회차(ref 35점)가 약 3KB라
# 넉넉하다. 파일이 개행으로 끝나므로 잘린 앞부분이 섞여도 마지막 줄은 항상 온전하다.
_TAIL_BYTES = 65536


def _last_line(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    with open(path, "rb") as f:
        f.seek(max(0, size - _TAIL_BYTES))
        lines = f.read().splitlines()
    return lines[-1].decode("utf-8", "replace") if lines else ""


def append(run, path=ARCHIVE_PATH):
    """run 한 회차를 끝에 추가한다. 마지막 줄과 내용이 같으면 아무것도 안 하고 False.

    같은 회차가 갱신돼 붙는 중복은 load 가 걷어내므로 여기서 전체를 훑지 않는다
    (호출 비용을 파일 크기와 무관하게 유지)."""
    line = json.dumps(run, ensure_ascii=False, sort_keys=True)
    if _last_line(path) == line:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return True


def load(path=ARCHIVE_PATH):
    """run_started 기준 last-wins 로 중복을 걷고 시간순으로 정렬해 돌려준다.

    깨진 줄(쓰다 만 줄 등)은 건너뛴다 — 분석용 자산이라 한 줄 손실보다 전체를 못 읽는
    쪽이 나쁘다."""
    runs = {}
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                run = json.loads(line)
            except ValueError:
                continue
            key = run.get("run_started")
            if key:
                runs[key] = run
    return [runs[k] for k in sorted(runs)]


if __name__ == "__main__":
    runs = load()
    print(f"{ARCHIVE_PATH}: {len(runs)}회차")
    if runs:
        print(f"  {runs[0]['run_started']} ~ {runs[-1]['run_started']}")
