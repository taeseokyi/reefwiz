#!/usr/bin/env python3
"""
AquaWiz KH 자동 측정 스크립트 — 매시간 정시 반복 (Windows)
COM 포트로 매 정시(HH:00:00)마다 측정, dkh.dat 에 기록.

대칭화 시퀀스: 저장수조(5L) 직접 폭기, tank 우선 측정.
KCl 스윕이 tank 헹굼을 겸하고, tank/ref 블록의 헹굼·채움·안정화
타이밍을 동일하게 맞춰 전극 응답 지연을 공통모드로 상쇄한다.
측정은 수렴 판정(연속 차 < CONV_EPS)으로 반복 — 수렴 횟수는 전극 건강 지표.

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
from datetime import datetime, timedelta

PORT     = 'COM14'
BAUD     = 9600
AIR_SECS = 1800         # 탈기 시간(초) — 테스트 시 줄여서 사용
STABLE_SECS   = 60      # 채움 후 안정화(초) — tank/ref 동일 (타이밍 대칭)
CONV_INTERVAL = 45      # 수렴 판정 재측정 간격(초)
CONV_EPS      = 0.003   # 수렴 기준: 연속 측정 pH 차
CONV_MAX      = 6       # 최대 측정 횟수
DAT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dkh.dat')


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
    # ── 준비: 수조수 샘플링 (→챔버) ───────────
    send(ser, 'airoff', stop_pattern='OFF')
    send(ser, 'ton', stop_pattern='수조ON')
    print("\n[준비] 수조수 샘플링 (→챔버)")
    send_motor(ser, 1, 'm1f:5')
    send(ser, 'airoff', stop_pattern='OFF')

    # ── 폭기: 저장수조(5L)+챔버 동시, 프로브는 측정컵 KCl 소크 ──
    print("\n[폭기] 에어 펌프 ON (저장수조+챔버)")
    send(ser, 'ron', stop_pattern='참조ON')

    print(f"\n[폭기] {AIR_SECS}초 탈기 대기 중...")
    for elapsed in range(0, AIR_SECS, 60):
        remaining = AIR_SECS - elapsed
        print(f"    남은 시간: {remaining}초")
        time.sleep(min(60, remaining))

    send(ser, 'airoff', stop_pattern='OFF')

    # ── tank ──────────────────────────────────
    send(ser, 'ton', stop_pattern='수조ON')
    print("\n[tank] KCL 보관액 배출")
    send_motor(ser, 3, 'm3b:5')

    print("\n[tank] KCL 스윕 (= 측정컵 헹굼)")
    send_motor(ser, 2, 'm2f:2')
    send_motor(ser, 2, 'm2b:2')

    print("\n[tank] 수조수 채움")
    send_motor(ser, 2, 'm2f:5')
    send(ser, 'airoff', stop_pattern='OFF')

    print(f"\n[tank] {STABLE_SECS}초 안정화")
    time.sleep(STABLE_SECS)
    print("\n[tank] 수조수 pH (수렴 판정)")
    tank_n = measure_converged(ser, 'tank')

    send(ser, 'ton', stop_pattern='수조ON')
    print("\n[tank] 수조수 배출")
    send_motor(ser, 2, 'm2b:5')

    # ── ref: tank 블록과 헹굼·채움·안정화 타이밍 동일 ──
    print("\n[ref] 참조수 헹굼 (tank 잔막 제거)")
    send_motor(ser, 4, 'm4f:2')
    send_motor(ser, 4, 'm4b:2')

    print("\n[ref] 참조수 채움")
    send_motor(ser, 4, 'm4f:5')
    send(ser, 'airoff', stop_pattern='OFF')

    print(f"\n[ref] {STABLE_SECS}초 안정화")
    time.sleep(STABLE_SECS)
    print("\n[ref] 참조수 pH (수렴 판정)")
    ref_n = measure_converged(ser, 'ref')

    print("\n[ref] KH 계산")
    kh_lines = send(ser, 'calkh', stop_pattern='===========', timeout=10)

    send(ser, 'ton', stop_pattern='수조ON')
    print("\n[ref] 참조수 배출")
    send_motor(ser, 4, 'm4b:5')

    # ── 정리 ──────────────────────────────────
    print("\n[정리] 챔버수 방출 (KCl 포함 → 본수조 희석)")
    send_motor(ser, 1, 'm1b:5')

    print("\n[정리] KCL 공급")
    send_motor(ser, 3, 'm3f:5')
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
# 정시 대기
# ─────────────────────────────────────────────

def wait_until_next_hour():
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    wait_secs = (next_hour - now).total_seconds()
    print(f"\n[대기] 다음 정시 {next_hour.strftime('%H:%M')}까지 {wait_secs:.0f}초")
    time.sleep(wait_secs)


# ─────────────────────────────────────────────
# 메인 루프 (매 정시 무한 반복)
# ─────────────────────────────────────────────

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else PORT
    print(f"AquaWiz KH 측정 루프 시작 — {port} @ {BAUD}baud")
    print(f"기록 파일: {DAT_FILE}")
    print("Ctrl+C 로 중지\n")

    while True:
        now  = datetime.now()
        hour = now.hour

        print(f"\n{'='*50}")
        print(f"측정 시작: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*50}")

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

        wait_until_next_hour()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n[중단] 사용자 인터럽트")
        sys.exit(0)
