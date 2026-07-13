#!/usr/bin/env python3
"""dkh.dat 을 읽어 대시보드용 JSON 2종을 낸다(그래프 렌더링 없음, 의존성 없음).

dkh.dat 형식(공백 구분, 한 줄에 하나): HH ref_pH tank_pH ref_kh tank_kh temp
  - 5개 값 전부 0.000  → 에러 표식(측정 실패/타임아웃/KCl 소크 실패), 스킵
  - tank_kh 가 음수    → 평탄(평형) 미도달 표식. 크기는 유지되므로 abs() 로 값만 취하고 따로 표시
  - 파일에 날짜가 없다(시각 HH만 기록) → --recent 는 "최근 N건"(회수) 근사다.
  - --dates-from-git: 각 행이 append 된 커밋 시각(git blame)으로 날짜를 복원해
    "date"(YYYY-MM-DD) 필드를 붙인다. git 이력이 없으면 조용히 날짜 없이 동작.
  - --plateau: dkh_plateau_history.json 의 런과 (date, hh) 로 매칭해 CO₂ 편향 의심
    필드(co2_suspect)를 행에 주입한다(--dates-from-git 필요 — date 없는 행은 매칭 불가).
    이력에 없는 행(보관 14일 밖 등)은 필드 자체를 생략 — 소비자(JS)는 falsy=미의심 처리.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

KST = datetime.timezone(datetime.timedelta(hours=9))


def load(path):
    rows = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            parts = line.split()
            if len(parts) < 5:
                continue
            hh, _ref_ph, _tank_ph, ref_kh, tank_kh, *rest = parts
            temp = float(rest[0]) if rest else float("nan")
            ref_kh_v = float(ref_kh)
            tank_kh_v = float(tank_kh)
            if ref_kh_v == 0.0 and tank_kh_v == 0.0:
                continue  # 에러 표식(전부 0) — 스킵
            is_flat = tank_kh_v >= 0
            rows.append({
                "line": lineno, "hh": int(hh), "ref_kh": ref_kh_v,
                "tank_kh": abs(tank_kh_v), "temp": temp, "is_flat": is_flat,
            })
    return rows


def git_line_dates(dat_path):
    """파일 각 줄의 날짜(YYYY-MM-DD 리스트, 1-base 줄번호 순)를 git 커밋 시각으로 복원.

    행은 측정 시작(HH시) 약 1.5시간 뒤 크론 커밋으로 append 되므로 커밋 날짜≈측정 날짜.
    - 커밋 시각의 시(hour)가 행의 HH보다 이르면 자정을 넘긴 지연 커밋 → 하루 빼기.
    - 여러 행이 한 커밋에 들어온 구간(최초 시드 등)은 blame 날짜가 전부 같아지므로,
      뒤에서부터 "HH가 다음 행보다 작지 않으면 날짜 경계"라는 하루 내 단조증가
      성질로 상한을 걸어 보정한다(과거로 갈수록 근사).
    실패(git 없음/이력 없음) 시 None — 호출부는 날짜 없이 동작한다.
    """
    try:
        out = subprocess.run(
            ["git", "blame", "--line-porcelain", "--", os.path.basename(dat_path)],
            cwd=os.path.dirname(os.path.abspath(dat_path)) or ".",
            capture_output=True, text=True, check=True,
        ).stdout
    except Exception as e:
        print(f"경고: git blame 실패 — 날짜 없이 진행 ({e})", file=sys.stderr)
        return None

    entries = []  # 줄 순서대로 (커밋 epoch, 행의 HH 또는 None)
    epoch = None
    for ln in out.splitlines():
        if ln.startswith("author-time "):
            epoch = int(ln.split()[1])
        elif ln.startswith("\t"):
            first = ln[1:].split()[:1]
            hh = int(first[0]) if first and first[0].isdigit() else None
            entries.append((epoch, hh))
    if not entries:
        return None

    dates = []
    for ep, hh in entries:
        dt = datetime.datetime.fromtimestamp(ep, KST)
        d = dt.date()
        if hh is not None and dt.hour < hh:
            d -= datetime.timedelta(days=1)
        dates.append(d)
    for i in range(len(entries) - 2, -1, -1):
        hh_i, hh_next = entries[i][1], entries[i + 1][1]
        if hh_i is not None and hh_next is not None and hh_i >= hh_next:
            max_d = dates[i + 1] - datetime.timedelta(days=1)
        else:
            max_d = dates[i + 1]
        if dates[i] > max_d:
            dates[i] = max_d
    return [d.isoformat() for d in dates]


def plateau_flags(plateau_path):
    """dkh_plateau_history.json → {(date, hh): co2_suspect} 매핑. 실패 시 None.

    run_started("2026-07-13 05:00:02")의 날짜·시가 곧 측정 시작 시각이라 행의
    (복원 date, HH)와 1:1 대응한다(git_line_dates 의 자정 넘김 보정이 커밋 지연을
    이미 흡수). 같은 키가 중복되면 뒤(최신) 런이 이긴다.
    """
    try:
        with open(plateau_path) as f:
            runs = json.load(f)
    except (OSError, ValueError) as e:
        print(f"경고: plateau 이력 읽기 실패 — 플래그 없이 진행 ({e})", file=sys.stderr)
        return None
    if not isinstance(runs, list):
        return None
    flags = {}
    for run in runs:
        rs = run.get("run_started") or ""
        try:
            key = (rs[:10], int(rs[11:13]))
        except (ValueError, IndexError):
            continue
        if "co2_suspect" in run:
            suspect = bool(run["co2_suspect"])
        else:
            # 백필(sync_dkh_dat.py) 전 과도기 — 판정 함수로 즉석 재계산
            import parse_plateau_log  # bin/ 동봉 모듈(스크립트 디렉토리가 sys.path 에 있음)
            suspect, _ = parse_plateau_log.classify_co2_suspect(run)
        flags[key] = suspect
    return flags


def row_json(r):
    out = {k: v for k, v in r.items() if k != "line"}
    return out


def write_latest_json(rows, path):
    latest = dict(row_json(rows[-1]), count=len(rows))
    with open(path, "w") as f:
        json.dump(latest, f, ensure_ascii=False)
    print(f"저장: {path}")


def write_series_json(rows, path):
    """대시보드 인터랙티브 차트용 — 최근 구간을 배열로 내보낸다."""
    with open(path, "w") as f:
        json.dump([row_json(r) for r in rows], f, ensure_ascii=False)
    print(f"저장: {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dat_file", nargs="?", default="/mnt/c/dkh/work/dkh.dat",
                     help="dkh.dat 경로 (기본: Windows 원본, WSL에서 /mnt/c 경유)")
    ap.add_argument("--json", default=None, help="최신 측정값 JSON 출력 경로(옵션)")
    ap.add_argument("--series-json", default=None, help="최근 구간 배열 JSON 출력 경로(옵션)")
    ap.add_argument("--recent", type=int, default=None,
                     help="series-json에 담을 최근 N건(옵션) — json(최신값)은 항상 전체 마지막 행 기준")
    ap.add_argument("--dates-from-git", action="store_true",
                     help="git blame 커밋 시각으로 각 행의 날짜를 복원해 date 필드 추가")
    ap.add_argument("--plateau", default=None,
                     help="dkh_plateau_history.json 경로 — (date,hh) 매칭으로 co2_suspect 주입")
    args = ap.parse_args()

    rows = load(args.dat_file)
    if not rows:
        raise SystemExit("표시할 데이터가 없습니다(전부 에러 표식이거나 파일이 비어있음).")

    if args.dates_from_git:
        dates = git_line_dates(args.dat_file)
        if dates:
            for r in rows:
                if r["line"] <= len(dates):
                    r["date"] = dates[r["line"] - 1]

    if args.plateau:
        flags = plateau_flags(args.plateau)
        if flags:
            for r in rows:
                key = (r.get("date"), r["hh"])
                if key in flags:
                    r["co2_suspect"] = flags[key]

    if args.json:
        write_latest_json(rows, args.json)
    if args.series_json:
        series_rows = rows[-args.recent:] if args.recent else rows
        write_series_json(series_rows, args.series_json)
