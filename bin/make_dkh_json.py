#!/usr/bin/env python3
"""dkh.dat 을 읽어 대시보드용 JSON 2종을 낸다(그래프 렌더링 없음, 의존성 없음).

dkh.dat 형식(공백 구분, 한 줄에 하나 — 파싱은 dkh_dat.py 단일 규약):
  YYYY-MM-DD HH ref_pH tank_pH ref_kh tank_kh temp  (2026-08-16~)
  - 5개 값 전부 0.000  → 에러 표식(측정 실패/타임아웃/KCl 소크 실패), 스킵
  - tank_kh 가 음수    → 평탄(평형) 미도달 표식. 크기는 유지되므로 abs() 로 값만 취하고 따로 표시
  - 날짜는 파일에 직접 적혀 있다. 회차 근사(하루 3회 가정)나 git blame 복원 같은
    우회로는 2026-08-16 에 전부 걷어냈다 — 소비자는 date 를 그대로 읽는다.
  - --recent-days: "최근 N일"을 날짜로 정확히 자른다. 측정이 빠진 날이 있어도 창이
    늘어나지 않고, 추가 측정을 돌린 날이 있어도 안쪽 데이터가 안 밀린다.
    기준일은 오늘이 아니라 마지막 행의 날짜.
  - --plateau: dkh_plateau_history.json 의 런과 (date, hh) 로 매칭해 CO₂ 편향 의심
    필드(co2_suspect)를 행에 주입한다.
    이력에 없는 행(보관 14일 밖 등)은 필드 자체를 생략 — 소비자(JS)는 falsy=미의심 처리.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dkh_dat


def load(path):
    """dkh.dat → 시리즈 행 목록. 날짜(date)는 파일에 적힌 값을 그대로 싣는다."""
    rows = []
    for r in dkh_dat.load(path):
        if r["ref_kh"] == 0.0 and r["tank_kh"] == 0.0:
            continue  # 에러 표식(전부 0) — 스킵
        row = {
            "line": r["line"], "hh": r["hh"], "ref_kh": r["ref_kh"],
            "tank_kh": abs(r["tank_kh"]), "temp": r["temp"], "is_flat": r["is_flat"],
        }
        if r["date"]:
            row["date"] = r["date"]
        rows.append(row)
    return rows


def plateau_flags(plateau_path):
    """dkh_plateau_history.json → {(date, hh): co2_suspect} 매핑. 실패 시 None.

    run_started("2026-07-13 05:00:02")의 날짜·시가 곧 측정 시작 시각이고, dkh.dat 의
    날짜도 측정 시작일(자정을 넘겨 끝나도 시작일 귀속)이라 행의 (date, HH)와 1:1
    대응한다. 같은 키가 중복되면 뒤(최신) 런이 이긴다.
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


def slice_recent_days(rows, days):
    """date 기준으로 최근 `days`일치만 남긴다.

    기준일(앵커)은 오늘이 아니라 **마지막 기록일** — 오늘 회차가 아직 없는 시간대에
    가장 오래된 하루가 잘려나가 days-1 일치만 보이는 것을 막는다. 순서가 뒤섞여
    있어도 날짜로 판정하므로 창 길이는 정확히 days 일이다.
    날짜 없는 행(구형식 백업본)은 창 밖으로 본다 — 날짜가 하나도 없으면 None.
    """
    dated = [r["date"] for r in rows if r.get("date")]
    if not dated:
        return None
    cut = (datetime.date.fromisoformat(max(dated))
           - datetime.timedelta(days=days - 1)).isoformat()
    return [r for r in rows if r.get("date", "") >= cut]


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
    ap.add_argument("--recent-days", type=int, default=None,
                     help="series-json에 담을 최근 N일(날짜 기준). 생략하면 전 구간")
    ap.add_argument("--plateau", default=None,
                     help="dkh_plateau_history.json 경로 — (date,hh) 매칭으로 co2_suspect 주입")
    args = ap.parse_args()

    rows = load(args.dat_file)
    if not rows:
        raise SystemExit("표시할 데이터가 없습니다(전부 에러 표식이거나 파일이 비어있음).")

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
        series_rows = rows
        if args.recent_days:
            series_rows = slice_recent_days(rows, args.recent_days)
            if series_rows is None:  # 날짜 없는 구형식 파일 — 자르지 않고 전부
                print("경고: 날짜 있는 행이 없음 — 최근 N일 컷 생략(전 구간 출력)",
                      file=sys.stderr)
                series_rows = rows
        write_series_json(series_rows, args.series_json)
