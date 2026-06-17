#!/usr/bin/env python3
"""
AquaWiz KH 1회 측정 스크립트
HC-06 블루투스 시리얼로 한 번만 측정하고 dkh.dat 에 기록 후 종료.

대칭화 시퀀스 (v2 — 측정 챔버/홀딩 챔버 동시 폭기):
  tank = 본수조 → 측정 챔버(에어스톤)에서 폭기·측정.
  ref  = 위즈수조 → 홀딩 챔버(에어스톤)에서 폭기 → 측정 챔버로 이송·측정.
  둘을 단일 ron 창에서 동시 폭기해 시간상수를 맞춰(부피 대칭) 방 CO₂ 과도
  비대칭을 제거한다. 측정 직전 본수조수로 측정 챔버 KCl 잔막을 헹군다.
  참조수는 측정 후 위즈수조로 순환 회수(폐기 아님). 프로브는 측정 사이
  KCl에 소크. tank 먼저, ref 나중(측정 챔버에서 폭기되어 프로브 마름 없음).
  측정은 수렴 판정(연속 차 < CONV_EPS)으로 반복 — 수렴 횟수는 전극 건강 지표.
  ※ 널테스트 한정: ref 채우기 전 헹굼 생략. 정상운영(KH 다름) 복귀 시 복원 필수.

dkh.dat 형식 (한 줄에 하나):
  HH ref_pH tank_pH ref_kh tank_kh temp
  14 7.823 7.412 8.523 7.901 25.3
  15 0.000 0.000 0.000 0.000 0.0   ← 오류/타임아웃 시
"""

import serial
import time
import re
import sys
import os
from datetime import datetime

PORT     = 'COM15'
BAUD     = 9600
AIR_SECS = 1200         # 탈기 시간(초) — 테스트 시 줄여서 사용
STABLE_SECS   = 120     # 채움 후 안정화(초) — tank/ref 동일 (타이밍 대칭). 제품 스펙 응답시간 최대 2분
CONV_INTERVAL = 45      # 수렴 판정 재측정 간격(초)
CONV_EPS      = 0.002   # 수렴 기준: 연속 측정 pH 차 (노이즈 0.0005~0.002의 1~4배, ≈0.04 dKH)
CONV_MAX      = 6       # 최대 측정 횟수
DAT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dkh.dat')
LOG_FILE = r'C:\dkh\measure_kh.log' if os.name == 'nt' else None


def setup_logging():
    """Windows에서 pythonw 로 실행될 때 모든 print 출력을 로그 파일로 보낸다.
    pythonw 는 sys.stdout 이 None 이라, redirect 하지 않으면 첫 print() 에서 죽는다.
    1MB 초과 시 새로 시작해 무한 증가를 막는다."""
    if os.name != 'nt':
        return
    target = None
    try:
        mode = 'w' if (os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000) else 'a'
        target = open(LOG_FILE, mode, encoding='utf-8', buffering=1)  # 줄 단위 flush
    except OSError:
        try:
            target = open(os.devnull, 'w')   # 로그 못 열어도 print 가 죽지 않게
        except OSError:
            target = None
    if target is not None:
        sys.stdout = target
        sys.stderr = target
        print(f"\n===== measure_kh_once {datetime.now():%Y-%m-%d %H:%M:%S} =====")


# ─────────────────────────────────────────────
# 파일 기록
# ─────────────────────────────────────────────

def log_kh(hour, ref_ph, tank_ph, ref_kh, tank_kh, temp):
    """시각과 측정값 전체를 dkh.dat 에 한 줄 추가 후 즉시 닫기.
    형식: HH ref_pH tank_pH ref_kh tank_kh temp
    """
    line = f"{hour:02d} {ref_ph:.3f} {tank_ph:.3f} {ref_kh:.3f} {tank_kh:.3f} {temp:.1f}"
    with open(DAT_FILE, 'a') as f:
        f.write(line + '\n')
    print(f"[LOG] {DAT_FILE} ← {line}")


# ─────────────────────────────────────────────
# 시리얼 헬퍼
# ─────────────────────────────────────────────

def read_until(ser, stop_pattern, timeout=60.0):
    lines = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='replace').strip()
            if line:
                print(f"    {line}")
                lines.append(line)
                if stop_pattern in line:
                    return lines
        else:
            time.sleep(0.02)
    print(f"    [TIMEOUT] '{stop_pattern}' 미수신")
    return lines


def send(ser, cmd, stop_pattern=None, timeout=5.0):
    print(f"\n→ {cmd}")
    ser.write((cmd + '\r\n').encode())
    if stop_pattern:
        return read_until(ser, stop_pattern, timeout)
    time.sleep(0.3)
    lines = []
    while ser.in_waiting:
        line = ser.readline().decode('utf-8', errors='replace').strip()
        if line:
            print(f"    {line}")
            lines.append(line)
    return lines


def send_motor(ser, motor_idx, cmd):
    m = re.search(r':(\d+)$', cmd)
    duration = int(m.group(1)) if m else 60
    return send(ser, cmd,
                stop_pattern=f'[모터{motor_idx}] 완료',
                timeout=duration + 15)


# ─────────────────────────────────────────────
# 수렴 측정
# ─────────────────────────────────────────────

def parse_ph(lines, label):
    """측정 출력 '[수조수] V:.. pH:.. T:..C'에서 pH 추출. 실패 시 None."""
    for line in lines:
        m = re.search(rf'\[{label}\].*pH:([\d.]+)', line)
        if m:
            return float(m.group(1))
    return None


def measure_converged(ser, what):
    """'tank'/'ref'를 CONV_INTERVAL초 간격으로 반복 측정,
    연속 차 < CONV_EPS면 수렴으로 보고 종료.
    펌웨어가 마지막 값을 덮어쓰므로 최종 측정값이 calkh 에 쓰인다.
    반환: 측정 횟수 (횟수 증가 = 전극 응답 둔화 신호)."""
    label = '수조수' if what == 'tank' else '참조수'
    prev = None
    for i in range(1, CONV_MAX + 1):
        lines = send(ser, what, stop_pattern='[OK]', timeout=20)
        ph = parse_ph(lines, label)
        if ph is not None and prev is not None and abs(ph - prev) < CONV_EPS:
            print(f"    [수렴] {what} {i}회 (Δ={abs(ph - prev):.4f})")
            return i
        prev = ph
        if i < CONV_MAX:
            time.sleep(CONV_INTERVAL)
    print(f"    [수렴실패] {what} {CONV_MAX}회 — 마지막 값으로 진행")
    return CONV_MAX


# ─────────────────────────────────────────────
# 결과 파싱
# ─────────────────────────────────────────────

def parse_results(kh_lines):
    """calkh 출력에서 참조pH/수조pH/refKH/수조KH/온도 파싱.
    반환: (ref_ph, tank_ph, ref_kh, tank_kh, temp) — 파싱 실패 항목은 None.
    """
    patterns = {
        'ref_ph':  r'참조pH:([\d.]+)',
        'tank_ph': r'수조pH:([\d.]+)',
        'ref_kh':  r'refKH:([\d.]+)',
        'tank_kh': r'수조KH:([\d.]+)',
        'temp':    r'온도:([\d.]+)',
    }
    vals = {k: None for k in patterns}
    for line in kh_lines:
        for key, pat in patterns.items():
            if vals[key] is None:
                m = re.search(pat, line)
                if m:
                    vals[key] = float(m.group(1))
    return (vals['ref_ph'], vals['tank_ph'],
            vals['ref_kh'], vals['tank_kh'], vals['temp'])


# ─────────────────────────────────────────────
# 측정 루틴
# ─────────────────────────────────────────────

def run_measurement(ser):
    # ── KCl 배출 (프로브 KCl 소크 해제) ──────────
    send(ser, 'airoff', stop_pattern='OFF')
    send(ser, 'ton', stop_pattern='수조ON')
    print("\n[준비] KCl 배출 (측정 챔버)")
    send_motor(ser, 3, 'm3b:68')

    # ── tank 측정수 준비: 본수조수로 측정 챔버 KCl 잔막 헹굼 후 채움 ──
    print("\n[tank] 본수조수로 측정 챔버 헹굼 (KCl 잔막 → 본수조)")
    send_motor(ser, 1, 'm1f:60')
    send_motor(ser, 1, 'm1b:72')
    print("\n[tank] 본수조수 채움 (측정용 → 측정 챔버)")
    send_motor(ser, 1, 'm1f:60')

    # ── ref 준비: 위즈수조 → 홀딩 챔버 ──
    print("\n[ref] 참조수 → 홀딩 챔버")
    send_motor(ser, 4, 'm4f:60')

    # ── 폭기: 측정 챔버(tank) + 홀딩 챔버(ref) 동시 (단일 창 → 시간상수 대칭) ──
    send(ser, 'airoff', stop_pattern='OFF')
    print("\n[폭기] 에어 펌프 ON (측정 챔버 tank + 홀딩 챔버 ref 동시)")
    send(ser, 'ron', stop_pattern='참조ON')

    print(f"\n[폭기] {AIR_SECS}초 탈기 대기 중...")
    for elapsed in range(0, AIR_SECS, 60):
        remaining = AIR_SECS - elapsed
        print(f"    남은 시간: {remaining}초")
        time.sleep(min(60, remaining))

    send(ser, 'airoff', stop_pattern='OFF')

    # ── tank 측정 (측정 챔버 제자리 — 폭기 중 프로브 침지로 마름 없음) ──
    print(f"\n[tank] {STABLE_SECS}초 안정화")
    time.sleep(STABLE_SECS)
    print("\n[tank] 수조수 pH (수렴 판정)")
    tank_n = measure_converged(ser, 'tank')

    # ── tank 배출 → 본수조, ref 이송 (홀딩 → 측정 챔버) ──
    send(ser, 'ton', stop_pattern='수조ON')
    print("\n[tank] 수조수 배출 → 본수조")
    send_motor(ser, 1, 'm1b:72')
    # 헹굼 생략: 널테스트라 수조수↔참조수 성분차 극히 작음.
    # ※ 정상운영(수조 KH ≠ 참조수) 복귀 시 여기에 측정 챔버 헹굼 복원 필수 (tank→ref 캐리오버 방지).
    print("\n[ref] 홀딩 챔버 참조수 → 측정 챔버")
    send_motor(ser, 2, 'm2f:60')

    # ── ref 측정 ──────────────────────────────
    send(ser, 'airoff', stop_pattern='OFF')
    print(f"\n[ref] {STABLE_SECS}초 안정화")
    time.sleep(STABLE_SECS)
    print("\n[ref] 참조수 pH (수렴 판정)")
    ref_n = measure_converged(ser, 'ref')

    print("\n[ref] KH 계산")
    kh_lines = send(ser, 'calkh', stop_pattern='===========', timeout=10)

    # ── ref 회수 (측정 챔버 → 홀딩 → 위즈수조 순환, 폐기 아님) ──
    send(ser, 'ton', stop_pattern='수조ON')
    print("\n[정리] 참조수 배출 → 홀딩 → 위즈수조 (순환 회수)")
    send_motor(ser, 2, 'm2b:68')
    send_motor(ser, 4, 'm4b:68')

    # ── KCl 공급 (프로브 소크), 종료 — 지속 폭기(terminal ron) 없음 ──
    print("\n[정리] KCl 공급 (프로브 소크)")
    send_motor(ser, 3, 'm3f:60')
    send(ser, 'airoff', stop_pattern='OFF')

    # ── 파싱 ──────────────────────────────────
    ref_ph, tank_ph, ref_kh, tank_kh, temp = parse_results(kh_lines)

    print("\n" + "=" * 40)
    print("측정 결과")
    print("=" * 40)
    if ref_ph  is not None: print(f"  참조수 pH : {ref_ph:.3f}")
    if tank_ph is not None: print(f"  수조수 pH : {tank_ph:.3f}")
    if ref_kh  is not None: print(f"  참조 dKH  : {ref_kh:.3f} dKH")
    if tank_kh is not None: print(f"  수조 dKH  : {tank_kh:.3f} dKH")
    if temp    is not None: print(f"  온도      : {temp:.1f} C")
    print(f"  수렴 횟수 : tank {tank_n}회 / ref {ref_n}회")
    if tank_kh is None:     print("  dKH 파싱 실패")
    print("=" * 40)

    return (ref_ph, tank_ph, ref_kh, tank_kh, temp)


# ─────────────────────────────────────────────
# 메인 (1회 실행 후 종료)
# ─────────────────────────────────────────────

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else PORT
    now  = datetime.now()
    hour = now.hour

    print(f"AquaWiz KH 1회 측정 — {port} @ {BAUD}baud")
    print(f"기록 파일: {DAT_FILE}")
    print(f"측정 시작: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")

    result = None
    try:
        with serial.Serial(port, BAUD, timeout=1) as ser:
            time.sleep(2)
            ser.reset_input_buffer()
            result = run_measurement(ser)
    except serial.SerialException as e:
        print(f"[ERR] 시리얼 오류: {e}")
    except Exception as e:
        print(f"[ERR] 예외 발생: {e}")

    if result and all(v is not None for v in result):
        log_kh(hour, *result)
    else:
        log_kh(hour, 0.0, 0.0, 0.0, 0.0, 0.0)


if __name__ == '__main__':
    setup_logging()
    try:
        main()
    except KeyboardInterrupt:
        print("\n[중단] 사용자 인터럽트")
        sys.exit(0)
