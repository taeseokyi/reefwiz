#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KMA 종관관측 습도 조회 — BT 무응답 임계(48h 이동평균 습도) 점검용.

기상청 API 허브 gts_syn1.php, 동작구 신대방동 종관관측소(WMO 47108, 수조와 동일 지역).
BT 드롭 진단 근거: 48h 이동평균 상대습도 >= 82% 가 무응답 켜지는 임계
(순간습도 무관, 누적 흡습이 2.4GHz 링크마진 초과). 상세 [[project-bt-rf-reconnect]].

★인증키는 코드에 넣지 않는다(저장소 PUBLIC). 아래 순서로 저장소 밖에서 로드:
   1) 환경변수 KMA_AUTHKEY
   2) C:\\dkh\\kma_authkey.txt   (WSL: /mnt/c/dkh/kma_authkey.txt)
   3) 모듈 폴더/kma_authkey.txt   (.gitignore 처리)
키 없으면 안내 후 종료. 키 자체는 절대 출력하지 않는다.

사용:
   python3 kma_humidity.py                 # 최근 48h 이동평균(+24h·순간·범위)
   python3 kma_humidity.py --hours 72      # 조회 구간 변경
   python3 kma_humidity.py --tm 202607230400  # 끝시각(UTC, YYYYMMDDHHMM) 지정
   python3 kma_humidity.py --raw           # 원자료 줄도 출력
종료코드: 0=정상, 1=조회/파싱 실패, 2=키 없음.
"""
import os, sys, json, argparse, urllib.request
from datetime import datetime, timezone, timedelta

STN = "47108"
URL = "https://apihub.kma.go.kr/api/typ01/url/gts_syn1.php"
THRESHOLD = 82.0  # 48h 이동평균 습도 임계(%) — 이상이면 BT 무응답 산발 위험

# gts_syn1 데이터 컬럼(0-base, split() 기준): 0=YYMMDDHHMI(UTC) 1=STN ... 10=TA 11=TD 12=HM 17=RN
I_TIME, I_TA, I_TD, I_HM, I_RN = 0, 10, 11, 12, 17


def load_authkey():
    if os.environ.get("KMA_AUTHKEY"):
        return os.environ["KMA_AUTHKEY"].strip()
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (r"C:\dkh\kma_authkey.txt", "/mnt/c/dkh/kma_authkey.txt",
              os.path.join(here, "kma_authkey.txt")):
        try:
            with open(p, encoding="utf-8") as f:
                k = f.read().strip()
            if k:
                return k
        except OSError:
            continue
    return None


def fetch(tm, hours, key):
    q = f"?tm={tm}&dtm={hours}&stn={STN}&help=0&authKey={key}"
    with urllib.request.urlopen(URL + q, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_hm(text):
    """(utc_str, HM%, TA, TD) 리스트를 시간순으로 반환. 결측 HM은 제외."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split()
        if len(f) <= I_HM or not f[0].isdigit():
            continue
        try:
            hm = float(f[I_HM]); ta = float(f[I_TA]); td = float(f[I_TD])
        except ValueError:
            continue
        if hm < 0 or hm > 100:  # 결측(-9/-99 등) 제외
            continue
        rows.append((f[I_TIME], hm, ta, td))
    return rows


def utc_to_kst(s):  # YYYYMMDDHHMM(UTC) -> 'MM-DD HH시 KST'
    dt = datetime.strptime(s, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return (dt + timedelta(hours=9)).strftime("%m-%d %H시")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--tm", help="끝시각 UTC YYYYMMDDHHMM (기본=현재)")
    ap.add_argument("--raw", action="store_true")
    a = ap.parse_args()

    key = load_authkey()
    if not key:
        print("[에러] KMA 인증키 없음. env KMA_AUTHKEY 또는 C:\\dkh\\kma_authkey.txt 에 키를 넣으세요.",
              file=sys.stderr)
        return 2

    tm = a.tm or datetime.now(timezone.utc).strftime("%Y%m%d%H00")
    try:
        text = fetch(tm, a.hours, key)
    except Exception as e:
        print(f"[에러] 조회 실패: {e}", file=sys.stderr)
        return 1

    rows = parse_hm(text)
    if not rows:
        print("[에러] 유효한 습도 데이터 없음(응답/컬럼 확인 필요).", file=sys.stderr)
        if a.raw:
            print(text)
        return 1

    hm = [r[1] for r in rows]
    last48 = hm[-48:]; last24 = hm[-24:]
    avg48 = sum(last48) / len(last48)
    avg24 = sum(last24) / len(last24)
    t0, tN = utc_to_kst(rows[0][0]), utc_to_kst(rows[-1][0])
    latest = rows[-1]
    gap = latest[2] - latest[3]  # TA-TD 이슬점 격차(작을수록 준포화)

    status = "⚠️ 위험(임계 초과)" if avg48 >= THRESHOLD else "✅ 안전(임계 미만)"
    print(f"KMA 47108(동작구 신대방동)  {t0} ~ {tN}  (관측 {len(rows)}시간)")
    print(f"  현재값({tN}):  HM {latest[1]:.0f}%   기온 {latest[2]:.1f}°C / 이슬점 {latest[3]:.1f}°C (격차 {gap:.1f}°C)")
    print(f"  24h 이동평균: {avg24:.1f}%")
    print(f"  48h 이동평균: {avg48:.1f}%   [임계 {THRESHOLD:.0f}%]  {status}")
    print(f"  48h 범위: 최소 {min(last48):.0f}% / 최대 {max(last48):.0f}%")
    if a.raw:
        print("\n[최근 12시간]")
        for utc, h, ta, td in rows[-12:]:
            print(f"  {utc_to_kst(utc)}  HM {h:>4.0f}%  T {ta:>5.1f} / Td {td:>5.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
