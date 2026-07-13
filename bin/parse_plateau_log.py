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

CO₂ 편향 의심 판정(classify_co2_suspect, 2026-07-13):
  에어컨 가동 후 실내 CO₂ 축적이 ref 시료에 유입되면 ref pH 곡선이 정상(+2~4mpH
  상승)과 반대로 하강 추적하며 평탄 판정이 늦어진다 → dKH가 −0.07~−0.24 낮게 나옴.
  실측 이력 35런 소급: 정상 27런=flat_n 8~15·net −1~+5mpH / 편향 7런=flat_n 21~36·
  net −20~−7mpH 로 두 지표가 완전 분리(오분류 0). 두 지표는 같은 물리 기전의 두
  단면이라 AND 결합(오탐 최소화 우선). 시각(HH) 조건은 넣지 않는다 — "새벽"에
  흔할 뿐 낮에도 발생했고(07-05 21시, 07-08 13시), 판정은 곡선 형태만 본다.
  임계 재조정은 아래 상수 2개만 수정하면 된다(이미 기록된 값의 소급 갱신이
  필요하면 sync 쪽 백필 조건을 "필드 없음"에서 "규칙 버전 필드 불일치"로 확장).
"""
import argparse
import json
import re

HEADER_RE = re.compile(r"===== measure_kh_once V4 (.+?) =====")
MODE_RE = re.compile(r"\[(calkh|calref)\b")
READING_RE = re.compile(r"\[(tank|ref)\]\s*(\d+)회\s*pH:([0-9.]+).*?(\d+)s\)")
FLAT_RE = re.compile(r"\[평탄\]\s*(tank|ref)\s*(\d+)회")

# CO₂ 편향 의심 임계 — 근거는 모듈 docstring(2026-07 실측 분리) 참조
CO2_FLAT_N_MIN = 21        # ref_flat_n > 20 (정상 최대 15 ↔ 편향 최소 21)
CO2_REF_NET_MPH_MAX = -3   # ref_net ≤ −3mpH (정상 최저 −1 ↔ 편향 최고 −7)


def classify_co2_suspect(run):
    """run dict(이력 JSON 항목 그대로)에 CO₂ 편향 의심 판정.

    반환: (co2_suspect: bool, ref_net_mph: int|None)
    ref 시계열이 2점 미만이면 net 계산 불능 → (False, None).
    ref_flat_n 없음(평탄 미도달) 은 판정 불능 → (False, ref_net은 계산해 둠).
    """
    ref = run.get("ref") or []
    if len(ref) < 2:
        return False, None
    try:
        ref_net_mph = round((ref[-1]["ph"] - ref[0]["ph"]) * 1000)
    except (KeyError, TypeError):
        return False, None
    flat_n = run.get("ref_flat_n")
    suspect = (flat_n is not None and flat_n >= CO2_FLAT_N_MIN
               and ref_net_mph <= CO2_REF_NET_MPH_MAX)
    return suspect, ref_net_mph


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

    completed = "[LOG] " in body  # log_kh() 가 항상 찍는 줄 — 성공/실패(에러 표식) 무관하게 "이 실행은 끝났다"는 신호

    run = {
        "run_started": run_started,
        "mode": mode,
        "completed": completed,
        "tank": tank,
        "ref": ref,
        "tank_flat_n": flat_at["tank"],
        "ref_flat_n": flat_at["ref"],
    }
    run["co2_suspect"], run["ref_net_mph"] = classify_co2_suspect(run)
    return run


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log_file", nargs="?", default="/mnt/c/dkh/measure_kh.log",
                     help="measure_kh.log 경로(기본: Windows 원본, WSL에서 /mnt/c 경유)")
    ap.add_argument("-o", "--out", default="dkh_plateau.json", help="출력 JSON 경로")
    args = ap.parse_args()

    with open(args.log_file, encoding="utf-8", errors="replace") as f:
        text = f.read()

    result = parse_last_run(text) or {
        "run_started": None, "mode": None, "completed": False, "tank": [], "ref": [],
        "tank_flat_n": None, "ref_flat_n": None, "co2_suspect": False, "ref_net_mph": None,
    }
    with open(args.out, "w") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"저장: {args.out} (tank {len(result['tank'])}건, ref {len(result['ref'])}건)")
