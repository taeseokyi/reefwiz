#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_kh_once.py 통합 회귀 테스트 (firmware_sim 소켓 가상 포트)

펌웨어 시뮬레이터로 측정 흐름을 상황별로 실전처럼 돌려, RF 순단 대응(송신 전 연결확인·재연결·
재시도·모터 재시도 시 정지)·keepalive·평탄(8회) 판정·calkh/calref 파싱을 검증한다.

★측정/BT 로직 변경 시 배포 *전* 항상 실행해 전부 PASS 확인(언제든 재실행 가능).
실행: cd bin && python3 test_measure_sim.py     (WSL python3 — pyserial 3.5)

총 20 시나리오 / 97 검증:
  ── 정상/회복 10 시나리오(57 검증) ──
    [1] 클린 calkh           (9) 전체 흐름·정확히 8회째 평탄·dKH·모터8종·재연결0
    [2] 측정 중 드롭(after)  (6) 송신 전 연결확인이 다음 측정 전 재연결, 정확도 유지
    [3] 모터 드롭→정지·재송신(5) 재시도 시 mNs 정지 후 재송신(순서 m1f→m1s→m1f)
    [4] calref(--setref)     (6) ref dKH 역산 경로 견고성 + 수조 dKH=입력값
    [9] 측정 in-send 재시도  (5) 측정 read 중 드롭→send 내부 재연결+재송신 성공(tank 9회)
    [10] calref 도중 드롭     (6) setref 재연결+재송신 후 역산 완주(setref 2회)
    [13] calref 기록(평탄)   (4) main 이 dkh.dat·reefCore 에 기록(수조 dKH=--setref 입력값, 양수)
    [14] calref 미평탄       (4) 상한 도달 시 수조 dKH 음수(-입력값) dkh.dat·reefCore 둘 다 발행
    [19] 링크 사망→복귀      (5) 평탄 phase 중 완전 두절→재기동→끈질긴 대기가 재접속·측정 재개·완주
    [20] 무딘 S커브 MIN_N    (8) 저진폭 lag false lock 재현(a) + FLAT_MIN_N_TANK=20 시 참평형 도달·ref 미적용(b)
  ── 예외 10 시나리오(40 검증) ──
    [5] 완전 통신 두절(kill) (3) main 이 잡는 예외로 우아하게 종료(크래시·행 없음)
    [6] 깨진 응답(pH 누락)   (3) 파싱 실패→FAIL_MAX phase 실패(연결문제 아님)
    [7] 모터 완료 누락(막힘) (3) 재시도(정지+재송신) 소진 후 미완료 처리
    [8] 버스트(연속 2회 드롭)(4) 두 번 재연결하며 완주, 정확도 유지
    [11] setref 예외         (4) 범위 밖=main 가드 차단 / 펌웨어 거부=측정 전 RuntimeError
    [12] 모터 정지 명령 드롭 (5) mNs 자체 드롭→다음 시도 재연결·재정지·재송신 완료
    [15] calkh 에러 발행     (5) 통신 두절→0(에러) dkh.dat·reefCore 발행 + _publishable 게이트(음수·0 발행, None 만 제외)
    [16] calref 에러 발행     (2) calref 실패도 calkh 와 동일하게 0(에러) dkh.dat·reefCore 발행
    [17] 에러 래치 공통       (6) calkh·calref 둘 다 래치 발동 시 측정 생략 + 0.0 기록·발행(에러 처리 완전 일치)
    [18] 호스트 구제          (4) calkh 직전 완전 두절→시작캐시 refKH 로 음수 dKH 기록·발행(0.0 래치 방지)

※ 테스트는 import 한 모듈의 타이밍 상수만 메모리에서 패치(빠른 실행). 소스 파일의 실전 상수는
  불변 → 배포본 정상 동작.
"""
import io
import sys
import time
import threading
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
mk.LINK_RETRY_INTERVAL = 0.5    # 링크 사망 끈질긴 대기(2026-07-03) 재접속 간격 단축
mk.FLAT_MIN_N_TANK = 0          # 기존 시나리오는 MIN_N 도입 전 게이트(8회 평탄)를 검증 — [20]이 MIN_N 전용 검증

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


def run(fn, drops=None, tank_dkh=None, tank_profile=None):
    """sim 시작→ser 연결→run_measurement→(result, captured_stdout, sim) 반환."""
    sim = FirmwareSim()
    sim.drops = drops or []
    sim.tank_profile = tank_profile
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
        check("수조 dKH = --setref 입력값 8.448(펌웨어 echo 아닌 입력값)",
              result[3] == 8.448, f"got {result[3]}")
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


def scenario_inmeasure_retry():
    print("\n[9] 측정 read 도중 드롭 → send 안에서 재연결+재시도해 성공(측정 in-send 재시도)")
    # 'before' 드롭: tank #3 명령을 받고 응답 전 끊음 → 그 send 가 응답 미수신 →
    #   다음 시도에서 ensure_link 재연결 후 *같은 tank 재송신* → 성공(드롭 후 ensure_link
    #   사전점검이 아니라 send 내부 재시도 경로를 탄다). 시나리오 2(after 드롭)와 달리 tank 재송신 발생.
    drops = [{'pat': 'tank', 'nth': 3, 'when': 'before'}]
    result, out, sim = run(lambda ser: mk.run_measurement(ser), drops=drops)
    check("결과 튜플 완성", result is not None and all(v is not None for v in result),
          f"result={result}")
    if result:
        check("tank dKH ≈ 8.142(회복)", abs(result[3] - EXPECT_TANK_DKH) < 0.01, f"got {result[3]}")
    check("재연결 발생(연결 ≥2)", sim.connection_count >= 2, f"got {sim.connection_count}")
    check("드롭된 tank 재송신(9회=8+재송신1)", sim.received.count('tank') == 9,
          f"got {sim.received.count('tank')}")
    check("'[RF]' 재시도 로그", '[RF]' in out)


def scenario_calref_drop():
    print("\n[10] calref(--setref) 도중 드롭 → setref 재연결+재송신 후 ref dKH 역산 완주")
    # setref 는 calref 모드 고유 명령(첫 단계). 응답 전 드롭→send 재시도로 재연결+재송신→성공.
    expect_new_ref = DEFAULT_REF_DKH * (10 ** (-(TANK_PH - REF_PH)))   # ≈ 8.765
    drops = [{'pat': 'setref', 'nth': 1, 'when': 'before'}]
    result, out, sim = run(lambda ser: mk.run_measurement(ser, tank_dkh=8.448), drops=drops)
    check("결과 튜플 완성", result is not None and all(v is not None for v in result),
          f"result={result}")
    if result:
        check("새 refDKH ≈ 8.765(회복)", abs(result[2] - expect_new_ref) < 0.01, f"got {result[2]}")
    check("setref 재송신(2회=드롭+재송신)", sim.received.count('setref:8.448') == 2,
          f"got {sim.received.count('setref:8.448')}")
    check("재연결 발생(연결 ≥2)", sim.connection_count >= 2, f"got {sim.connection_count}")
    check("'calref' 끝까지 수신", 'calref' in sim.received)
    check("'[RF]' 재시도 로그", '[RF]' in out)


def scenario_setref_exception():
    print("\n[11] setref 예외 — (a) 범위 밖 입력은 main 가드가 측정 전 차단 (b) 펌웨어 거부 시 우아한 실패")
    # (a) main() 범위 가드: --setref 가 펌웨어 허용범위(0.5~30) 밖이면 시리얼 접속 전 조기 거부
    buf = io.StringIO(); argv_bak = sys.argv
    sys.argv = ['measure_kh_once.py', '--setref', '40']
    try:
        with contextlib.redirect_stdout(buf):
            mk.main()
    finally:
        sys.argv = argv_bak
    out_a = buf.getvalue()
    check("(a) main 범위 가드가 측정 전 거부", '[ERR]' in out_a and '범위' in out_a,
          f"out={out_a.strip()[:80]}")

    # (b) 가드 우회(run_measurement 직접 호출) — 펌웨어가 setref 거부([ERR]) → 측정 전 RuntimeError
    sim = FirmwareSim()
    port = sim.start(); time.sleep(0.1)
    ser = open_ser(port); time.sleep(0.1); ser.reset_input_buffer()
    buf = io.StringIO(); raised = None
    sr_bak = mk.SEND_RETRY_MAX
    mk.SEND_RETRY_MAX = 1            # 결정적 [ERR]엔 재시도 무의미 → 테스트 시간 단축
    try:
        with contextlib.redirect_stdout(buf):
            mk.run_measurement(ser, tank_dkh=40.0)
    except Exception as e:
        raised = e
    finally:
        mk.SEND_RETRY_MAX = sr_bak
        try: ser.close()
        except Exception: pass
        sim.stop()
    check("(b) 펌웨어 거부 시 RuntimeError(setref 실패)", isinstance(raised, RuntimeError),
          f"raised={raised!r}")
    check("(b) setref 는 전송됨", 'setref:40.000' in sim.received, f"received={sim.received}")
    check("(b) 측정 전 중단(tank 미측정)", sim.received.count('tank') == 0,
          f"tank={sim.received.count('tank')}")


def scenario_motor_stop_drop():
    print("\n[12] 모터 정지(mNs) 명령 자체 드롭 → 다음 시도서 재연결·재정지 후 완료")
    # m1f 첫 시도 드롭(→재시도 진입) + 그 재시도의 m1s 정지도 드롭 → 또 한 번 재시도서
    #   재연결+재정지(m1s)+재송신(m1f) 으로 완료. _stop_motor 는 best-effort라 그 자체는 재시도
    #   안 하지만, 바깥 send 재시도 루프가 다음 시도에 ensure_link 재연결 후 정지를 다시 발행한다.
    sim = FirmwareSim()
    sim.drops = [{'pat': 'm1f', 'nth': 1, 'when': 'before'},
                 {'pat': 'm1s', 'nth': 1, 'when': 'before'}]
    port = sim.start(); time.sleep(0.1)
    ser = open_ser(port); time.sleep(0.1); ser.reset_input_buffer()
    buf = io.StringIO(); lines = None
    try:
        with contextlib.redirect_stdout(buf):
            lines = mk.send(ser, 'm1f:5', stop_pattern='[모터1] 완료', timeout=0.5, keepalive=True)
    finally:
        try: ser.close()
        except Exception: pass
        sim.stop()
    got_done = bool(lines) and any('[모터1] 완료' in ln for ln in lines)
    check("최종 모터 완료(정지 명령 드롭에도 회복)", got_done, f"lines={lines}")
    m1f_idx = [i for i, c in enumerate(sim.received) if c == 'm1f:5']
    m1s_idx = [i for i, c in enumerate(sim.received) if c == 'm1s']
    check("m1f 재송신(2회)", len(m1f_idx) == 2, f"m1f at {m1f_idx}")
    check("m1s 재발행(2회=정지 드롭+재정지)", len(m1s_idx) == 2, f"m1s at {m1s_idx}")
    if m1f_idx and m1s_idx:
        check("최종 정지 후 최종 재송신(마지막 m1s < 마지막 m1f)", m1s_idx[-1] < m1f_idx[-1],
              f"m1s={m1s_idx} m1f={m1f_idx}")
    check("재연결 2회 이상(연결 ≥3)", sim.connection_count >= 3, f"got {sim.connection_count}")


def _drive_main(setref=None, meas_max=None, drops=None, dat_error=False):
    """main() 을 socket 가상포트로 구동하고 log_kh·publish_to_reefcore 호출 인자를 가로채 반환.
    serial.Serial 을 socket:// 로 바꿔 sim 과 통신(실 BT·실 reefCore 발행 없음).
    last_dat_is_error 도 패치(기본 False) → 실 dkh.dat 와 무관하게 측정 진행; dat_error=True 로 래치 경로 테스트.
    returns (logged, published, out)."""
    sim = FirmwareSim(); sim.drops = drops or []
    port = sim.start(); time.sleep(0.1)
    logged, published = {}, {}
    orig_log, orig_pub = mk.log_kh, mk.publish_to_reefcore
    orig_serial, orig_argv, orig_max = mk.serial.Serial, sys.argv, mk.MEAS_MAX
    orig_late = mk.last_dat_is_error
    def fake_log(hour, ref_ph, tank_ph, ref_kh, tank_kh, temp):
        logged.update(dict(hour=hour, ref_ph=ref_ph, tank_ph=tank_ph,
                           ref_kh=ref_kh, tank_kh=tank_kh, temp=temp))
    def fake_pub(tank_kh, temp):
        published.update(dict(tank_kh=tank_kh, temp=temp))
    def fake_serial(p, b, timeout=1, **kw):
        # write_timeout 등 추가 kwargs 는 socket 가상포트에 그대로 전달(2026-07-03 write_timeout=5 대응)
        return serial.serial_for_url(p, baudrate=b, timeout=timeout, **kw)
    argv = ['measure_kh_once.py', f'socket://127.0.0.1:{port}']
    if setref is not None:
        argv += ['--setref', str(setref)]
    buf = io.StringIO()
    try:
        mk.log_kh, mk.publish_to_reefcore = fake_log, fake_pub
        mk.serial.Serial = fake_serial
        mk.last_dat_is_error = lambda: dat_error
        if meas_max is not None:
            mk.MEAS_MAX = meas_max
        sys.argv = argv
        with contextlib.redirect_stdout(buf):
            mk.main()
    finally:
        mk.log_kh, mk.publish_to_reefcore = orig_log, orig_pub
        mk.serial.Serial, sys.argv, mk.MEAS_MAX = orig_serial, orig_argv, orig_max
        mk.last_dat_is_error = orig_late
        sim.stop()
    return logged, published, buf.getvalue()


def scenario_calref_records():
    print("\n[13] calref(--setref) 평탄 → main 이 dkh.dat·reefCore 에 양수 기록(수조 dKH=입력값)")
    logged, published, out = _drive_main(setref=8.448)
    expect_new_ref = DEFAULT_REF_DKH * (10 ** (-(TANK_PH - REF_PH)))   # ≈ 8.765
    check("dkh.dat 기록됨(log_kh 호출)", bool(logged), f"logged={logged}")
    check("수조 dKH = 입력값 8.448(양수)", logged.get('tank_kh') == 8.448, f"got {logged.get('tank_kh')}")
    check("ref_kh 칼럼 = 역산된 새 ref dKH(≈8.765)",
          logged.get('ref_kh') is not None and abs(logged['ref_kh'] - expect_new_ref) < 0.01,
          f"got {logged.get('ref_kh')}")
    check("reefCore 발행 호출(tank_kh=입력값 8.448)", published.get('tank_kh') == 8.448,
          f"published={published}")


def scenario_calref_unflat():
    print("\n[14] calref 미평탄(상한) → 수조 dKH 음수(-입력값) dkh.dat·reefCore 둘 다 발행")
    # MEAS_MAX 를 FLAT_NET_N(8) 미만으로 낮춰 평탄 판정 전 상한 도달 → flat_ok=False 강제.
    logged, published, out = _drive_main(setref=8.448, meas_max=5)
    check("상한(미평탄) 로그", '[상한]' in out)
    check("dkh.dat 수조 dKH = 음수 표식 -8.448(값=입력값)", logged.get('tank_kh') == -8.448,
          f"got {logged.get('tank_kh')}")
    check("ref_kh 는 양수(부호는 tank_kh 만)", (logged.get('ref_kh') or 0) > 0, f"got {logged.get('ref_kh')}")
    check("reefCore 도 음수 -8.448 발행(미평탄 약속 전달)", published.get('tank_kh') == -8.448,
          f"published={published}")


def scenario_host_salvage():
    print("\n[18] calkh 직전 완전 두절 → 호스트 구제: 음수 dKH 기록·발행(0.0 래치 방지) (2026-07-03)")
    # 양 phase 평탄 후 calkh 만 못 돌린 상황 — 호스트가 시작 시 캐시한 refKH 로 동일 차동식 계산,
    # 음수(미평탄) 표식으로 기록해 0.0 래치(다음 측정 생략)를 피해야 한다.
    logged, published, out = _drive_main(
        drops=[{'pat': 'calkh', 'nth': 1, 'when': 'before', 'kill': True}])
    check("호스트 구제 로그", '호스트 구제' in out)
    check("dkh.dat 음수 구제값(≈-기대 dKH)",
          logged.get('tank_kh') is not None and abs(logged['tank_kh'] + EXPECT_TANK_DKH) < 0.01,
          f"got {logged.get('tank_kh')} (기대 {-EXPECT_TANK_DKH:.3f})")
    check("ref_kh = 시작 시 status 캐시(양수)",
          logged.get('ref_kh') is not None and abs(logged['ref_kh'] - DEFAULT_REF_DKH) < 0.001,
          f"got {logged.get('ref_kh')}")
    check("reefCore 도 음수 발행(0.0 래치 아님)",
          published.get('tank_kh') is not None and published['tank_kh'] < 0,
          f"published={published}")


def scenario_link_recovery():
    print("\n[19] 평탄 phase 중 링크 사망 → 복귀 → 측정 재개·완주(끈질긴 대기) (2026-07-03)")
    # tank 2회차 직전 서버 kill(완전 두절) → 3초 뒤 같은 포트에 sim 재기동('노트북 복귀' 모사)
    # → _wait_link_recovery 가 재접속해 측정 재개, 끝까지 정상(양수) 완주해야 한다.
    sim = FirmwareSim()
    sim.drops = [{'pat': 'tank', 'nth': 2, 'when': 'before', 'kill': True}]
    port = sim.start(); time.sleep(0.1)
    ser = open_ser(port); time.sleep(0.1); ser.reset_input_buffer()
    sim2 = FirmwareSim(port=port)
    timer = threading.Timer(3.0, sim2.start)
    timer.start()
    buf = io.StringIO(); result = None
    try:
        with contextlib.redirect_stdout(buf):
            result = mk.run_measurement(ser)
    finally:
        timer.cancel()
        try: ser.close()
        except Exception: pass
        sim.stop(); sim2.stop()
    out = buf.getvalue()
    check("링크 사망 끈질긴 대기 로그", '재접속 대기' in out)
    check("링크 복구 후 측정 재개 로그", '측정 재개' in out)
    check("결과 튜플 완성(완주)", result is not None and all(v is not None for v in result),
          f"result={result}")
    check("dKH 양수(정상 완주, 구제 아님)",
          result is not None and result[3] is not None and abs(result[3] - EXPECT_TANK_DKH) < 0.01,
          f"got {result and result[3]} (기대 {EXPECT_TANK_DKH:.3f})")
    check("호스트 구제 미발동", '호스트 구제' not in out)


def scenario_dull_scurve():
    print("\n[20] 무딘 S커브(저진폭 lag) → MIN_N 미적용=초기 lag false lock / 적용=참평형 도달 (2026-07-03)")
    # 7/3 05:00 실측 모사: 초기 lag(±1mpH 평평) 10샘플 → 완만 하강 40mpH → 진짜 평형 7.898.
    # 평형 접근은 지수(기울기∝진폭)라 저진폭 날은 lag 가 net8 창 감지한계 밑 → MIN_N 필요성 검증.
    lag  = [7.938, 7.939, 7.938, 7.938, 7.939, 7.938, 7.938, 7.938, 7.939, 7.938]
    fall = [round(7.938 - 0.003 * i, 3) for i in range(1, 14)]          # 7.935 → 7.899
    profile = lag + fall + [7.898]                                       # 소진 후 7.898 유지(평형)
    false_dkh = DEFAULT_REF_DKH * (10 ** (-(REF_PH - 7.938)))            # lag 서 잠기면 ≈ 8.105 (과대)
    true_dkh  = DEFAULT_REF_DKH * (10 ** (-(REF_PH - 7.898)))            # 참평형 ≈ 7.392

    # (a) MIN_N=0 (도입 전 동작) — 8회째 lag 에서 false lock 재현(버그 문서화)
    result, out, sim = run(lambda ser: mk.run_measurement(ser), tank_profile=profile)
    check("(a) lag 서 8회 false lock", sim.received.count('tank') == 8,
          f"got {sim.received.count('tank')}")
    check("(a) 과대 dKH(≈false)", result is not None and result[3] is not None
          and abs(result[3] - false_dkh) < 0.01, f"got {result and result[3]} expect {false_dkh:.3f}")
    check("(a) 참값 대비 +0.3 이상 이탈", result is not None and result[3] is not None
          and result[3] - true_dkh > 0.3, f"got {result and result[3]} true {true_dkh:.3f}")

    # (b) MIN_N=20 (실전값) — lag 보류 → 하강 관찰 → 참평형서 잠금
    mk.FLAT_MIN_N_TANK = 20
    try:
        result, out, sim = run(lambda ser: mk.run_measurement(ser), tank_profile=profile)
    finally:
        mk.FLAT_MIN_N_TANK = 0
    check("(b) lag 구간 평탄보류 발동", '평탄보류' in out)
    check("(b) 하강 종료 후 잠금(≥25회)", sim.received.count('tank') >= 25,
          f"got {sim.received.count('tank')}")
    check("(b) 참평형 dKH(≈true)", result is not None and result[3] is not None
          and abs(result[3] - true_dkh) < 0.01, f"got {result and result[3]} expect {true_dkh:.3f}")
    check("(b) 양수(평탄 도달, 미평탄 표식 아님)", result is not None and result[3] is not None
          and result[3] > 0, f"got {result and result[3]}")
    check("(b) ref 는 MIN_N 미적용(8회 빠른 잠금 유지)", sim.received.count('ref') == 8,
          f"got {sim.received.count('ref')}")


def scenario_calkh_error_publish():
    print("\n[15] calkh 측정 실패(통신 두절) → dkh.dat·reefCore 둘 다 0(에러) 발행")
    # 첫 tank 직전 서버 kill → run_measurement 예외 → main 이 0.0 행 기록 + 0.0 발행.
    logged, published, out = _drive_main(drops=[{'pat': 'tank', 'nth': 1, 'when': 'before', 'kill': True}])
    check("dkh.dat 에러 표식 0.0 기록", logged.get('tank_kh') == 0.0, f"got {logged.get('tank_kh')}")
    check("reefCore 도 0.0(에러) 발행", published.get('tank_kh') == 0.0, f"published={published}")
    # 발행 게이트: None 만 제외, 음수(미평탄)·0(에러)은 발행 허용.
    check("_publishable: 음수 True", mk._publishable(-8.448) is True)
    check("_publishable: 0 True", mk._publishable(0.0) is True)
    check("_publishable: None False", mk._publishable(None) is False)


def scenario_calref_error_publish():
    print("\n[16] calref 측정 실패(통신 두절) → calkh 와 동일하게 0(에러) dkh.dat·reefCore 발행")
    # 에러 처리 일치 검증: calref 도 실패 시 [15] calkh 와 똑같이 0.0 기록·발행해야 함.
    logged, published, out = _drive_main(setref=8.448, drops=[{'pat': 'tank', 'nth': 1, 'when': 'before', 'kill': True}])
    check("dkh.dat 에러 표식 0.0 기록(calref)", logged.get('tank_kh') == 0.0, f"got {logged.get('tank_kh')}")
    check("reefCore 도 0.0(에러) 발행(calref)", published.get('tank_kh') == 0.0, f"published={published}")


def scenario_latch_consistency():
    print("\n[17] 에러 래치 calkh·calref 공통 → 측정 생략 + 0.0 기록·발행")
    # 마지막 dkh.dat 줄이 에러(0.0)면 두 모드 모두 측정 안 하고 0.0 재기록·발행(에러 처리 일치).
    for label, setref in (("calkh", None), ("calref", 8.448)):
        logged, published, out = _drive_main(setref=setref, dat_error=True)
        check(f"[{label}] 래치 발동(측정 생략 로그)", '[중단]' in out, f"out={out[:80]}")
        check(f"[{label}] dkh.dat 0.0 재기록", logged.get('tank_kh') == 0.0, f"got {logged.get('tank_kh')}")
        check(f"[{label}] reefCore 0.0 발행", published.get('tank_kh') == 0.0, f"published={published}")


def main():
    print("=" * 56)
    print("measure_kh_once 통합 테스트 (firmware_sim)")
    print(f"  기대 tank dKH(클린) ≈ {EXPECT_TANK_DKH:.3f}")
    print("=" * 56)
    print("\n── 정상/회복 시나리오 ──")
    scenario_clean()
    scenario_drop_during_measure()
    scenario_motor_retry_stop()
    scenario_calref()
    scenario_inmeasure_retry()
    scenario_calref_drop()
    print("\n── 예외 시나리오 ──")
    scenario_total_loss()
    scenario_garbled_measure()
    scenario_motor_no_complete()
    scenario_burst_recover()
    scenario_setref_exception()
    scenario_motor_stop_drop()
    scenario_calref_records()
    scenario_calref_unflat()
    scenario_host_salvage()
    scenario_link_recovery()
    scenario_dull_scurve()
    scenario_calkh_error_publish()
    scenario_calref_error_publish()
    scenario_latch_consistency()
    print("\n" + "=" * 56)
    print(f"결과: {_passed} PASS / {_failed} FAIL")
    print("=" * 56)
    sys.exit(1 if _failed else 0)


if __name__ == '__main__':
    main()
