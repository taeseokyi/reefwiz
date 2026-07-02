#!/usr/bin/env python3
"""dkh.dat 을 읽어 대시보드용 JSON 2종을 낸다(그래프 렌더링 없음, 의존성 없음).

dkh.dat 형식(공백 구분, 한 줄에 하나): HH ref_pH tank_pH ref_kh tank_kh temp
  - 5개 값 전부 0.000  → 에러 표식(측정 실패/타임아웃/KCl 소크 실패), 스킵
  - tank_kh 가 음수    → 평탄(평형) 미도달 표식. 크기는 유지되므로 abs() 로 값만 취하고 따로 표시
  - 파일에 날짜가 없다(시각 HH만 기록) → --recent 는 "최근 N건"(회수) 근사다.
"""
import argparse
import json


def load(path):
    rows = []  # (hh, ref_kh, tank_kh, temp, is_flat)
    with open(path) as f:
        for line in f:
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
            rows.append((int(hh), ref_kh_v, abs(tank_kh_v), temp, is_flat))
    return rows


def write_latest_json(rows, path):
    hh, ref_kh, tank_kh, temp, is_flat = rows[-1]
    with open(path, "w") as f:
        json.dump({
            "hh": hh, "ref_kh": ref_kh, "tank_kh": tank_kh,
            "temp": temp, "is_flat": is_flat, "count": len(rows),
        }, f, ensure_ascii=False)
    print(f"저장: {path}")


def write_series_json(rows, path):
    """대시보드 인터랙티브 차트용 — 최근 구간을 배열로 내보낸다."""
    series = [
        {"hh": hh, "ref_kh": ref_kh, "tank_kh": tank_kh, "temp": temp, "is_flat": is_flat}
        for hh, ref_kh, tank_kh, temp, is_flat in rows
    ]
    with open(path, "w") as f:
        json.dump(series, f, ensure_ascii=False)
    print(f"저장: {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dat_file", nargs="?", default="/mnt/c/dkh/work/dkh.dat",
                     help="dkh.dat 경로 (기본: Windows 원본, WSL에서 /mnt/c 경유)")
    ap.add_argument("--json", default=None, help="최신 측정값 JSON 출력 경로(옵션)")
    ap.add_argument("--series-json", default=None, help="최근 구간 배열 JSON 출력 경로(옵션)")
    ap.add_argument("--recent", type=int, default=None,
                     help="series-json에 담을 최근 N건(옵션) — json(최신값)은 항상 전체 마지막 행 기준")
    args = ap.parse_args()

    rows = load(args.dat_file)
    if not rows:
        raise SystemExit("표시할 데이터가 없습니다(전부 에러 표식이거나 파일이 비어있음).")

    if args.json:
        write_latest_json(rows, args.json)
    if args.series_json:
        series_rows = rows[-args.recent:] if args.recent else rows
        write_series_json(series_rows, args.series_json)
