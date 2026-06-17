#!/usr/bin/env python3
"""
폭기 평형(plateau) 테스트 — "완전 폭기 상태에서는 pH가 움직이지 않는다" 검증.

측정 챔버에 본수조수를 채우고 ron으로 *계속 폭기*하면서, 일정 간격으로 tank pH를
반복 측정해 시간-pH 곡선을 기록한다. 연속 읽기 Δ가 PLATEAU_EPS 이하로 PLATEAU_N회
이어지면 평형 도달로 보고 종료(= pH가 더는 안 움직임). 평형 도달 시간이 곧
measure_kh_once 의 폭기/재폭기 시간을 정하는 근거.

펌웨어 변경/플래시 불필요 — 기존 명령(ron/tank/airoff/m1·m3)만 사용.
폭기 중 측정이라 절대 pH엔 흐름 오프셋 포함(트림평균 펌웨어면 노이즈↓). 추세만 본다.

실행:  python test_aeration_plateau.py [COM포트]
       (WSL에서 Windows파이썬: /mnt/c/dkh/python313/python.exe -X utf8 test_aeration_plateau.py)
중단:  Ctrl+C (안전하게 airoff 후 종료)
"""

import serial
import time
import re
import sys
import os
from datetime import datetime

PORT = 'COM15'
BAUD = 9600

DO_PREP       = True    # True면 KCl 배출 후 본수조수로 측정챔버 채움. False면 챔버 현 상태로 측정
FILL_SECS     = 70      # 본수조수 채움(긴 호스 +10s, measure_kh_once 와 동일)
LOG_INTERVAL  = 30      # pH 재측정 간격(초)
MAX_DURATION  = 3600    # 최대 폭기·기록 시간(초)
PLATEAU_N     = 4       # 연속 N회가 EPS 이내면 평형 판정
PLATEAU_EPS   = 0.001   # 평형 기준(pH, 양자화 1 LSB)
CLEANUP_KCL   = True    # 종료 시 시료 배출 후 KCl 공급(프로브 소크 복원)

CSV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'aeration_plateau.csv')


def read_until(ser, stop, timeout=20.0):
    lines = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='replace').strip()
            if line:
                lines.append(line)
                if stop in line:
                    return lines
        else:
            time.sleep(0.02)
    return lines


def send(ser, cmd, stop=None, timeout=5.0):
    ser.write((cmd + '\r\n').encode())
    if stop:
        return read_until(ser, stop, timeout)
    time.sleep(0.3)
    out = []
    while ser.in_waiting:
        line = ser.readline().decode('utf-8', errors='replace').strip()
        if line:
            out.append(line)
    return out


def motor(ser, idx, cmd):
    m = re.search(r':(\d+)$', cmd)
    dur = int(m.group(1)) if m else 60
    return send(ser, cmd, stop=f'[모터{idx}] 완료', timeout=dur + 15)


def measure_ph(ser):
    """tank 1회 측정 → (pH, V, T). 폭기 켠 상태에서 호출."""
    lines = send(ser, 'tank', stop='[OK]', timeout=20)
    ph = v = t = None
    for ln in lines:
        m = re.search(r'\[수조수\] V:([\d.]+) pH:([\d.]+) T:([\d.]+)', ln)
        if m:
            v, ph, t = float(m.group(1)), float(m.group(2)), float(m.group(3))
    return ph, v, t


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else PORT
    print(f"폭기 평형 테스트 — {port} @ {BAUD}baud, 간격 {LOG_INTERVAL}s, 최대 {MAX_DURATION}s")
    print(f"평형 판정: 연속 {PLATEAU_N}회 Δ≤{PLATEAU_EPS}")
    print(f"CSV: {CSV_FILE}\n")

    with serial.Serial(port, BAUD, timeout=1) as ser:
        time.sleep(2)
        ser.reset_input_buffer()

        # ── 준비: KCl 배출 → 본수조수 채움 (액체 이동 전 airoff) ──
        if DO_PREP:
            send(ser, 'airoff', stop='OFF')
            send(ser, 'ton', stop='수조ON')
            print("[준비] KCl 배출")
            motor(ser, 3, 'm3b:68')
            print("[준비] 본수조수 채움")
            motor(ser, 1, f'm1f:{FILL_SECS}')

        # ── 폭기 ON, 시간-pH 기록 ──
        send(ser, 'airoff', stop='OFF')
        send(ser, 'ron', stop='참조ON')
        print("[폭기] ON — 시간별 pH 기록 시작\n")
        print(f"{'t(s)':>6} {'pH':>7} {'ΔpH':>7} {'V(mV)':>9} {'T':>5}")

        with open(CSV_FILE, 'w') as f:
            f.write("elapsed_s,pH,dPH,V_mV,T_C,clock\n")

        t0 = time.time()
        prev = None
        stable = 0
        plateaued = False
        while True:
            elapsed = int(time.time() - t0)
            ph, v, t = measure_ph(ser)
            if ph is None:
                print(f"{elapsed:6d}  [측정 실패]")
            else:
                d = (ph - prev) if prev is not None else 0.0
                print(f"{elapsed:6d} {ph:7.3f} {d:+7.3f} {v:9.3f} {t:5.1f}")
                with open(CSV_FILE, 'a') as f:
                    f.write(f"{elapsed},{ph:.3f},{d:+.3f},{v:.3f},{t:.1f},"
                            f"{datetime.now():%H:%M:%S}\n")
                if prev is not None and abs(d) <= PLATEAU_EPS:
                    stable += 1
                else:
                    stable = 0
                prev = ph
                if stable >= PLATEAU_N - 1:
                    print(f"\n[평형] 연속 {PLATEAU_N}회 Δ≤{PLATEAU_EPS} → pH 평탄(완전 폭기 도달) "
                          f"@ {elapsed}s, pH={ph:.3f}")
                    plateaued = True
                    break
            if time.time() - t0 >= MAX_DURATION:
                print(f"\n[종료] 최대 {MAX_DURATION}s 도달 — 평형 미확정(아직 움직임)")
                break
            time.sleep(LOG_INTERVAL)

        # ── 정리: 시료 배출 → KCl 공급(소크 복원) ──
        if CLEANUP_KCL:
            send(ser, 'airoff', stop='OFF')
            send(ser, 'ton', stop='수조ON')
            print("\n[정리] 시료 배출 → 본수조")
            motor(ser, 1, 'm1b:82')
            print("[정리] KCl 공급(프로브 소크)")
            motor(ser, 3, 'm3f:60')
        send(ser, 'airoff', stop='OFF')
        print(f"\n완료. CSV 저장: {CSV_FILE}  (평형도달={'O' if plateaued else 'X'})")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n[중단] 사용자 인터럽트 — airoff 시도")
        try:
            with serial.Serial(sys.argv[1] if len(sys.argv) > 1 else PORT, BAUD, timeout=1) as s:
                time.sleep(1); s.write(b'airoff\r\n')
        except Exception:
            pass
        sys.exit(0)
