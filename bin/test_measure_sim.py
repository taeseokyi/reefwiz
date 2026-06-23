#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_kh_once.py 통합 회귀 테스트 (firmware_sim 소켓 가상 포트)

펌웨어 시뮬레이터로 측정 흐름을 상황별로 실전처럼 돌려, RF 순단 대응(송신 전 연결확인·재연결·
재시도·모터 재시도 시 정지)·keepalive·평탄(8회) 판정·calkh/calref 파싱을 검증한다.

★측정/BT 로직 변경 시 배포 *전* 항상 실행해 전부 PASS 확인(언제든 재실행 가능).
실행: cd bin && python3 test_measure_sim.py     (WSL python3 — pyserial 3.5)

총 8 시나리오 / 38 검증:
  ── 정상 4 시나리오(25 검증) ──
    [1] 클린 calkh           (9) 전체 흐름·정확히 8회째 평탄·dKH·모터8종·재연결0
    [2] 측정 중 드롭         (6) 송신 전 연결확인이 다음 측정 전 재연결, 정확도 유지
    [3] 모터 드롭→정지·재송신(5) 재시도 시 mNs 정지 후 재송신(순서 m1f→m1s→m1f)
    [4] calref(--setref)     (5) ref dKH 역산 경로 동일 견고성
  ── 예외 4 시나리오(13 검증) ──
    [5] 완전 통신 두절(kill) (3) main 이 잡는 예외로 우아하게 종료(크래시·행 없음)
    [6] 깨진 응답(pH 누락)   (3) 파싱 실패→FAIL_MAX phase 실패(연결문제 아님)
    [7] 모터 완료 누락(막힘) (3) 재시도(정지+재송신) 소진 후 미완료 처리
    [8] 버스트(연속 2회 드롭)(4) 두 번 재연결하며 완주, 정확도 유지

※ 테스트는 import 한 모듈의 타이밍 상수만 메모리에서 패치(빠른 실행). 소스 파일의 실전 상수는
  불변 → 배포본 정상 동작.
"""
import io
import sys
import time
import contextlib

import serial
import measure_kh_once as mk
from firmware_sim import FirmwareSim, TANK_PH, REF_PH, DEFAULT_REF_DKH

# ── 테스트 속도용 타이밍 패치(측정 의미는 불변) ──
mk.MEAS_INTERVAL   = 0.02
mk.KEEPALIVE_SECS  = 0.05
mk.RECONNECT_BACKOFF = (0.02,)
mk.RECONNECT_TRIES = 5
mk.SEND_RETRY_MAX  = 3
mk.PHASE_MAX_SECS  = 60
mk.MEAS_READ_TIMEOUT = 0.5     # 정상 응답은 즉시라 무관; 예외(무응답) 시 빨리 타임아웃
mk.LINK_PING_TIMEOUT = 0.3     # ensure_link/reconnect 핑 대기 단축
mk.FAIL_MAX        = 2          # 예외 시나리오에서 빨리 phase 실패(백스톱 경로 검증)

EXPECT_TANK_DKH = DEFAULT_REF_DKH * (10 ** (-(REF_PH - TANK_PH)))   # ≈ 8.142
MOTORS = ['m3b:68', 'm1f:70', 'm2f:60', 'm2b:68', 'm4f:60', 'm4b:70', 'm1b:82', 'm3f:60']

_passed = 0
_failed = 0


def check(name, cond, detail=''):
    global _passed, _failed
    mark = 'PASS' if cond else 'FAIL'
    if cond: _passed += 1
    else: _failed += 1
    print(f"   [{mark}] {name}" + (f" — {detail}" if detail and not cond else ''))


def open_ser(port):
    return serial.serial_for_url(f'socket://127.0.0.1:{port}', baudrate=9600, timeout=1)


def run(fn, drops=None, tank_dkh=None):
    """sim 시작→ser 연결→run_measurement→(result, captured_stdout, sim) 반환."""
    sim = FirmwareSim()
    sim.drops = drops or []
    port = sim.start()
    time.sleep(0.1)
    ser = open_ser(port)
    time.sleep(0.1); ser.reset_input_buffer()
    buf = io.StringIO()
    result = None
    try:
        with contextlib.redirect_stdout(buf):
            result = fn(ser)
    finally:
        try: ser.close()
        except Exception: pass
        sim.stop()
    return result, buf.getvalue(), sim


# ── 시나리오 1: 클린 측정(calkh) ──────────────────────────
def scenario_clean():
    print("\n[1] 클린 측정(calkh) — 드롭 없음")
    result, out, sim = run(lambda ser: mk.run_measurement(ser))
    check("결과 튜플 완성", result is not None and all(v is not None for v in result),
          f"result={result}")
    if result:
        ref_ph, tank_ph, ref_kh, tank_kh, temp = result
        check("tank dKH ≈ 8.142", abs(tank_kh - EXPECT_TANK_DKH) < 0.01,
              f"got {tank_kh} expect {EXPECT_TANK_DKH:.3f}")
        check("tank_kh 양수(평탄 도달)", tank_kh > 0, f"got {tank_kh}")
        check("ref_ph/tank_ph 일치", ref_ph == REF_PH and tank_ph == TANK_PH,
              f"ref={ref_ph} tank={tank_ph}")
    check("tank 8회 측정(정확히 8회째 평탄)", sim.received.count('tank') == 8,
          f"got {sim.received.count('tank')}")
    check("ref 8회 측정", sim.received.count('ref') == 8, f"got {sim.received.count('ref')}")
    check("모터 8종 전부 수신", all(mm in sim.received for mm in MOTORS),
          f"received motors={[c for c in sim.received if c.startswith('m') and ':' in c]}")
    check("연결 1회(재연결 없음)", sim.connection_count == 1, f"got {sim.connection_count}")
    check("'calkh' 수신", 'calkh' in sim.received)


# ── 시나리오 2: 측정 중 드롭 → 송신 전 점검이 재연결 ───────
def scenario_drop_during_measure():
    print("\n[2] tank 4회 응답 후 드롭 → 다음 측정 송신 전 ensure_link 가 재연결")
    drops = [{'pat': 'tank', 'nth': 4, 'when': 'after'}]
    result, out, sim = run(lambda ser: mk.run_measurement(ser), drops=drops)
    check("결과 튜플 완성", result is not None and all(v is not None for v in result),
          f"result={result}")
    if result:
        check("tank dKH ≈ 8.142(정확도 유지)", abs(result[3] - EXPECT_TANK_DKH) < 0.01,
              f"got {result[3]}")
        check("tank_kh 양수(평탄 도달)", result[3] > 0)
    check("재연결 발생(연결 ≥2)", sim.connection_count >= 2, f"got {sim.connection_count}")
    check("'[RF]' 재연결 로그", '[RF]' in out)
    check("tank 결국 8회 성공", sim.received.count('tank') == 8, f"got {sim.received.count('tank')}")


# ── 시나리오 3: 모터 응답 중 드롭 → 재시도 시 정지(mNs) 후 재송신 ──
def scenario_motor_retry_stop():
    print("\n[3] m1f 첫 응답 전 드롭 → 재시도 시 m1s 정지 후 재송신(중복 구동 방지)")
    sim = FirmwareSim()
    sim.drops = [{'pat': 'm1f', 'nth': 1, 'when': 'before'}]
    port = sim.start(); time.sleep(0.1)
    ser = open_ser(port); time.sleep(0.1); ser.reset_input_buffer()
    buf = io.StringIO()
    lines = None
    try:
        with contextlib.redirect_stdout(buf):
            lines = mk.send(ser, 'm1f:5', stop_pattern='[모터1] 완료', timeout=3, keepalive=True)
    finally:
        try: ser.close()
        except Exception: pass
        sim.stop()
    out = buf.getvalue()
    got_done = bool(lines) and any('[모터1] 완료' in ln for ln in lines)
    check("최종 모터 완료 수신", got_done, f"lines={lines}")
    m1f_idx = [i for i, c in enumerate(sim.received) if c == 'm1f:5']
    m1s_idx = [i for i, c in enumerate(sim.received) if c == 'm1s']
    check("m1f 두 번 전송(드롭→재송신)", len(m1f_idx) == 2, f"m1f at {m1f_idx}")
    check("재시도 전 m1s 정지 삽입", len(m1s_idx) == 1, f"m1s at {m1s_idx}")
    if len(m1f_idx) == 2 and len(m1s_idx) == 1:
        check("순서: m1f → m1s → m1f", m1f_idx[0] < m1s_idx[0] < m1f_idx[1],
              f"m1f={m1f_idx} m1s={m1s_idx}")
    check("재연결 발생", sim.connection_count >= 2, f"got {sim.connection_count}")


# ── 시나리오 4: calref(--setref) 경로 ─────────────────────
def scenario_calref():
    print("\n[4] calref(--setref 8.448) — ref dKH 역산 경로")
    expect_new_ref = DEFAULT_REF_DKH * (10 ** (-(TANK_PH - REF_PH)))   # ≈ 8.765
    result, out, sim = run(lambda ser: mk.run_measurement(ser, tank_dkh=8.448))
    check("결과 튜플 완성", result is not None and all(v is not None for v in result),
          f"result={result}")
    if result:
        check("새 refDKH ≈ 8.765", abs(result[2] - expect_new_ref) < 0.01,
              f"got {result[2]} expect {expect_new_ref:.3f}")
    check("'setref:8.448' 수신", 'setref:8.448' in sim.received)
    check("'calref' 수신", 'calref' in sim.received)
    check("연결 1회(재연결 없음)", sim.connection_count == 1, f"got {sim.connection_count}")


# ── 예외 시나리오 ─────────────────────────────────────────
def scenario_total_loss():
    print("\n[5] 완전 통신 두절(서버 kill) → 우아한 실패(크래시·행 없음, 상위가 0.0 기록)")
    sim = FirmwareSim()
    sim.drops = [{'pat': 'tank', 'nth': 1, 'when': 'before', 'kill': True}]  # 첫 tank서 두절
    port = sim.start(); time.sleep(0.1)
    ser = open_ser(port); time.sleep(0.1); ser.reset_input_buffer()
    buf = io.StringIO(); raised = None
    try:
        with contextlib.redirect_stdout(buf):
            mk.run_measurement(ser)
    except Exception as e:
        raised = e
    finally:
        try: ser.close()
        except Exception: pass
        sim.stop()
    out = buf.getvalue()
    # main() 은 serial.SerialException·Exception 을 모두 잡아 0.0(에러 표식) 기록 → 우아한 실패.
    check("main 이 처리할 예외로 종료(크래시·행 없음)",
          isinstance(raised, (RuntimeError, serial.SerialException)), f"raised={raised!r}")
    check("재연결 시도 로그('[RF]')", '[RF]' in out)
    check("비상정리 시도", '[비상정리]' in out)


def scenario_garbled_measure():
    print("\n[6] 측정 응답 pH 누락(깨진 데이터) → 파싱 실패 → FAIL_MAX 로 phase 실패")
    sim = FirmwareSim(); sim.garble = {'tank'}
    port = sim.start(); time.sleep(0.1)
    ser = open_ser(port); time.sleep(0.1); ser.reset_input_buffer()
    buf = io.StringIO(); raised = None
    try:
        with contextlib.redirect_stdout(buf):
            mk.run_measurement(ser)
    except Exception as e:
        raised = e
    finally:
        try: ser.close()
        except Exception: pass
        sim.stop()
    out = buf.getvalue()
    check("측정실패 카운트 동작", '[측정실패' in out)
    check("tank phase 실패→RuntimeError", isinstance(raised, RuntimeError), f"raised={raised!r}")
    check("재연결 없음(연결 문제 아님)", sim.connection_count == 1, f"got {sim.connection_count}")


def scenario_motor_no_complete():
    print("\n[7] 모터 '완료' 누락(튜브 막힘) → 재시도(정지+재송신) 소진 후 미완료 처리")
    sim = FirmwareSim(); sim.no_done = {'m1f'}
    port = sim.start(); time.sleep(0.1)
    ser = open_ser(port); time.sleep(0.1); ser.reset_input_buffer()
    buf = io.StringIO(); lines = None
    try:
        with contextlib.redirect_stdout(buf):
            lines = mk.send(ser, 'm1f:1', stop_pattern='[모터1] 완료', timeout=0.5, keepalive=True)
    finally:
        try: ser.close()
        except Exception: pass
        sim.stop()
    check("완료 미수신(미완료로 반환)", not (lines and any('[모터1] 완료' in ln for ln in lines)),
          f"lines={lines}")
    check("m1f 재송신(SEND_RETRY_MAX회)", sim.received.count('m1f:1') == mk.SEND_RETRY_MAX,
          f"got {sim.received.count('m1f:1')}")
    check("재시도마다 정지(m1s) 삽입", sim.received.count('m1s') == mk.SEND_RETRY_MAX - 1,
          f"got {sim.received.count('m1s')}")


def scenario_burst_recover():
    print("\n[8] 버스트(연속 2회 드롭) 후 회복 → 측정 완주")
    drops = [{'pat': 'tank', 'nth': 3, 'when': 'after'},
             {'pat': 'tank', 'nth': 4, 'when': 'after'}]
    result, out, sim = run(lambda ser: mk.run_measurement(ser), drops=drops)
    check("결과 튜플 완성", result is not None and all(v is not None for v in result),
          f"result={result}")
    if result:
        check("tank dKH ≈ 8.142(정확도 유지)", abs(result[3] - EXPECT_TANK_DKH) < 0.01,
              f"got {result[3]}")
    check("재연결 2회 이상", sim.connection_count >= 3, f"got {sim.connection_count}")
    check("tank 결국 8회 성공", sim.received.count('tank') == 8, f"got {sim.received.count('tank')}")


def main():
    print("=" * 56)
    print("measure_kh_once 통합 테스트 (firmware_sim)")
    print(f"  기대 tank dKH(클린) ≈ {EXPECT_TANK_DKH:.3f}")
    print("=" * 56)
    print("\n── 정상 시나리오 ──")
    scenario_clean()
    scenario_drop_during_measure()
    scenario_motor_retry_stop()
    scenario_calref()
    print("\n── 예외 시나리오 ──")
    scenario_total_loss()
    scenario_garbled_measure()
    scenario_motor_no_complete()
    scenario_burst_recover()
    print("\n" + "=" * 56)
    print(f"결과: {_passed} PASS / {_failed} FAIL")
    print("=" * 56)
    sys.exit(1 if _failed else 0)


if __name__ == '__main__':
    main()
