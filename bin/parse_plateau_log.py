#!/usr/bin/env python3
"""C:\\dkh\\measure_kh.log(측정 중 print 로그)에서 마지막 1회 측정의
tank/ref 평탄(plateau) 추종 곡선을 추출해 대시보드용 JSON으로 낸다.

실제 측정(calkh/calref)은 dkh.dat 에 최종값 한 줄만 남기고, 매 읽음(pH)
자체는 이 로그에만 텍스트로 남는다(구조화 파일 없음) — 그래서 정규식으로
파싱한다. 원본 로그는 1MB 넘으면 다음 실행 시 통째로 비워지므로(코드 쪽
설계), 여기서는 항상 "마지막 실행 헤더 이후" 구간만 본다.

로그 줄 형식(measure_kh_once.py measure_until_flat):
  [tank] 1회 pH:7.931 (윈도우 1/4, 9s)                       ← 초기(윈도우 미충족)
  [tank] 8회 pH:7.909 span4:12mpH net8:22mpH (284s)          ← 이후
  [평탄] tank 78회 — span4=0≤2 AND net8=1≤1 → 평형 (pH 7.808)  ← 평탄 판정 시점
"""
import argparse
import json
import re

HEADER_RE = re.compile(r"===== measure_kh_once V4 (.+?) =====")
MODE_RE = re.compile(r"\[(calkh|calref)\b")
READING_RE = re.compile(r"\[(tank|ref)\]\s*(\d+)회\s*pH:([0-9.]+).*?(\d+)s\)")
FLAT_RE = re.compile(r"\[평탄\]\s*(tank|ref)\s*(\d+)회")


def parse_last_run(text):
    headers = list(HEADER_RE.finditer(text))
    if not headers:
        return None
    body = text[headers[-1].end():]
    run_started = headers[-1].group(1)

    mode_m = MODE_RE.search(body)
    mode = mode_m.group(1) if mode_m else None

    tank, ref = [], []
    for m in READING_RE.finditer(body):
        phase, n, ph, elapsed = m.group(1), int(m.group(2)), float(m.group(3)), int(m.group(4))
        (tank if phase == "tank" else ref).append({"n": n, "ph": ph, "elapsed": elapsed})

    flat_at = {"tank": None, "ref": None}
    for m in FLAT_RE.finditer(body):
        flat_at[m.group(1)] = int(m.group(2))

    return {
        "run_started": run_started,
        "mode": mode,
        "tank": tank,
        "ref": ref,
        "tank_flat_n": flat_at["tank"],
        "ref_flat_n": flat_at["ref"],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log_file", nargs="?", default="/mnt/c/dkh/measure_kh.log",
                     help="measure_kh.log 경로(기본: Windows 원본, WSL에서 /mnt/c 경유)")
    ap.add_argument("-o", "--out", default="dkh_plateau.json", help="출력 JSON 경로")
    args = ap.parse_args()

    with open(args.log_file, encoding="utf-8", errors="replace") as f:
        text = f.read()

    result = parse_last_run(text) or {
        "run_started": None, "mode": None, "tank": [], "ref": [],
        "tank_flat_n": None, "ref_flat_n": None,
    }
    with open(args.out, "w") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"저장: {args.out} (tank {len(result['tank'])}건, ref {len(result['ref'])}건)")
