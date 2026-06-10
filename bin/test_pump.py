#!/usr/bin/env python3
"""AquaWiz 펌프 시험 스크립트 — 1회 실행 후 종료."""

import serial
import time
import re
import sys
from datetime import datetime

PORT     = 'COM14'
BAUD     = 9600
AIR_SECS = 5         # 탈기 시간(초) — 테스트 시 줄여서 사용


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
# 결과 파싱
# ─────────────────────────────────────────────

def parse_results(kh_lines):
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
    # ── 준비 ──────────────────────────────────
    send(ser, 'airoff', stop_pattern='OFF')
    send(ser, 'ton', stop_pattern='수조ON')
    print("\n[시험] 수조수 샘플링")
    send_motor(ser, 1, 'm1f:5')
    print("\n[시험] 수조수 이송")
    send_motor(ser, 2, 'm2f:5')
    print("\n[시험] KCL 공급")
    send_motor(ser, 3, 'm3f:10')
    print("\n[시험] 참조수 이송")
    send_motor(ser, 4, 'm4f:5')
    send(ser, 'airoff', stop_pattern='OFF')

    # ── 폭기 ──────────────────────────────────
    print("\n[폭기] 에어 펌프 ON")
    send(ser, 'ron', stop_pattern='참조ON')

    print(f"\n[폭기] {AIR_SECS}초 탈기 대기 중...")
    for elapsed in range(0, AIR_SECS, 60):
        remaining = AIR_SECS - elapsed
        print(f"    남은 시간: {remaining}초")
        time.sleep(min(60, remaining))

    send(ser, 'airoff', stop_pattern='OFF')

    # ── 측정 ──────────────────────────────────
    print("\n[측정] 참조수 pH")
    send(ser, 'ref',   stop_pattern='[OK]',       timeout=20)
    print("\n[측정] 수조수 pH")
    send(ser, 'tank',  stop_pattern='[OK]',       timeout=20)
    print("\n[측정] KH 계산")
    kh_lines = send(ser, 'calkh', stop_pattern='===========', timeout=10)

    # ── 정리 ──────────────────────────────────
    send(ser, 'ton', stop_pattern='수조ON')
    print("\n[시험] 수조수 반환1")
    send_motor(ser, 1, 'm1b:5')
    print("\n[시험] 수조수 반환2")
    send_motor(ser, 2, 'm2b:5')
    print("\n[시험] KCL 저장수 배출")
    send_motor(ser, 3, 'm3b:5')
    print("\n[시험] 참조수 반환")
    send_motor(ser, 4, 'm4b:5')
    send(ser, 'airoff', stop_pattern='OFF')

    ref_ph, tank_ph, ref_kh, tank_kh, temp = parse_results(kh_lines)
    print("\n" + "=" * 40)
    print("측정 결과")
    print("=" * 40)
    if ref_ph  is not None: print(f"  참조수 pH : {ref_ph:.3f}")
    if tank_ph is not None: print(f"  수조수 pH : {tank_ph:.3f}")
    if ref_kh  is not None: print(f"  참조 dKH  : {ref_kh:.3f} dKH")
    if tank_kh is not None: print(f"  수조 dKH  : {tank_kh:.3f} dKH")
    if temp    is not None: print(f"  온도      : {temp:.1f} C")
    if tank_kh is None:     print("  dKH 파싱 실패")
    print("=" * 40)


# ─────────────────────────────────────────────
# 메인 (1회 실행 후 종료)
# ─────────────────────────────────────────────

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else PORT
    now  = datetime.now()

    print(f"AquaWiz KH 펌프 시험 — {port} @ {BAUD}baud")
    print(f"측정 시작: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")

    try:
        with serial.Serial(port, BAUD, timeout=1) as ser:
            time.sleep(2)
            ser.reset_input_buffer()
            run_measurement(ser)
    except serial.SerialException as e:
        print(f"[ERR] 시리얼 오류: {e}")
    except Exception as e:
        print(f"[ERR] 예외 발생: {e}")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n[중단] 사용자 인터럽트")
        sys.exit(0)
