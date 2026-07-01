#!/usr/bin/env python3
"""dkh.dat 을 읽어 tank/ref dKH 추세 그래프를 그린다.

dkh.dat 형식(공백 구분, 한 줄에 하나): HH ref_pH tank_pH ref_kh tank_kh temp
  - 5개 값 전부 0.000  → 에러 표식(측정 실패/타임아웃/KCl 소크 실패), 스킵
  - tank_kh 가 음수    → 평탄(평형) 미도달 표식. 크기는 유지되므로 abs() 로 값만 취하고 따로 표시
  - 파일에 날짜가 없다(시각 HH만 기록) → 가로축은 파일에 적힌 순서(행 순번)이고,
    눈금에는 HH를 함께 표기한다. 절대 날짜가 필요하면 docs/measurement-ledger.md 의
    타임스탬프와 대조해야 한다(이 파일만으로는 복원 불가).
"""
import argparse
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

matplotlib.rcParams["font.family"] = "NanumGothic"
matplotlib.rcParams["axes.unicode_minus"] = False


def load(path):
    rows = []  # (idx, hh, ref_kh, tank_kh, temp, is_flat)
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


def plot(rows, out_path, mobile=False):
    if not rows:
        print("표시할 데이터가 없습니다(전부 에러 표식이거나 파일이 비어있음).", file=sys.stderr)
        sys.exit(1)

    total = len(rows)
    idx = list(range(len(rows)))
    hh = [r[0] for r in rows]
    ref_kh = [r[1] for r in rows]
    tank_kh = [r[2] for r in rows]
    temp = [r[3] for r in rows]
    flat_idx = [i for i, r in enumerate(rows) if r[4]]
    not_flat_idx = [i for i, r in enumerate(rows) if not r[4]]

    if mobile:
        fig, ax1 = plt.subplots(figsize=(6.4, 4.6), dpi=200)
        fs_tick, fs_label, fs_legend, fs_title = 11, 12, 10, 13
        marker_s, not_flat_s = 26, 45
    else:
        fig, ax1 = plt.subplots(figsize=(max(8, total * 0.12), 5), dpi=150)
        fs_tick, fs_label, fs_legend, fs_title = 8, 10, 8, 11
        marker_s, not_flat_s = 18, 30

    ax1.plot(idx, tank_kh, "-", color="tab:blue", linewidth=1, alpha=0.6, zorder=1)
    ax1.scatter([idx[i] for i in flat_idx], [tank_kh[i] for i in flat_idx],
                color="tab:blue", label="tank dKH (평탄)", zorder=2, s=marker_s)
    if not_flat_idx:
        ax1.scatter([idx[i] for i in not_flat_idx], [tank_kh[i] for i in not_flat_idx],
                    color="tab:red", marker="x", label="tank dKH (평탄 미도달)", zorder=3, s=not_flat_s)
    ax1.plot(idx, ref_kh, "--", color="tab:gray", linewidth=1, label="ref dKH(앵커)")

    ax1.set_ylabel("dKH", fontsize=fs_label)
    xlabel = "최근 측정 (눈금=HH시)" if mobile else "측정 순번 (파일에 날짜가 없어 순번 기준; 눈금=HH시)"
    ax1.set_xlabel(xlabel, fontsize=fs_label)
    ax1.tick_params(axis="both", labelsize=fs_tick)

    n_ticks = 6 if mobile else 20
    step = max(1, len(rows) // n_ticks)
    ax1.set_xticks(idx[::step])
    ax1.set_xticklabels([f"{hh[i]:02d}" for i in idx[::step]], rotation=45, fontsize=fs_tick)

    ax2 = ax1.twinx()
    ax2.plot(idx, temp, ":", color="tab:orange", linewidth=1, alpha=0.7, label="온도(°C)")
    ax2.set_ylabel("온도 (°C, 밀폐챔버 내부)", fontsize=fs_label)
    ax2.tick_params(axis="y", labelsize=fs_tick)

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="best", fontsize=fs_legend)

    title = f"최근 dKH 추세 (최근 {total}건)" if mobile else f"KH 측정 대장 (dkh.dat) — 최근 {total}건"
    ax1.set_title(title, fontsize=fs_title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=fig.dpi)
    print(f"저장: {out_path}")


def write_latest_json(rows, path):
    hh, ref_kh, tank_kh, temp, is_flat = rows[-1]
    with open(path, "w") as f:
        json.dump({
            "hh": hh, "ref_kh": ref_kh, "tank_kh": tank_kh,
            "temp": temp, "is_flat": is_flat, "count": len(rows),
        }, f, ensure_ascii=False)
    print(f"저장: {path}")


def write_series_json(rows, path):
    """대시보드 인터랙티브 차트용 — 그린 구간 그대로를 배열로 내보낸다."""
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
    ap.add_argument("-o", "--out", default="dkh_trend.png", help="출력 PNG 경로")
    ap.add_argument("--json", default=None, help="최신 측정값 JSON 출력 경로(옵션, 대시보드용)")
    ap.add_argument("--recent", type=int, default=None,
                     help="마지막 N건만 그린다(옵션, 모바일 대시보드용 요약 차트)")
    ap.add_argument("--mobile", action="store_true",
                     help="작은 화면에 맞춘 큰 글씨·고정 비율로 렌더링")
    ap.add_argument("--series-json", default=None,
                     help="그려진 구간(--recent 적용분)을 배열 JSON으로 출력(옵션, 인터랙티브 차트용)")
    args = ap.parse_args()

    rows = load(args.dat_file)
    plot_rows = rows[-args.recent:] if args.recent else rows
    plot(plot_rows, args.out, mobile=args.mobile)
    if args.json:
        write_latest_json(rows, args.json)
    if args.series_json:
        write_series_json(plot_rows, args.series_json)
