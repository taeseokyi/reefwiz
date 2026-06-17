#!/usr/bin/env python3
"""
AquaWiz KH 1회 측정 (V4 — 평형(plateau) 추종 측정)
HC-06 블루투스 시리얼로 한 번만 측정하고 dkh.dat 에 기록 후 종료.

V4 (2026-06-17): "측정 중 폭기 + 윈도우 평탄도로 *진짜 평형*까지" 측정.
  - tank·ref 를 폭기 유지(ron)한 채 반복 측정, 최근 FLAT_N개 max-min ≤ FLAT_EPS 면
    평형 도달로 보고 종료. → 격차(시작 CO₂)에 자동 적응(평탄할 때까지 측정).
    차동에서 ref·tank 가 같은 평형(실내공기 pCO₂)에 도달 → 방 CO₂ 상쇄.
  - [A] tank·ref 동시 폭기 + tank 평탄까지 측정 (이때 ref 는 홀딩 챔버서 함께 폭기 = co-aeration).
    [B] tank 완전 배출(오염방지, 단축불가) → ref 측정챔버 이송 → 폭기 + 평탄까지 측정
       (co-aeration 덕에 평형 근처라 빨리 끝남). 전이(A→B)는 무대기로 최대한 조임
       (t2−t1 최소화 = 측정 사이 방 CO₂ 표류 잔류 최소화).
  - ★무한 대기 방지: phase 별 PHASE_MAX_SECS·MEAS_MAX·연속실패 FAIL_MAX 상한,
    시리얼 read 타임아웃. 평탄 미도달 시 마지막값+경고로 종료(행 안 함).
  - ★규칙: 액체 이동(mXf/mXb) 직전 airoff. ron=에어(D12)·ton=PWM(D13) 독립, airoff=둘 다 OFF.
  - 오류/비정상 종료 시 비상 정리(_safe_cleanup): 에어 OFF + 측정챔버 배출 + KCl 소크 복원.
  ※ 측정 중 폭기라 절대 pH 에 흐름(streaming) 오프셋 — V3 이전 절대값과 직접 비교 금지
    (오프셋은 ref·tank 공통모드라 ΔpH/dKH 엔 무영향).
  ※ 널테스트 한정: ref 채우기 전 헹굼 생략. 정상운영(KH 다름) 복귀 시 복원 필수.

dkh.dat 형식 (한 줄에 하나):
  HH ref_pH tank_pH ref_kh tank_kh temp
  14 7.823 7.412 8.523 7.901 25.3
  15 0.000 0.000 0.000 0.000 0.0   ← 오류/타임아웃/KCl 소크 실패 시 (에러 표식)
"""

import serial
import time
import re
import sys
import os
from datetime import datetime

PORT     = 'COM15'
BAUD     = 9600
CLEAN_SECS     = 60      # KCl 헹굼 기포 청소(초) — 액체 정지 중에만 폭기(이동 전 airoff)

# ── 윈도우 평탄도(평형) 판정 — measure_until_flat ──
FLAT_N         = 4       # 최근 N개 읽기로 판정 (CSV 검증: K=4)
FLAT_EPS       = 0.002   # 최근 N개 max-min ≤ 이 값이면 평탄(평형) (CSV 검증: 0.002→8.100 정확)
MEAS_INTERVAL  = 40      # 측정 간 간격(초) — 이 동안 폭기 지속
# ── ★무한 대기 방지 상한 ──
PHASE_MAX_SECS = 2400    # phase(tank/ref)별 최대 측정 시간(초). 초과 시 마지막값+경고
MEAS_MAX       = 80      # phase별 최대 측정 횟수(백스톱)
FAIL_MAX       = 5       # 연속 측정 파싱 실패 허용 횟수 → 초과 시 phase 실패

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
        print(f"\n===== measure_kh_once V4 {datetime.now():%Y-%m-%d %H:%M:%S} =====")


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


def _motor_ok(lines, idx):
    """send_motor 결과에 '[모터idx] 완료'가 있으면 True (타임아웃·무응답이면 False)."""
    return any(f'[모터{idx}] 완료' in ln for ln in (lines or []))


# ─────────────────────────────────────────────
# 평형(plateau) 추종 측정 — 윈도우 평탄도
# ─────────────────────────────────────────────

def parse_ph(lines, label):
    """측정 출력 '[수조수] V:.. pH:.. T:..C'에서 pH 추출. 실패 시 None."""
    for line in lines:
        m = re.search(rf'\[{label}\].*pH:([\d.]+)', line)
        if m:
            return float(m.group(1))
    return None


def measure_until_flat(ser, what):
    """폭기 켠 채(ron, 호출자가 ON 상태로 진입) what('tank'/'ref')를 반복 측정.
    최근 FLAT_N개 pH의 (max-min) ≤ FLAT_EPS 면 평형(평탄) 도달로 보고 종료.
    펌웨어가 마지막 측정값을 refPH/tankPH 에 보관하므로 최종(평탄) 값이 calkh 에 쓰인다.

    ★무한 대기 방지: 경과 PHASE_MAX_SECS 또는 측정 MEAS_MAX 회 초과 시 마지막값+경고로 종료.
      연속 파싱 실패 FAIL_MAX 회 초과 시 실패(ph=None) 반환.
    반환: (ph, n_reads, flat_ok). ph=None 이면 측정 실패(응답 없음/계속 실패)."""
    label = '수조수' if what == 'tank' else '참조수'
    window = []
    last_ph = None
    fails = 0
    t0 = time.time()
    n = 0
    while True:
        n += 1
        lines = send(ser, what, stop_pattern='[OK]', timeout=20)
        ph = parse_ph(lines, label)
        if ph is None:
            fails += 1
            print(f"    [측정실패 {fails}/{FAIL_MAX}] {what}")
            if fails >= FAIL_MAX:
                print(f"    [실패] {what} 연속 {FAIL_MAX}회 응답 이상 — phase 중단")
                return last_ph, n, False
            # 실패는 측정 횟수엔 세되, 평탄 윈도우엔 미반영
        else:
            fails = 0
            last_ph = ph
            window.append(ph)
            if len(window) > FLAT_N:
                window.pop(0)
            elapsed = int(time.time() - t0)
            if len(window) >= FLAT_N:
                span = max(window) - min(window)
                print(f"    [{what}] {n}회 pH:{ph:.3f} 최근{FLAT_N}span:{span:.3f} ({elapsed}s)")
                if span <= FLAT_EPS:
                    print(f"    [평탄] {what} {n}회 — 최근{FLAT_N} span={span:.3f}≤{FLAT_EPS} → 평형 (pH {ph:.3f})")
                    return ph, n, True
            else:
                print(f"    [{what}] {n}회 pH:{ph:.3f} (윈도우 채우는 중 {len(window)}/{FLAT_N}, {elapsed}s)")

        # ── 무한 대기 방지 ──
        if time.time() - t0 >= PHASE_MAX_SECS:
            print(f"    [상한] {what} {PHASE_MAX_SECS}s 초과 — 미평탄, 마지막값 {last_ph} 채택")
            return last_ph, n, False
        if n >= MEAS_MAX:
            print(f"    [상한] {what} 측정 {MEAS_MAX}회 초과 — 미평탄, 마지막값 {last_ph} 채택")
            return last_ph, n, False
        time.sleep(MEAS_INTERVAL)


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
# 비상 정리 (오류/비정상 종료 시 프로브를 KCl 에 소크)
# ─────────────────────────────────────────────

def _safe_cleanup(ser):
    """에어 OFF + 측정챔버 배출 + KCl 소크 복원. 각 단계 guard(예외/타임아웃 무시).
    KCl 소크가 끝내 실패하면 큰 경고를 남긴다(이 경로에선 main 이 dkh.dat 에 0.0 기록)."""
    print("\n[비상정리] 에어 OFF + 측정챔버 배출 + KCl 소크 복원 시도")
    for cmd, stop in (('airoff', 'OFF'), ('ton', '수조ON')):
        try: send(ser, cmd, stop_pattern=stop, timeout=5)
        except Exception: pass
    try: send_motor(ser, 1, 'm1b:82')   # 측정챔버 배출(오염방지 완전배출)
    except Exception: pass
    kcl_ok = False
    try:
        kcl_lines = send_motor(ser, 3, 'm3f:60')   # KCl 소크
        kcl_ok = _motor_ok(kcl_lines, 3)
    except Exception: pass
    try: send(ser, 'airoff', stop_pattern='OFF', timeout=5)
    except Exception: pass
    if not kcl_ok:
        print("★★[경고] 비상 KCl 소크도 미완료 — 프로브가 KCl 없이 방치됐을 수 있음! 수동 확인 필요")


# ─────────────────────────────────────────────
# 측정 루틴 (V4)
# ─────────────────────────────────────────────

def run_measurement(ser):
    completed = False
    try:
        # ── 준비: KCl 배출 → 헹굼물 채움 → 기포 청소(정지 중) → 배출 → 측정수 채움 → ref 홀딩 ──
        send(ser, 'airoff', stop_pattern='OFF')
        send(ser, 'ton', stop_pattern='수조ON')
        print("\n[준비] KCl 배출 (측정 챔버)")
        send_motor(ser, 3, 'm3b:68')
        print("\n[tank] 측정 챔버 헹굼물 채움")
        send_motor(ser, 1, 'm1f:60')
        print(f"\n[tank] 기포 청소 {CLEAN_SECS}초 (KCl 잔막 — 액체 정지 중 폭기)")
        send(ser, 'ron', stop_pattern='참조ON')
        time.sleep(CLEAN_SECS)
        send(ser, 'airoff', stop_pattern='OFF')          # ★액체 이동 전 airoff
        send(ser, 'ton', stop_pattern='수조ON')
        print("\n[tank] 더러운 헹굼물 배출 → 본수조")
        send_motor(ser, 1, 'm1b:72')
        print("\n[tank] 본수조수 채움 (측정용 → 측정 챔버)")
        send_motor(ser, 1, 'm1f:70')
        print("\n[ref] 참조수 → 홀딩 챔버")
        send_motor(ser, 4, 'm4f:60')

        # ── [A] 양쪽 동시 폭기 + tank 평탄(평형)까지 측정 (ref 는 홀딩서 함께 폭기) ──
        send(ser, 'airoff', stop_pattern='OFF')
        print("\n[폭기] ON (측정챔버 tank + 홀딩 ref 동시) — tank 평탄까지 측정")
        send(ser, 'ron', stop_pattern='참조ON')
        tank_ph, tank_n, tank_flat = measure_until_flat(ser, 'tank')
        if tank_ph is None:
            raise RuntimeError("tank 측정 실패(응답 없음)")

        # ── 전이(무대기, 최대한 조임): tank 완전배출 → ref 이송 ──
        send(ser, 'airoff', stop_pattern='OFF')          # ★액체 이동 전 airoff
        send(ser, 'ton', stop_pattern='수조ON')
        print("\n[tank] 수조수 완전 배출 → 본수조 (오염방지, 단축 불가)")
        send_motor(ser, 1, 'm1b:82')
        print("\n[ref] 홀딩 → 측정 챔버")
        send_motor(ser, 2, 'm2f:60')

        # ── [B] ref 폭기 + 평탄(평형)까지 측정 (co-aeration 이라 평형 근처서 시작 → 빨리) ──
        send(ser, 'airoff', stop_pattern='OFF')
        send(ser, 'ron', stop_pattern='참조ON')
        print("\n[폭기] ON — ref 평탄까지 측정")
        ref_ph, ref_n, ref_flat = measure_until_flat(ser, 'ref')
        if ref_ph is None:
            raise RuntimeError("ref 측정 실패(응답 없음)")

        # ── KH 계산 (펌웨어 저장 refPH/tankPH = 각 phase 마지막 평탄값) ──
        print("\n[ref] KH 계산")
        kh_lines = send(ser, 'calkh', stop_pattern='===========', timeout=10)

        # ── 정상 정리: ref 회수 → KCl 소크 ──
        send(ser, 'ton', stop_pattern='수조ON')
        print("\n[정리] 참조수 배출 → 홀딩 → 위즈수조 (순환 회수)")
        send_motor(ser, 2, 'm2b:68')
        send_motor(ser, 4, 'm4b:68')
        print("\n[정리] KCl 공급 (프로브 소크)")
        kcl_lines = send_motor(ser, 3, 'm3f:60')
        send(ser, 'airoff', stop_pattern='OFF')
        if not _motor_ok(kcl_lines, 3):
            # KCl 소크가 조용히 실패(타임아웃/무응답)하면 측정값이 멀쩡해도 에러로 본다
            # → finally 의 _safe_cleanup 이 한 번 더 KCl 시도, main 은 0.0 기록.
            raise RuntimeError("KCl 소크(m3f) 미완료 — 프로브 소크 실패 → 에러(0.0) 기록")
        completed = True

        # ── 파싱·출력 ──
        ref_ph_r, tank_ph_r, ref_kh, tank_kh, temp = parse_results(kh_lines)
        print("\n" + "=" * 40)
        print("측정 결과 (V4)")
        print("=" * 40)
        if ref_ph_r  is not None: print(f"  참조수 pH : {ref_ph_r:.3f}")
        if tank_ph_r is not None: print(f"  수조수 pH : {tank_ph_r:.3f}")
        if ref_kh    is not None: print(f"  참조 dKH  : {ref_kh:.3f} dKH")
        if tank_kh   is not None: print(f"  수조 dKH  : {tank_kh:.3f} dKH")
        if temp      is not None: print(f"  온도      : {temp:.1f} C")
        print(f"  평탄도달 : tank {tank_n}회 {'O' if tank_flat else 'X(상한)'} / "
              f"ref {ref_n}회 {'O' if ref_flat else 'X(상한)'}")
        if not (tank_flat and ref_flat):
            print("  ※ 일부 phase 가 평탄 미도달(상한)로 종료 — 값 신뢰도 주의")
        if tank_kh is None: print("  dKH 파싱 실패")
        print("=" * 40)
        return (ref_ph_r, tank_ph_r, ref_kh, tank_kh, temp)
    finally:
        if not completed:
            _safe_cleanup(ser)


# ─────────────────────────────────────────────
# 메인 (1회 실행 후 종료)
# ─────────────────────────────────────────────

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else PORT
    now  = datetime.now()
    hour = now.hour

    print(f"AquaWiz KH 1회 측정 V4 — {port} @ {BAUD}baud")
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
