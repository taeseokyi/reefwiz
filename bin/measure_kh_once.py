#!/usr/bin/env python3
"""
AquaWiz KH 1회 측정 (V4 — 평형(plateau) 추종 측정)
HC-06 블루투스 시리얼로 한 번만 측정하고 dkh.dat 에 기록 후 종료.

V4 (2026-06-17): "측정 중 폭기 + 진짜 평형(평탄)까지" 측정.
  - tank·ref 를 폭기 유지(ron)한 채 반복 측정, **최근 FLAT_SPAN_N개 max−min ≤ FLAT_SPAN_MPH(흔들림)
    AND 최근 FLAT_NET_N개 양끝차 ≤ FLAT_NET_MPH(드리프트)** 면 평형 도달로 보고 종료. ★B1: net 룩백을
    span보다 길게(8 vs 4) 둬 느린 단조 꼬리 조기 latch 차단(정수 비교라 float 지터 없음).
    → 격차(시작 CO₂)에 자동 적응(평탄할 때까지 측정).
    차동에서 ref·tank 가 같은 평형(실내공기 pCO₂)에 도달 → 방 CO₂ 상쇄.
  - ★tank 먼저 + 5L 위즈수조 동시 폭기(2026-06-20): m1=본수조↔홀딩, m2=홀딩↔측정챔버,
    m4=참조수(5L)↔측정챔버, m3=KCl↔측정챔버.
    [A] tank(본수조수)를 측정챔버서 폭기+평탄까지 측정. 이 동안 5L 위즈수조가 *동시 폭기*돼
        ref 가 5L서 co-aeration → 평형 도달.
    [B] tank 를 홀딩에 임시 파킹(빠른 비움) → 즉시 ref(5L) 이송 → 폭기+평탄까지 측정
        (ref 가 동시폭기로 이미 평형 근처라 빨리 끝남). 파킹 tank 는 ref 측정 후 마무리 배출.
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
  - ★에러 래치: 마지막 줄이 에러 표식(값 전부 0)이면, 다음 실행은 *측정하지 않고*
    에러 표식만 재기록한다(수동으로 마지막 에러 줄을 지우기 전까지). 프로브 보호를
    위해 오류 상태에서 무인 반복측정을 멈춤.
  - ★평탄 미도달 표식: 정상이지만 일부 phase 가 평탄(평형) 미도달이면 측정 경도(tank_kh)에
    음수(-) 부호를 붙인다(값 크기는 유지). 서버가 부호로 "미평탄"을 인지.
"""

import serial
import time
import re
import sys
import os
from datetime import datetime

PORT     = 'COM9'
BAUD     = 9600

# ── 평형(평탄) 판정 — measure_until_flat (정수 milli-pH 윈도우; float 비교 지터 회피) ──
FLAT_SPAN_N    = 4       # 흔들림(span) 판정 윈도우 — 최근 N개
FLAT_SPAN_MPH  = 2       # 최근 FLAT_SPAN_N개 max−min ≤ 2 mpH (흔들림 폭). 정수 비교라 float 지터 없음
FLAT_NET_N     = 8       # ★B1(2026-06-20): 드리프트(net) 판정 룩백 — span보다 긴 창(≈5분)
FLAT_NET_MPH   = 1       # 최근 FLAT_NET_N개 양끝 |win[-1]-win[0]| ≤ 1 mpH. 짧은 4점은 느린 단조 꼬리서 net=1 위장(20:29 tank 10회 조기 latch) → 8점으로 차단
MEAS_INTERVAL  = 30      # 측정 간 간격(초) — 폭기 지속
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


def last_dat_is_error():
    """dkh.dat 마지막(비어있지 않은) 줄이 에러 표식(5개 값 전부 0)인지.
    파일 없음/빈 파일/파싱 실패면 False(정상 측정 진행)."""
    try:
        with open(DAT_FILE) as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return False
    if not lines:
        return False
    parts = lines[-1].split()
    if len(parts) < 6:
        return False
    try:
        vals = [float(x) for x in parts[1:6]]   # ref_pH tank_pH ref_kh tank_kh temp
    except ValueError:
        return False
    return all(v == 0.0 for v in vals)


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
# 평형(plateau) 추종 측정 — 정수 milli-pH 윈도우 span
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
    평형 판정 = 최근 FLAT_SPAN_N개 (max−min) ≤ FLAT_SPAN_MPH (흔들림) AND
                최근 FLAT_NET_N개 양끝차 ≤ FLAT_NET_MPH (드리프트).
    ★B1: net 룩백(FLAT_NET_N)을 span(FLAT_SPAN_N)보다 길게 둬 느린 단조 꼬리 조기 latch 차단.
    펌웨어가 마지막 측정값을 refPH/tankPH 에 보관하므로 최종(평탄) 값이 calkh 에 쓰인다.

    ★무한 대기 방지: 경과 PHASE_MAX_SECS 또는 측정 MEAS_MAX 회 초과 시 마지막값+경고로 종료.
      연속 파싱 실패 FAIL_MAX 회 초과 시 실패(ph=None) 반환.
    반환: (ph, n_reads, flat_ok). ph=None 이면 측정 실패(응답 없음/계속 실패)."""
    label = '수조수' if what == 'tank' else '참조수'
    win = []          # 최근 FLAT_NET_N개 정수 milli-pH (span은 뒤 FLAT_SPAN_N개로 판정)
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
            # 실패는 측정 횟수엔 세되, 윈도우엔 미반영
        else:
            fails = 0
            last_ph = ph
            elapsed = int(time.time() - t0)
            win.append(round(ph * 1000))         # 정수 milli-pH (float 비교 지터 회피)
            if len(win) > FLAT_NET_N:
                win.pop(0)
            if len(win) >= FLAT_SPAN_N:
                span = max(win[-FLAT_SPAN_N:]) - min(win[-FLAT_SPAN_N:])           # 최근 FLAT_SPAN_N개 흔들림 폭
                net  = abs(win[-1] - win[0]) if len(win) >= FLAT_NET_N else None    # ★최근 FLAT_NET_N개 양끝차=드리프트
                netstr = f"{net}" if net is not None else f"-({len(win)}/{FLAT_NET_N})"
                print(f"    [{what}] {n}회 pH:{ph:.3f} span{FLAT_SPAN_N}:{span}mpH net{FLAT_NET_N}:{netstr}mpH ({elapsed}s)")
                if span <= FLAT_SPAN_MPH and (net is not None) and net <= FLAT_NET_MPH:
                    print(f"    [평탄] {what} {n}회 — span{FLAT_SPAN_N}={span}≤{FLAT_SPAN_MPH} AND net{FLAT_NET_N}={net}≤{FLAT_NET_MPH} → 평형 (pH {ph:.3f})")
                    return ph, n, True
            else:
                print(f"    [{what}] {n}회 pH:{ph:.3f} (윈도우 {len(win)}/{FLAT_SPAN_N}, {elapsed}s)")

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
    # ★호스 스왑 후 측정챔버 배출 경로 = m2(측정챔버→홀딩) → m1(홀딩→본수조).
    #   (m1 단독은 이제 홀딩↔본수조라 측정챔버를 못 비움 → KCl 오버플로 방지 위해 m2 먼저)
    try: send_motor(ser, 2, 'm2b:68')   # 측정챔버 → 홀딩 (비우기)
    except Exception: pass
    try: send_motor(ser, 1, 'm1b:82')   # 홀딩 → 본수조 (배출)
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
        # ── 준비: KCl 배출 → tank(본수조수) 측정챔버로 이송 (m1→m2) ──
        #    ★배관(2026-06-20): m1=본수조↔홀딩, m2=홀딩↔측정챔버, m4=참조수(5L)↔측정챔버, m3=KCl↔측정챔버.
        #    ★측정 순서 = tank 먼저 → ref 나중. 이유: 5L 위즈수조가 *동시 폭기*돼 ref 가 tank 측정 내내
        #      5L서 co-aeration → ref 차례엔 이미 평형 근처 → ref 측정이 빠름.
        send(ser, 'airoff', stop_pattern='OFF')
        send(ser, 'ton', stop_pattern='수조ON')
        print("\n[준비] KCl 배출 (측정 챔버)")
        send_motor(ser, 3, 'm3b:68')
        print("\n[tank] 본수조수 → 홀딩 (m1)")
        send_motor(ser, 1, 'm1f:70')
        print("\n[tank] 홀딩 → 측정 챔버 (m2)")
        send_motor(ser, 2, 'm2f:60')

        # ── [A] 폭기 ON (측정챔버 tank + 5L 위즈수조 동시) — tank 평탄까지 측정 ──
        #    이 동안 ref(5L)는 동시 폭기로 평형에 도달 → [B] ref 측정이 빨라짐(co-aeration).
        send(ser, 'airoff', stop_pattern='OFF')
        send(ser, 'ron', stop_pattern='참조ON')
        print("\n[폭기] ON (측정챔버 tank + 5L 위즈수조 동시) — tank 평탄까지 측정")
        tank_ph, tank_n, tank_flat = measure_until_flat(ser, 'tank')
        if tank_ph is None:
            raise RuntimeError("tank 측정 실패(응답 없음)")

        # ── 전이(빠른 측정 우선): tank 를 홀딩에 *임시 파킹* → 즉시 ref 이송 ──
        #    ★tank 완전배출(본수조) 대신 홀딩으로 빠르게 비우고 바로 ref 채움 → ref 측정 조기 시작.
        #      (파킹된 tank 수는 ref 측정 후 마무리 배출.)
        send(ser, 'airoff', stop_pattern='OFF')          # ★액체 이동 전 airoff (기포기 off)
        send(ser, 'ton', stop_pattern='수조ON')
        print("\n[tank] 측정챔버 → 홀딩 임시 파킹 (m2 역방향)")
        send_motor(ser, 2, 'm2b:68')
        print("\n[ref] 참조수 5L → 측정 챔버 (m4) — 동시폭기로 이미 평형 근처")
        send_motor(ser, 4, 'm4f:60')

        # ── [B] 폭기 ON (측정챔버 ref + 5L 위즈수조 동시) — ref 평탄까지 측정 ──
        #    ref 는 5L서 내내 co-aeration 됐으므로 평형 근처서 시작 → 빨리 끝남.
        send(ser, 'airoff', stop_pattern='OFF')
        send(ser, 'ron', stop_pattern='참조ON')
        print("\n[폭기] ON (측정챔버 ref + 5L 위즈수조 동시) — ref 평탄까지 측정")
        ref_ph, ref_n, ref_flat = measure_until_flat(ser, 'ref')
        if ref_ph is None:
            raise RuntimeError("ref 측정 실패(응답 없음)")
        send(ser, 'airoff', stop_pattern='OFF')   # ★측정 종료 즉시 OFF → 이후 calkh·정리 이동은 전부 에어 OFF(액체 이동 규칙)

        # ── KH 계산 (펌웨어 저장 refPH/tankPH = 각 phase 마지막 평탄값) ──
        print("\n[KH] 계산")
        kh_lines = send(ser, 'calkh', stop_pattern='===========', timeout=10)

        # ── 정상 정리: ref 회수(측정챔버→5L) → 파킹 tank 마무리(홀딩→본수조) → KCl 소크 ──
        send(ser, 'ton', stop_pattern='수조ON')
        print("\n[정리] 참조수 측정챔버 → 5L 위즈수조 회수 (m4 역방향)")
        send_motor(ser, 4, 'm4b:68')
        print("\n[정리] 파킹된 수조수 홀딩 → 본수조 마무리 배출 (m1 역방향)")
        send_motor(ser, 1, 'm1b:82')
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
        plateau_ok = bool(tank_flat and ref_flat)
        # ★평탄 미도달이면 측정 경도(tank_kh)에 음수(-) 표식 — 값 크기는 유지(서버가 부호로 인지).
        #   (전부 0인 '에러 표식'과는 구분됨 → 에러 래치를 트리거하지 않음)
        if (tank_kh is not None) and (not plateau_ok):
            tank_kh = -abs(tank_kh)
        print("\n" + "=" * 40)
        print("측정 결과 (V4)")
        print("=" * 40)
        if ref_ph_r  is not None: print(f"  참조수 pH : {ref_ph_r:.3f}")
        if tank_ph_r is not None: print(f"  수조수 pH : {tank_ph_r:.3f}")
        if ref_kh    is not None: print(f"  참조 dKH  : {ref_kh:.3f} dKH")
        if tank_kh   is not None:
            print(f"  수조 dKH  : {tank_kh:.3f} dKH" + ("  ← 음수=평탄 미도달 표식" if not plateau_ok else ""))
        if temp      is not None: print(f"  온도      : {temp:.1f} C")
        print(f"  평탄도달 : tank {tank_n}회 {'O' if tank_flat else 'X(상한)'} / "
              f"ref {ref_n}회 {'O' if ref_flat else 'X(상한)'}")
        if not plateau_ok:
            print("  ※ 평탄 미도달 — 측정 경도(tank_kh) 음수(-) 표식. 값은 유효, 서버가 부호로 인지.")
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

    # ★에러 래치: 마지막 줄이 에러 표식(전부 0)이면 측정하지 않고 에러 표식만 재기록.
    #   (수동으로 마지막 에러 줄을 지우기 전까지 매 실행 반복 — 오류 상태 무인 반복측정 방지)
    if last_dat_is_error():
        print("[중단] dkh.dat 마지막 줄이 에러 표식(전부 0) — 측정 생략, 에러 표식 재기록.")
        print("       수동으로 마지막 에러 줄을 제거하기 전까지 매 실행 반복됩니다.")
        log_kh(hour, 0.0, 0.0, 0.0, 0.0, 0.0)
        return

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
