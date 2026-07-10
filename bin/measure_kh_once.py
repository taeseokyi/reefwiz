#!/usr/bin/env python3
"""
AquaWiz KH 1회 측정 (V4 — 평형(plateau) 추종 측정)
HC-06 블루투스 시리얼로 한 번만 측정하고 dkh.dat 에 기록 후 종료.

두 가지 모드:
  • (기본) calkh 모드 — 인자 없이 실행. ref dKH(EEPROM 앵커)로 tank dKH 측정,
    결과를 dkh.dat 에 기록(아래 형식). Windows 작업 스케줄러 정시 실행용.
  • calref 모드 — `--setref <수조실측dKH>` 지정. tank·ref pH 측정은 동일하나
    calkh 대신 calref 를 호출해 *ref dKH 를 역산*한다. 절차:
      1) 명령라인 수조 실측 dKH 를 setref 로 펌웨어에 기록(측정 전 즉시 검증).
      2) tank·ref pH 측정(calkh 모드와 완전히 동일).
      3) calref 호출 → 펌웨어가 newRefDKH = 수조dKH · 10^(-(tankPH-refPH)) 계산 후
         refDKH 에 대입 + EEPROM 저장(펌웨어 calcRefDKH 가 자동 저장 → 추가 setref 불요).
    ★측정 결과도 dkh.dat·reefCore 에 기록한다(수조 dKH = --setref 로 입력한 실측값,
    ref_kh 칼럼 = 역산된 새 ref dKH). 평탄 미도달이면 calkh 와 동일하게 수조 dKH 에
    음수(-) 표식을 붙여(값 크기는 입력값 유지) 값만으로 미평탄을 알 수 있게 한다
    (음수=미평탄·0=에러도 reefCore 에 그대로 발행 — 부호/0 이 상태 전달). 알려진 수조 dKH 로 ref 를 교정할 때 쓴다.
    ★★calref 의 저장·발행·에러 처리는 calkh 와 **완전 동일**(성공=값 기록·발행, 실패/일부 None=0.0 에러 표식
    기록·발행, 에러 래치도 공통). 모드 차이는 콘솔 안내 문구(ref dKH 저장 여부)뿐 — 동작 일치로 상황 파악을 직관화.

V4 (2026-06-17): "측정 중 폭기 + 진짜 평형(평탄)까지" 측정.
  - tank·ref 를 폭기 유지(ron)한 채 반복 측정, **최근 FLAT_SPAN_N개 max−min ≤ FLAT_SPAN_MPH(흔들림)
    AND 최근 FLAT_NET_N개 양끝차 ≤ FLAT_NET_MPH(드리프트)** 면 평형 도달로 보고 종료. ★B1: net 룩백을
    span보다 길게(8 vs 4) 둬 느린 단조 꼬리 조기 latch 차단(정수 비교라 float 지터 없음).
    → 격차(시작 CO₂)에 자동 적응(평탄할 때까지 측정).
    ★MIN_N(2026-07-03): tank 는 유효샘플 FLAT_MIN_N_TANK(20)회 전 잠금 금지 — 저진폭(무딘 S커브)
    날은 초기 lag/측정계 과도가 net8 창 감지한계 밑이라 false lock(7/3 05:00 7.509). ref 미적용.
    차동에서 ref·tank 가 같은 평형(실내공기 pCO₂)에 도달 → 방 CO₂ 상쇄.
  - ★tank 먼저 + 5L 위즈수조 동시 폭기(2026-06-20): m1=본수조↔홀딩, m2=홀딩↔측정챔버,
    m4=참조수(5L)↔측정챔버, m3=KCl↔측정챔버.
    [A] tank(본수조수)를 측정챔버서 폭기+평탄까지 측정. 이 동안 5L 위즈수조가 *동시 폭기*돼
        ref 가 5L서 co-aeration → 평형 도달.
    [B] tank 를 홀딩에 임시 파킹(빠른 비움) → 즉시 ref(5L) 이송 → 폭기+평탄까지 측정
        (ref 가 동시폭기로 이미 평형 근처라 빨리 끝남). 파킹 tank 는 ref 측정 후 마무리 배출.
  - ★무한 대기 방지: phase 별 PHASE_MAX_SECS·MEAS_MAX·연속실패 FAIL_MAX 상한,
    시리얼 read 타임아웃. 평탄 미도달 시 마지막값+경고로 종료(행 안 함).
  - ★HC-06 블루투스 RF 순단 대응(사용자 설계 2026-06-23): 측정이 BT SPP(COM9)로 돌아 RF 링크가
    간헐적으로 끊긴다(장시간 다운 없음; 펌웨어는 살아있고 드롭은 대개 '보낼 때 이미 끊겨 있음').
      (예방) keepalive — HC-06 는 ~20초 무통신 시 링크가 끊기는 경향(조사: Arduino/Reddit 사례).
             측정 간 30초·모터 60~85초 유휴 동안 빈 줄을 주기 송신해 링크를 깨워 둔다.
      (send 정책) ①모든 명령 송신 전 연결확인(ensure_link=status 핑) ②끊겼으면 재연결(close→open)
             ③보낸 뒤 연결문제(송신/수신 예외·응답 미수신)면 재연결 후 재시도 ④재시도 SEND_RETRY_MAX 회
             ⑤모터는 재시도 시 먼저 정지(mNs) 후 재송신 → 미전달이든 진행중이든 중복 구동 방지.
    calkh·calref(--setref) 모두 같은 send()/measure_until_flat 를 타므로 동일 적용.
  - ★규칙: 액체 이동(mXf/mXb) 직전 airoff. ron=에어(D12)·ton=PWM(D13) 독립, airoff=둘 다 OFF.
  - 오류/비정상 종료 시 비상 정리(_safe_cleanup): 에어 OFF + 챔버 배출/회수 + KCl 소크 복원.
    ★전제조건 우선(2026-07-10): airoff·ton 실패 시 CLEANUP_RECOVERY_SECS 까지 링크 회복을
    기다려 전제조건부터 재시도, 끝내 실패하면 모터 생략(밀폐계라 이송 무효 — 7/9 21시 실증).
    ★진행 지점 판단 선행(2026-07-10, 사용자 원칙): 액체 위치(_liquid)를 추적해 상태별
    레시피로 정리(챔버=KCl→조치 불필요 / tank수→배출 / ref수→5L 회수). 위치 불명(UNKNOWN,
    이송 도중 중단)이면 자동 정리 대신 현 상태 동결+경고 — 잘못된 이송(KCl 을 본수조로 배출,
    참조수 소실 등)보다 동결이 낫다.
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

★링크 사망 보강(2026-07-03, 7/2 21시 노트북 이동 BT 사망 사후):
  ① write_timeout=5 — 반열림 BT COM 쓰기 무한 블로킹(좀비)을 예외로 전환.
  ② 평탄 phase 중 링크 사망 → FAIL_MAX 로 포기하지 않고 phase 마감까지
     LINK_RETRY_INTERVAL 간격 재접속 대기(폭기 유지=평형 보존·모터 정지=무해), 복구 시 재개.
  ③ 링크 사망으로 calkh/정리 불능이어도 phase 데이터가 온전하면 호스트가 동일 차동식으로
     dKH 를 계산해 음수 표식으로 기록 — 순수 링크 사망이 0.0 래치로 다음 측정을 막지 않게.
     (KCl 소크 실패 등 링크 생존 상태의 장비 문제는 기존대로 0.0 래치)
★링크 사망 보강 2차(2026-07-10, 7/9 21시 무선 콤보칩 장애 사후):
  ④ 비상정리 전제조건 우선 — airoff·ton 실패 시 링크 회복을 기다려 전제조건부터 재시도,
     끝내 실패하면 모터 생략(밀폐계라 airoff·ton 없인 이송 무효 — 모터 '완료'≠이송 성공).
  ⑤ reconnect 시 출력버퍼 purge — 사망 중 고인 미송신 바이트가 복구 직후 지연 배달·실행되는
     것 방지(7/9: 21:07 의 'tank' 가 23:10 에 실행됨을 실증. 하부 스택 버퍼는 못 비우므로 베스트에포트).
"""

import serial
import time
import re
import sys
import os
import argparse
from datetime import datetime

PORT     = 'COM9'
BAUD     = 9600

# ── 평형(평탄) 판정 — measure_until_flat (정수 milli-pH 윈도우; float 비교 지터 회피) ──
FLAT_SPAN_N    = 4       # 흔들림(span) 판정 윈도우 — 최근 N개
FLAT_SPAN_MPH  = 2       # 최근 FLAT_SPAN_N개 max−min ≤ 2 mpH (흔들림 폭). 정수 비교라 float 지터 없음
FLAT_NET_N     = 8       # ★B1(2026-06-20): 드리프트(net) 판정 룩백 — span보다 긴 창(≈5분)
FLAT_NET_MPH   = 1       # 최근 FLAT_NET_N개 양끝 |win[-1]-win[0]| ≤ 1 mpH. 짧은 4점은 느린 단조 꼬리서 net=1 위장(20:29 tank 10회 조기 latch) → 8점으로 차단
FLAT_MIN_N_TANK = 20     # ★MIN_N(2026-07-03): tank 는 유효샘플 20회(≈13분) 전 평탄 잠금 금지.
                         #   평형 접근은 지수라 기울기∝진폭 — 진폭 작은 날(새벽, 수조수 pCO₂≈헤드스페이스)은
                         #   S커브가 무뎌져 초기 lag(측정계 과도 포함)가 net8 창 감지한계 밑 → false lock
                         #   (7/3 05:00 7.509, 참값 ~7.24). 관측 lag ~4-6샘플의 3배 여유.
                         #   ref 는 능동폭기 앵커(진폭 원래 0, 8~15회 잠금이 정상)라 미적용 — 여기에
                         #   '하강 관찰 필수' 류 조건을 걸면 ref 가 PHASE_MAX 까지 교착하므로 금지.
MEAS_INTERVAL  = 30      # 측정 간 간격(초) — 폭기 지속
# ── ★무한 대기 방지 상한 ──
PHASE_MAX_SECS = 7200    # phase(tank/ref)별 최대 측정 시간(초)=2h. 2-phase라 총 4h(측정 갭 8h의 절반). 초과 시 마지막값+경고
MEAS_MAX       = 240     # phase별 최대 측정 횟수(백스톱)=7200s/30s, PHASE_MAX_SECS와 정합
FAIL_MAX       = 5       # 연속 측정 파싱 실패 허용 횟수 → 초과 시 phase 실패
# ── ★HC-06 블루투스 RF 순단 대응(사용자 설계 2026-06-23): 측정이 BT SPP(COM9)로 돌아 RF 링크가
#    간헐적으로 끊긴다(장시간 다운은 없음; 펌웨어는 살아있고 드롭은 대개 '보낼 때 이미 끊겨 있음').
#    send() 정책: ①모든 명령 송신 전 연결확인(ensure_link) ②끊겼으면 재연결 ③보낸 뒤 연결문제면
#    재연결 후 재시도 ④재시도 SEND_RETRY_MAX 회까지 ⑤모터는 재시도 시 정지(mNs) 후 재송신.
RECONNECT_TRIES   = 5            # reconnect() 1회당 close→open 재연결 시도 횟수(진짜 순단은 1~2회면 붙음)
RECONNECT_BACKOFF = (1, 1, 2, 2, 3)      # 시도별 대기(초). 범위 넘으면 마지막값 유지
SEND_RETRY_MAX    = 3            # send() 1회당 재시도 횟수(꼭 3 아니어도 됨 — 튜닝 가능)
# ★keepalive(2026-06-23, 조사 결과): HC-06 SPP 는 ~20초 무통신 시 링크가 끊기는 경향이 있다
#   (Arduino/Reddit 사례: "유휴=드롭, 주기적 송신=안정"). 측정 간격 30초·모터 동작 60~85초가 그 임계를
#   넘으므로, 유휴 동안 빈 줄('\r\n', 펌웨어가 line 544 에서 조용히 무시)을 주기 송신해 링크를 *예방적*으로
#   깨운다 → 애초에 드롭을 줄이고, 그래도 끊기면 send() 의 재연결-재송신이 복구.
KEEPALIVE_SECS    = 12           # 유휴 keepalive 간격(초) — HC-06 ~20초 임계 아래
MEAS_READ_TIMEOUT = 20           # 측정 1회 '[OK]' 대기 상한(초). 펌웨어 측정 ~8초이므로 20초면 RF 드롭 판정
LINK_PING_TIMEOUT = 3            # ensure_link/reconnect 의 status 핑 응답 대기 상한(초)
# ★링크 사망 끈질긴 대기(2026-07-03, 7/2 21시 노트북 이동 BT 사망 사후): 평탄 phase 는 폭기 유지
#   중이라 평형이 보존되고 모터도 정지 상태 → 기다려도 물리적으로 무해. FAIL_MAX 로 몇 분 만에
#   포기하지 않고 phase 마감(PHASE_MAX_SECS)까지 아래 간격으로 재접속을 시도, 복구되면 측정 재개.
LINK_RETRY_INTERVAL = 60         # 링크 사망 시 재접속 시도 간격(초)
# ★비상정리 전제조건 우선(2026-07-10, 7/9 21시 실증): 밀폐계라 airoff·ton 없이는 모터가 돌아도
#   액체가 안 움직인다('완료' 응답≠이송 성공 — 라인 건조로 확인). 전제조건 실패(링크 사망) 시
#   아래 상한까지 재접속을 기다려 전제조건부터 재시도한다. 폭기 유지 상태라 기다려도 무해.
CLEANUP_RECOVERY_SECS = 1800     # 비상정리 전제조건(airoff·ton) 실패 시 링크 회복 대기 상한(초)
# ★진행 상태 추적(2026-07-10, 사용자 원칙 "복원은 진행 지점 판단이 선행"): 비상정리가 올바른
#   레시피를 고르려면 액체가 어디 있는지 알아야 한다. 액체 이동(_move_liquid) 성공 시점마다
#   갱신하고, 이동 도중 실패(전달/이송량 불명)면 UNKNOWN — 이후 성공해도 확신은 복구되지
#   않으므로(sticky) 비상정리는 모터를 돌리지 않고 현 상태를 동결한다.
_liquid = {'chamber': 'KCL', 'holding': 'EMPTY'}   # chamber: KCL|EMPTY|TANK|REF|UNKNOWN, holding: EMPTY|TANK|UNKNOWN

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


def _reefcore_creds():
    """reefCore 자격증명을 (user, pw, mac, tls_verify) 으로 반환. 없으면 (None, None, None, False).

    우선순위: 환경변수 → 설정 파일(스케줄 작업이 env 를 못 볼 때 대비).
    설정 파일 형식(한 줄에 KEY=VALUE): user=..., pass=..., mac=...(선택), tls_verify=0/1(선택).
    값을 저장소에 커밋하지 않는다 — 설정 파일은 .gitignore 처리.
    tls_verify: 브로커 인증서 검증 여부. 현재 8883 인증서가 만료 상태라 기본 0(검증 끔).
                운영자가 인증서 갱신하면 conf 에 tls_verify=1 만 추가하면 검증 ON(재배포 불요).
    """
    def truthy(v): return str(v).strip().lower() in ('1', 'true', 'yes', 'on')
    user = os.environ.get('REEFCORE_USER')
    pw   = os.environ.get('REEFCORE_PASS')
    if user and pw:
        return (user, pw, os.environ.get('REEFCORE_MAC', 'b0cbd88ec880'),
                truthy(os.environ.get('REEFCORE_TLS_VERIFY', '0')))
    here = os.path.dirname(os.path.abspath(__file__))
    for p in [os.environ.get('REEFCORE_CONF'), r'C:\dkh\reefcore.conf',
              '/mnt/c/dkh/reefcore.conf', os.path.join(here, 'reefcore.conf')]:
        try:
            if p and os.path.exists(p):
                conf = {}
                for ln in open(p, encoding='utf-8'):
                    ln = ln.strip()
                    if ln and not ln.startswith('#') and '=' in ln:
                        k, v = ln.split('=', 1)
                        conf[k.strip().lower()] = v.strip()
                u  = conf.get('user') or conf.get('reefcore_user')
                pw2 = conf.get('pass') or conf.get('password') or conf.get('reefcore_pass')
                if u and pw2:
                    return (u, pw2, conf.get('mac', 'b0cbd88ec880'),
                            truthy(conf.get('tls_verify', '0')))
        except Exception:
            pass
    return None, None, None, False


def _publishable(tank_kh):
    """reefCore 발행 정책: 값 자체가 상태를 전달하므로 None(값 없음)만 제외하고 전부 발행한다.
    양수=정상 측정, 음수=미평탄 표식, 0=에러 표식 — 부호·0 이 reefCore 에도 그대로 실린다."""
    return tank_kh is not None


def publish_to_reefcore(tank_kh, temp):
    """측정 직후 reefCore(reefChecker)에 dKH 를 best-effort 로 발행한다.

    체커의 '최근 측정값' MQTT 토픽에 `dKH: <값> dKH | <온도>°C @ <시각>` 을 쏘면
    백엔드가 파싱해 해당 체커의 dKH 측정 레코드를 만든다. 상세: docs/reefcore-integration.md
    - 자격증명(env REEFCORE_USER/PASS 또는 설정파일)이 없으면 조용히 비활성(opt-in).
    - paho 미설치·연결 실패 등 어떤 오류도 측정을 중단시키지 않는다(best-effort).
    - 값 None(값 없음)만 발행 제외 — 음수(미평탄)·0(에러)은 부호/0 자체가 상태라 그대로 발행한다.
    """
    if not _publishable(tank_kh):
        return                                    # None(값 없음)만 제외 — 음수(미평탄)·0(에러)도 발행
    user, pw, mac, tls_verify = _reefcore_creds()
    if not user or not pw:
        return                                    # opt-in: 자격 미설정이면 비활성
    try:
        import ssl as _ssl
        import paho.mqtt.client as _mqtt
        topic = f"reefcore-checker-{mac[-6:]}/sensor/{'_' * 16}/state"   # '최근 측정값'
        summary = f"dKH: {tank_kh:.2f} dKH | {temp:.1f}°C @ {datetime.now():%Y-%m-%d %H:%M}"
        try:
            cl = _mqtt.Client(_mqtt.CallbackAPIVersion.VERSION2,
                              client_id='reefwiz-bridge', clean_session=True)
        except AttributeError:
            cl = _mqtt.Client(client_id='reefwiz-bridge', clean_session=True)  # paho-mqtt 1.x
        cl.username_pw_set(user, pw)
        if tls_verify:
            cl.tls_set(cert_reqs=_ssl.CERT_REQUIRED)   # 인증서 검증 ON (브로커 인증서 갱신 후)
        else:
            cl.tls_set(cert_reqs=_ssl.CERT_NONE)       # 브로커 인증서 만료 상태 → 검증 생략
            cl.tls_insecure_set(True)
        cl.connect('reef.anih.net', 8883, keepalive=30)
        cl.loop_start()
        info = cl.publish(topic, summary, qos=1, retain=False)
        info.wait_for_publish(timeout=10)
        cl.loop_stop(); cl.disconnect()
        print(f"[reefCore] 발행: {summary}" if info.rc == 0
              else f"[reefCore] 발행 실패 rc={info.rc}")
    except Exception as e:
        print(f"[reefCore] 발행 건너뜀(측정엔 영향 없음): {e}")


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

def read_until(ser, stop_pattern, timeout=60.0, keepalive=False):
    """stop_pattern 수신까지 읽는다(라인 출력). ★keepalive=True 면 긴 무통신 구간(모터 동작 등)에서
    KEEPALIVE_SECS 마다 빈 줄을 보내 HC-06 SPP 링크 유휴 드롭을 예방(펌웨어는 빈 줄 무시=무응답)."""
    lines = []
    deadline = time.time() + timeout
    next_ka = time.time() + KEEPALIVE_SECS
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='replace').strip()
            if line:
                print(f"    {line}")
                lines.append(line)
                next_ka = time.time() + KEEPALIVE_SECS    # 수신도 활동 → keepalive 미룸
                if stop_pattern in line:
                    return lines
        else:
            time.sleep(0.02)
            if keepalive and time.time() >= next_ka:
                try:
                    ser.write(b'\r\n')                     # 빈 줄 = 펌웨어 무시, 링크만 깨움
                except (serial.SerialException, OSError):
                    pass                                   # 끊겼으면 상위 명령의 재연결이 처리
                next_ka = time.time() + KEEPALIVE_SECS
    print(f"    [TIMEOUT] '{stop_pattern}' 미수신")
    return lines


def keepalive_sleep(ser, secs):
    """측정 간 유휴(secs초)를 KEEPALIVE_SECS 청크로 나눠 자며 매 청크 사이 빈 줄을 보내
    HC-06 SPP 유휴 드롭을 예방한다(펌웨어 무응답). 다음 실제 측정이 살아있는 링크에서 시작."""
    end = time.time() + secs
    while True:
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(KEEPALIVE_SECS, remaining))
        if time.time() < end:                              # 마지막 청크 뒤엔 굳이 안 보냄
            try:
                ser.write(b'\r\n')
            except (serial.SerialException, OSError):
                pass


def reconnect(ser, why):
    """★RF 순단 대응: 포트 close→open 으로 HC-06 재연결 유도 + 부작용 없는 status 핑으로
    펌웨어 응답 확인. 성공 True / RECONNECT_TRIES 모두 실패 시 False.
    (장시간 RF 다운은 없다는 전제 — 보통 1~2시도면 붙음.)"""
    print(f"    [RF] 링크 끊김 — {why} → 재연결 시도")
    for i in range(1, RECONNECT_TRIES + 1):
        time.sleep(RECONNECT_BACKOFF[min(i - 1, len(RECONNECT_BACKOFF) - 1)])
        try:
            if ser.is_open:
                try:
                    ser.reset_output_buffer()  # ★사망 중 고인 미송신 바이트 폐기(7/9: 'tank'가 2h 뒤 지연 배달·실행 실증)
                except Exception:
                    pass
                ser.close()
        except Exception:
            pass
        try:
            ser.open()
            ser.reset_input_buffer()
            try:
                ser.reset_output_buffer()                  # ★재연결 직후에도 비움 — 스테일 명령 배달 방지(베스트에포트)
            except Exception:
                pass
            ser.write(b'status\r\n')                       # 부작용 없는 핑(printStatus)
            lines = read_until(ser, '============', timeout=LINK_PING_TIMEOUT)
            if any('============' in ln for ln in lines):   # 펌웨어가 응답 = 링크 복구
                ser.reset_input_buffer()
                print(f"    [RF] 재연결 성공 (시도 {i}) — 펌웨어 응답 확인, 측정 재개")
                return True
            print(f"    [RF] 재연결 시도 {i}/{RECONNECT_TRIES}: 포트 열림이나 펌웨어 무응답 — 재시도")
        except (serial.SerialException, OSError) as e:
            print(f"    [RF] 재연결 시도 {i}/{RECONNECT_TRIES} 실패: {e}")
    print(f"    ★[RF] 재연결 {RECONNECT_TRIES}회 모두 실패 — 링크 복구 불가")
    return False


def ensure_link(ser):
    """★명령 송신 *직전* 링크 생존 확인(부작용 없는 status 핑, 출력 안 함). 죽었으면 reconnect.
    실측(2026-06-23): 드롭은 대개 '보내려는 순간 이미 끊겨 있음'(통신 중엔 잘 안 끊김)이라,
    죽은 링크에 write 하면 OS가 조용히 버퍼링→응답 타임아웃까지 허비/모터는 미전달인데 '완료 못 받음'으로
    오인. 보내기 전 점검이 그 창을 닫는다(특히 모터=재송신 불가 명령에 필수)."""
    try:
        ser.reset_input_buffer()
        ser.write(b'status\r\n')
        deadline = time.time() + LINK_PING_TIMEOUT
        while time.time() < deadline:
            if ser.in_waiting:
                if '============' in ser.readline().decode('utf-8', errors='replace'):
                    ser.reset_input_buffer()
                    return True
            else:
                time.sleep(0.02)
    except (serial.SerialException, OSError):
        pass
    return reconnect(ser, "송신 전 점검: 링크 무응답")


def _wait_link_recovery(ser, phase_t0):
    """★링크 사망 시 끈질긴 대기(2026-07-03): phase 마감(phase_t0+PHASE_MAX_SECS)까지
    LINK_RETRY_INTERVAL 간격으로 재접속을 시도한다. 폭기 유지 중=평형 보존·모터 정지라
    기다려도 무해(노트북 이동 등 일시 이탈은 복귀 즉시 측정 재개). 복구 True/마감 False."""
    deadline = phase_t0 + PHASE_MAX_SECS
    remain = int(deadline - time.time())
    if remain <= 0:
        return False
    print(f"    [RF] 링크 사망 — phase 마감까지(잔여 {remain}s) {LINK_RETRY_INTERVAL}s 간격 재접속 대기")
    while time.time() < deadline:
        time.sleep(min(LINK_RETRY_INTERVAL, max(1, deadline - time.time())))
        if reconnect(ser, "링크 복구 대기"):
            return True
    return False


def _motor_index(cmd):
    """'m1f:70'/'m2b:68' → 1/2. 모터 구동 명령이 아니면 None."""
    m = re.match(r'm([1-4])[fb]:', cmd)
    return int(m.group(1)) if m else None


def _stop_motor(ser, idx):
    """모터 재시도 *전* 진행 중일 수 있는 모터를 정지(mNs) → 재송신이 중복 구동 안 되게.
    펌웨어 응답 '[M{idx}] 정지'(motorStopNow) 까지 흡수, 없으면 짧게 타임아웃(베스트에포트)."""
    print(f"    [모터정지] m{idx}s (재시도 전 안전 정지)")
    try:
        ser.reset_input_buffer()
        ser.write(f'm{idx}s\r\n'.encode())
        read_until(ser, f'[M{idx}] 정지', timeout=3)
    except (serial.SerialException, OSError):
        pass


def send(ser, cmd, stop_pattern=None, timeout=5.0, allow_reconnect=True, keepalive=False):
    """명령 송신 후 stop_pattern 까지 수신. ★HC-06 RF 순단 대응(사용자 설계 2026-06-23):
      1) 모든 명령은 보내기 전 ensure_link 로 연결 확인(드롭은 대개 '보낼 때 이미 끊김').
      2) 끊겼으면 재연결한다.
      3) 보낸 뒤 연결 문제(송신/수신 예외·응답 미수신)면 재연결 후 재시도.
      4) 재시도는 SEND_RETRY_MAX 회까지.
      5) 모터는 재시도 시 먼저 정지(mNs)하고 다시 명령한다(중복 구동 방지).
    소진 후엔 기존 동작대로 (부분/빈) 결과 반환 → 상위 FAIL_MAX·상한·_motor_ok 가 처리.
    keepalive=True 면 긴 응답 대기(모터) 동안 read_until 이 유휴 keepalive 송신."""
    print(f"\n→ {cmd}")
    motor_idx = _motor_index(cmd)
    lines = []
    for attempt in range(1, SEND_RETRY_MAX + 1):
        if allow_reconnect:
            ensure_link(ser)                       # 1)+2) 보내기 전 연결확인·필요시 재연결
            if attempt > 1 and motor_idx is not None:
                _stop_motor(ser, motor_idx)        # 5) 모터 재시도 전 정지 후 재송신
        try:
            ser.write((cmd + '\r\n').encode())
            if not stop_pattern:
                time.sleep(0.3)
                lines = []
                while ser.in_waiting:
                    line = ser.readline().decode('utf-8', errors='replace').strip()
                    if line:
                        print(f"    {line}")
                        lines.append(line)
                return lines
            lines = read_until(ser, stop_pattern, timeout, keepalive=keepalive)
        except (serial.SerialException, OSError) as e:
            # 송신/수신 중 링크 드롭 → 3)+4) 재시도(다음 루프 ensure_link 가 재연결).
            print(f"    [RF] '{cmd}' 통신 오류: {e}")
            if allow_reconnect and attempt < SEND_RETRY_MAX:
                continue
            raise
        if any(stop_pattern in ln for ln in lines):
            return lines
        # 응답 미수신 → 3)+4) 재연결 후 재시도(다음 루프 ensure_link).
        if allow_reconnect and attempt < SEND_RETRY_MAX:
            print(f"    [RF] '{cmd}' 응답 미수신 → 재연결 후 재시도 ({attempt}/{SEND_RETRY_MAX})")
            continue
        return lines
    return lines


def send_motor(ser, motor_idx, cmd):
    m = re.search(r':(\d+)$', cmd)
    duration = int(m.group(1)) if m else 60
    return send(ser, cmd,
                stop_pattern=f'[모터{motor_idx}] 완료',
                timeout=duration + 15,
                keepalive=True)   # 모터 동작(60~85s) 동안 링크 유휴 드롭 예방
                                  # (송신 전 연결확인·재시도 시 모터정지는 send() 가 일괄 처리)


def _motor_ok(lines, idx):
    """send_motor 결과에 '[모터idx] 완료'가 있으면 True (타임아웃·무응답이면 False)."""
    return any(f'[모터{idx}] 완료' in ln for ln in (lines or []))


def _move_liquid(ser, motor_idx, cmd, chamber_after, holding_after):
    """★send_motor + 진행 상태(_liquid) 갱신(2026-07-10). 송신~완료 사이 예외 또는 '완료'
    미수신이면 이송량 불명 → UNKNOWN 유지. 한번 UNKNOWN 이 되면 이후 이동이 성공해도
    확신이 복구되지 않는다(sticky) → 비상정리가 자동 정리를 포기하고 동결한다."""
    was_known = 'UNKNOWN' not in (_liquid['chamber'], _liquid['holding'])
    _liquid['chamber'] = _liquid['holding'] = 'UNKNOWN'   # 이동 중(실패 시 이대로 남음)
    lines = send_motor(ser, motor_idx, cmd)
    if was_known and _motor_ok(lines, motor_idx):
        _liquid['chamber'], _liquid['holding'] = chamber_after, holding_after
    return lines


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
    ★MIN_N: tank 는 유효샘플 FLAT_MIN_N_TANK회 전 잠금 금지(무딘 S커브 초기 lag false lock 차단).
    펌웨어가 마지막 측정값을 refPH/tankPH 에 보관하므로 최종(평탄) 값이 calkh 에 쓰인다.

    ★무한 대기 방지: 경과 PHASE_MAX_SECS 또는 측정 MEAS_MAX 회 초과 시 마지막값+경고로 종료.
      연속 파싱 실패 FAIL_MAX 회 초과 시 실패(ph=None) 반환.
    반환: (ph, n_reads, flat_ok). ph=None 이면 측정 실패(응답 없음/계속 실패)."""
    label = '수조수' if what == 'tank' else '참조수'
    min_n = FLAT_MIN_N_TANK if what == 'tank' else 0
    win = []          # 최근 FLAT_NET_N개 정수 milli-pH (span은 뒤 FLAT_SPAN_N개로 판정)
    last_ph = None
    fails = 0
    t0 = time.time()
    n = 0
    n_ok = 0          # 유효샘플 수(파싱 실패 제외) — MIN_N 판정용
    while True:
        n += 1
        try:
            lines = send(ser, what, stop_pattern='[OK]', timeout=MEAS_READ_TIMEOUT)
        except (serial.SerialException, OSError):
            lines = []            # 링크 문제 — 아래 링크 사망 판별로 넘긴다
        ph = parse_ph(lines, label)
        if ph is None:
            # ★링크 사망 판별(2026-07-03): 링크가 죽은 거면 FAIL_MAX 로 포기하지 않고
            #   phase 마감까지 끈질기게 재접속 대기(_wait_link_recovery) — 복구되면 측정 재개.
            #   펌웨어가 살아있는데 응답만 이상한 경우에만 fails 로 센다.
            if not ensure_link(ser):
                if _wait_link_recovery(ser, t0):
                    print(f"    [RF] 링크 복구 — {what} 측정 재개")
                    continue
                print(f"    [상한] {what} 링크 복구 실패(phase 마감) — 미평탄, 마지막값 {last_ph} 채택")
                return last_ph, n, False
            fails += 1
            print(f"    [측정실패 {fails}/{FAIL_MAX}] {what}")
            if fails >= FAIL_MAX:
                print(f"    [실패] {what} 연속 {FAIL_MAX}회 응답 이상 — phase 중단")
                return last_ph, n, False
            # 실패는 측정 횟수엔 세되, 윈도우엔 미반영
        else:
            fails = 0
            last_ph = ph
            n_ok += 1
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
                    if n_ok < min_n:
                        print(f"    [평탄보류] {what} {n}회 — 판정조건 충족이나 MIN_N({min_n}) 미달({n_ok}회) — 초기 lag/과도 구간, 계속 관찰")
                    else:
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
        keepalive_sleep(ser, MEAS_INTERVAL)   # 측정 간 30s 유휴 — keepalive 로 RF 링크 유지


# ─────────────────────────────────────────────
# 결과 파싱
# ─────────────────────────────────────────────

def parse_results(kh_lines, calref=False):
    """calkh/calref 출력에서 참조pH/수조pH/ref dKH/tank dKH/온도 파싱.
    반환: (ref_ph, tank_ph, ref_kh, tank_kh, temp) — 파싱 실패 항목은 None.
      • calkh: ref_kh=앵커 refKH, tank_kh=측정 수조KH.
      • calref: ref_kh=새refDKH(역산·저장됨), tank_kh=수조dKH(입력한 실측값).
    펌웨어 라벨이 두 명령에서 다르므로(refKH/수조KH vs 새refDKH/수조dKH) 분기한다.
    """
    if calref:
        patterns = {
            'ref_ph':  r'참조pH:([\d.]+)',
            'tank_ph': r'수조pH:([\d.]+)',
            'ref_kh':  r'새refDKH:([\d.]+)',   # calref 가 역산·저장한 새 ref dKH
            'tank_kh': r'수조dKH:([\d.]+)',     # setref 로 입력한 수조 실측 dKH
            'temp':    r'온도:([\d.]+)',
        }
    else:
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

def _cleanup_precond(ser):
    """비상정리 전제조건 airoff+ton 송신 — 둘 다 응답 확인돼야 True.
    ★"액체 이동 직전 airoff" 규칙(밀폐계, 헤더 참조): 이게 성립하지 않으면 뒤의 모터
    이송은 헛돈다(모터 '완료' 응답이 와도 액체는 안 움직임 — 7/9 21시 라인 건조로 실증)."""
    ok = True
    for cmd, stop in (('airoff', 'OFF'), ('ton', '수조ON')):
        try:
            lines = send(ser, cmd, stop_pattern=stop, timeout=5)
            ok = any(stop in ln for ln in (lines or [])) and ok
        except Exception:
            ok = False
    return ok


def _safe_cleanup(ser):
    """에러/비정상 종료 시 비상 정리. 각 단계 guard(예외/타임아웃 무시).
    KCl 소크가 끝내 실패하면 큰 경고를 남긴다(이 경로에선 main 이 dkh.dat 에 0.0 기록).
    ★전제조건 우선(2026-07-10, 7/9 21시 사후): 예전엔 airoff·ton 실패(링크 사망)를 무시하고
      모터를 돌려 '완료' 응답으로 거짓 성공을 남겼다(실물은 이송 무효=KCl 미복원). 이제
      전제조건이 실패하면 CLEANUP_RECOVERY_SECS 까지 재접속을 기다려 전제조건부터 재시도하고,
      끝내 실패하면 모터를 생략한다(헛도는 이송+거짓 성공 로그 방지, 챔버 상태 동결=프로브는
      수조수에 젖은 채 유지).
    ★진행 지점 판단 선행(2026-07-10, 사용자 원칙): _liquid 로 "어디까지 갔나"를 먼저 판단해
      상태별 레시피로 정리한다. 위치 불명이면 자동 정리보다 동결이 낫다:
        챔버 UNKNOWN → 모터 생략(동결)+★★경고   (이송 도중 중단 — 잘못된 이송 방지)
        챔버 KCL     → 조치 불필요               (시작 전 에러 = 이미 목표 상태=프로브 소크)
        챔버 TANK    → m2b(→홀딩) → m1b(→본수조) → m3f KCl   (7/9 유형)
        챔버 REF     → m4b(→5L 순환회수) → m1b → m3f KCl     (참조수 배출 금지=회수 원칙)
        챔버 EMPTY   → (홀딩에 tank 수 있으면 m1b) → m3f KCl"""
    ch, hd = _liquid['chamber'], _liquid['holding']
    print(f"\n[비상정리] 진행 지점 판단: 챔버={ch} 홀딩={hd}")
    if 'UNKNOWN' in (ch, hd):
        print("★★[비상정리] 액체 위치 불명(이송 도중 중단) — 자동 정리 생략(현 상태 동결). "
              "챔버·홀딩·KCl 수동 확인 필요")
        print("★★[경고] 비상 KCl 소크도 미완료 — 프로브가 KCl 없이 방치됐을 수 있음! 수동 확인 필요")
        return
    if ch == 'KCL':
        # 준비 이전 에러 — 챔버는 직전 런이 남긴 KCl 소크 상태 그대로 = 이미 목표 상태.
        # (직전 런이 동결로 끝났다면 KCl 이 아닐 수 있으나, 그 런이 이미 ★★경고를 남겼다.)
        print("    [비상정리] 챔버=KCl 소크 상태(목표 상태) — 모터 조치 불필요")
        try: send(ser, 'airoff', stop_pattern='OFF', timeout=5)
        except Exception: pass
        return
    print("[비상정리] 에어 OFF + 챔버 배출/회수 + KCl 소크 복원 시도")
    pre_ok = _cleanup_precond(ser)
    if not pre_ok:
        deadline = time.time() + CLEANUP_RECOVERY_SECS
        print(f"    [비상정리] 전제조건(airoff·ton) 실패 — 링크 회복 대기(최대 {CLEANUP_RECOVERY_SECS}s, "
              f"{LINK_RETRY_INTERVAL}s 간격) 후 전제조건부터 재시도")
        while time.time() < deadline:
            time.sleep(min(LINK_RETRY_INTERVAL, max(0.05, deadline - time.time())))
            if reconnect(ser, "비상정리 전제조건 재시도") and _cleanup_precond(ser):
                pre_ok = True
                break
    kcl_ok = False
    if pre_ok:
        # ★호스 스왑 후 측정챔버 배출 경로 = m2(측정챔버→홀딩) → m1(홀딩→본수조).
        #   (m1 단독은 이제 홀딩↔본수조라 측정챔버를 못 비움 → KCl 오버플로 방지 위해 m2 먼저)
        if ch == 'TANK':
            try: _move_liquid(ser, 2, 'm2b:68', 'EMPTY', 'TANK')   # 측정챔버 → 홀딩 (비우기)
            except Exception: pass
        elif ch == 'REF':
            try: _move_liquid(ser, 4, 'm4b:70', 'EMPTY', hd)       # 측정챔버 → 5L 회수
            except Exception: pass
        if _liquid['holding'] == 'TANK':
            try: _move_liquid(ser, 1, 'm1b:82', 'EMPTY', 'EMPTY')  # 홀딩 → 본수조 (배출)
            except Exception: pass
        if (_liquid['chamber'], _liquid['holding']) == ('EMPTY', 'EMPTY'):
            try:
                kcl_lines = _move_liquid(ser, 3, 'm3f:60', 'KCL', 'EMPTY')   # KCl 소크
                kcl_ok = _motor_ok(kcl_lines, 3)
            except Exception: pass
        else:
            print("★★[비상정리] 배출/회수 미완(위치 불명) — KCl 재공급 생략(현 상태 동결)")
        try: send(ser, 'airoff', stop_pattern='OFF', timeout=5)
        except Exception: pass
    else:
        print("★★[비상정리] 전제조건(airoff·ton) 끝내 실패 — 이송 무효라 모터 생략(챔버 상태 동결)")
    if not kcl_ok:
        print("★★[경고] 비상 KCl 소크도 미완료 — 프로브가 KCl 없이 방치됐을 수 있음! 수동 확인 필요")


# ─────────────────────────────────────────────
# 측정 루틴 (V4)
# ─────────────────────────────────────────────

def run_measurement(ser, tank_dkh=None):
    """tank_dkh 가 None 이면 calkh 모드(기본), 값이 있으면 calref 모드.
    calref 모드는 측정 시작 전에 setref:<tank_dkh> 를 펌웨어에 기록·검증한다."""
    calref = tank_dkh is not None
    completed = False
    # ★진행 상태 초기화(2026-07-10): 런 시작 = 직전 런이 남긴 KCl 소크 상태.
    _liquid['chamber'], _liquid['holding'] = 'KCL', 'EMPTY'
    try:
        # ── calref 모드: 수조 실측 dKH 를 setref 로 기록(측정 전 즉시 검증) ──
        #    범위(0.5~30.0) 밖이거나 응답 이상이면 긴 측정 전에 바로 실패시킨다.
        if calref:
            print(f"\n[calref] 수조 실측 dKH 를 setref 로 기록: {tank_dkh:.3f} dKH")
            sr_lines = send(ser, f'setref:{tank_dkh:.3f}', stop_pattern='refDKH', timeout=5)
            if not any('[OK] refDKH' in ln for ln in sr_lines):
                raise RuntimeError(f"setref 실패(범위 0.5~30.0 확인) — 응답: {sr_lines}")

        # ── ★구제 캐시(2026-07-03): 링크 사망으로 calkh 를 못 돌려도 호스트가 같은 차동식으로
        #    dKH 를 계산할 수 있게, 시작 시 status 에서 refKH(EEPROM 앵커)·온도를 확보(calkh 전용).
        refkh_cached = temp_cached = None
        if not calref:
            for ln in send(ser, 'status', stop_pattern='============', timeout=5):
                m = re.search(r'refKH:([\d.]+)', ln)
                if m and refkh_cached is None:
                    refkh_cached = float(m.group(1))
                m = re.search(r'온도:([\d.]+)', ln)
                if m and temp_cached is None:
                    temp_cached = float(m.group(1))
            print(f"\n[구제캐시] refKH={refkh_cached} 온도={temp_cached}C (링크 사망 대비)")

        # ── 준비: KCl 배출 → tank(본수조수) 측정챔버로 이송 (m1→m2) ──
        #    ★배관(2026-06-20): m1=본수조↔홀딩, m2=홀딩↔측정챔버, m4=참조수(5L)↔측정챔버, m3=KCl↔측정챔버.
        #    ★측정 순서 = tank 먼저 → ref 나중. 이유: 5L 위즈수조가 *동시 폭기*돼 ref 가 tank 측정 내내
        #      5L서 co-aeration → ref 차례엔 이미 평형 근처 → ref 측정이 빠름.
        send(ser, 'airoff', stop_pattern='OFF')
        send(ser, 'ton', stop_pattern='수조ON')
        print("\n[준비] KCl 배출 (측정 챔버)")
        _move_liquid(ser, 3, 'm3b:68', 'EMPTY', 'EMPTY')
        print("\n[tank] 본수조수 → 홀딩 (m1)")
        _move_liquid(ser, 1, 'm1f:70', 'EMPTY', 'TANK')
        print("\n[tank] 홀딩 → 측정 챔버 (m2)")
        _move_liquid(ser, 2, 'm2f:60', 'TANK', 'EMPTY')

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
        _move_liquid(ser, 2, 'm2b:68', 'EMPTY', 'TANK')
        print("\n[ref] 참조수 5L → 측정 챔버 (m4) — 동시폭기로 이미 평형 근처")
        _move_liquid(ser, 4, 'm4f:60', 'REF', 'TANK')

        # ── [B] 폭기 ON (측정챔버 ref + 5L 위즈수조 동시) — ref 평탄까지 측정 ──
        #    ref 는 5L서 내내 co-aeration 됐으므로 평형 근처서 시작 → 빨리 끝남.
        send(ser, 'airoff', stop_pattern='OFF')
        send(ser, 'ron', stop_pattern='참조ON')
        print("\n[폭기] ON (측정챔버 ref + 5L 위즈수조 동시) — ref 평탄까지 측정")
        ref_ph, ref_n, ref_flat = measure_until_flat(ser, 'ref')
        if ref_ph is None:
            raise RuntimeError("ref 측정 실패(응답 없음)")
        # ── KH 계산 (펌웨어 저장 refPH/tankPH = 각 phase 마지막 평탄값) ──
        #    calref 모드는 calkh 대신 calref 호출(ref dKH 역산·EEPROM 저장).
        #    ★calkh 모드는 링크 사망을 예외로 죽이지 않는다(2026-07-03) — 아래 호스트 구제로 진행.
        try:
            send(ser, 'airoff', stop_pattern='OFF')   # ★측정 종료 즉시 OFF → 이후 calkh·정리 이동은 전부 에어 OFF(액체 이동 규칙)
            if calref:
                print("\n[calref] ref dKH 역산·저장")
                kh_lines = send(ser, 'calref', stop_pattern='refDKH 저장', timeout=10)
            else:
                print("\n[KH] 계산")
                kh_lines = send(ser, 'calkh', stop_pattern='===========', timeout=10)
        except (serial.SerialException, OSError):
            if calref:
                raise                               # calref 는 유인 작업 — 기존대로 실패 처리
            print("    [RF] 링크 사망 — calkh 불능, 호스트 구제 계산으로 진행")
            kh_lines = []

        # ── ★호스트 구제(2026-07-03): calkh 응답이 없어도 phase 데이터가 온전하면 펌웨어와 동일한
        #    차동식(tankKH=refKH·10^(tankPH−refPH))으로 dKH 를 계산, 음수(미평탄) 표식으로 기록해
        #    0.0 래치(다음 측정까지 생략)를 피한다. 정상 정리는 링크 사망 시 불가능하므로 건너뛰고
        #    finally 의 _safe_cleanup(각 단계 guard)에 맡긴다. (7/2 21시 사례의 수동 계산 자동화)
        if (not calref) and (not any('수조KH:' in ln for ln in kh_lines)) \
                and None not in (refkh_cached, tank_ph, ref_ph):
            tk = refkh_cached * 10.0 ** (tank_ph - ref_ph)
            if 0.0 < tk <= 50.0:                    # 펌웨어 calcAndSaveKH 와 동일 범위 가드
                temp_s = temp_cached if temp_cached is not None else 0.0
                print("\n" + "=" * 40)
                print("측정 결과 (V4 — 호스트 구제)")
                print("=" * 40)
                print(f"  참조수 pH : {ref_ph:.3f}")
                print(f"  수조수 pH : {tank_ph:.3f}")
                print(f"  참조 dKH  : {refkh_cached:.3f} dKH (시작 시 status 캐시)")
                print(f"  수조 dKH  : {-tk:.3f} dKH  ← 음수=구제(calkh 미실행) 표식")
                print(f"  온도      : {temp_s:.1f} C (시작 시 status 캐시)")
                print("  ※ 정상 정리 생략 — finally 비상정리(KCl 소크) 결과 수동 확인 필요")
                print("=" * 40)
                return (ref_ph, tank_ph, refkh_cached, -tk, temp_s)
            print(f"    [WARN] 구제 dKH 이상({tk:.3f}) — 구제 포기(기존 에러 경로)")

        # ── 정상 정리: ref 회수(측정챔버→5L) → 파킹 tank 마무리(홀딩→본수조) → KCl 소크 ──
        #    ★정리 중 링크 사망(calkh 모드)은 예외로 죽이지 않는다(2026-07-03) — 측정 데이터는
        #      이미 온전하므로 음수 표식으로 결과만 살리고, 비상정리는 finally 에 맡긴다.
        link_lost = False
        try:
            send(ser, 'ton', stop_pattern='수조ON')
            print("\n[정리] 참조수 측정챔버 → 5L 위즈수조 회수 (m4 역방향)")
            _move_liquid(ser, 4, 'm4b:70', 'EMPTY', 'TANK')   # 5L↔측정챔버 호스가 길어 역방향 +10(60→70)으로 완전 회수
            print("\n[정리] 파킹된 수조수 홀딩 → 본수조 마무리 배출 (m1 역방향)")
            _move_liquid(ser, 1, 'm1b:82', 'EMPTY', 'EMPTY')
            print("\n[정리] KCl 공급 (프로브 소크)")
            kcl_lines = _move_liquid(ser, 3, 'm3f:60', 'KCL', 'EMPTY')
            send(ser, 'airoff', stop_pattern='OFF')
            if not _motor_ok(kcl_lines, 3):
                # KCl 소크가 조용히 실패(타임아웃/무응답)하면 측정값이 멀쩡해도 에러로 본다
                # → finally 의 _safe_cleanup 이 한 번 더 KCl 시도, main 은 0.0 기록.
                #   (★링크 생존 상태의 실패 = 장비 문제 → 0.0 래치 유지가 맞다)
                raise RuntimeError("KCl 소크(m3f) 미완료 — 프로브 소크 실패 → 에러(0.0) 기록")
            completed = True
        except (serial.SerialException, OSError):
            if calref:
                raise
            link_lost = True
            print("★★[RF] 링크 사망 — 정상 정리 미완료(비상정리는 finally 재시도, KCl 소크 수동 확인 필요). "
                  "측정 데이터는 온전 → 음수 표식으로 기록")

        # ── 파싱·출력 ──
        ref_ph_r, tank_ph_r, ref_kh, tank_kh, temp = parse_results(kh_lines, calref=calref)
        plateau_ok = bool(tank_flat and ref_flat) and not link_lost

        if calref:
            # ── calref 모드: ref dKH 역산·저장 + (수조 dKH = --setref 입력 실측값으로) 결과 기록 ──
            if ref_kh is None:
                raise RuntimeError("calref 실패 — 새 refDKH 파싱 실패(범위 0.5~30.0 또는 응답 이상)")
            # ★수조 dKH 는 펌웨어 echo(수조dKH) 대신 --setref 로 입력한 실측값(tank_dkh)을 정본으로 쓴다.
            #   평탄 미도달이면 calkh 와 동일하게 음수(-) 표식(값 크기=입력값 유지) → dkh.dat·reefCore 둘 다
            #   음수로 발행돼 값만으로 미평탄 식별. 에러 표식(전부 0)과는 구분돼 래치 안 걸림.
            tank_kh = tank_dkh if plateau_ok else -abs(tank_dkh)
            print("\n" + "=" * 40)
            print("ref dKH 교정 결과 (calref)")
            print("=" * 40)
            if ref_ph_r  is not None: print(f"  참조수 pH       : {ref_ph_r:.3f}")
            if tank_ph_r is not None: print(f"  수조수 pH       : {tank_ph_r:.3f}")
            print(f"  입력 수조 dKH   : {tank_dkh:.3f} dKH")
            print(f"  ★새 ref dKH    : {ref_kh:.3f} dKH  (EEPROM 저장 완료)")
            if temp      is not None: print(f"  온도            : {temp:.1f} C")
            print(f"  평탄도달        : tank {tank_n}회 {'O' if tank_flat else 'X(상한)'} / "
                  f"ref {ref_n}회 {'O' if ref_flat else 'X(상한)'}")
            if not plateau_ok:
                print("  ※ 평탄 미도달(상한) — 수조 dKH 에 음수(-) 표식(값=입력값). "
                      "ref 교정값 정확도 저하 가능, 평탄도달 후 재교정 권장.")
            print("=" * 40)
            return (ref_ph_r, tank_ph_r, ref_kh, tank_kh, temp)

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
    parser = argparse.ArgumentParser(
        description="AquaWiz KH 1회 측정 V4. 인자 없으면 calkh(수조 dKH 측정→dkh.dat 기록), "
                    "--setref 지정 시 calref(ref dKH 역산·EEPROM 저장).")
    parser.add_argument('port', nargs='?', default=PORT,
                        help=f'시리얼 포트(기본 {PORT})')
    parser.add_argument('--setref', type=float, default=None, dest='tank_dkh', metavar='수조dKH',
                        help='수조 실측 dKH. 지정 시 calref 모드 — 이 값을 setref 로 기록하고 '
                             'calref 로 ref dKH 를 역산·저장한다. 측정 결과도 dkh.dat·reefCore 에 '
                             '기록하며, 이때 수조 dKH 는 이 입력값을 쓴다. 미지정 시 calkh 모드.')
    a = parser.parse_args()
    port = a.port
    tank_dkh = a.tank_dkh
    calref = tank_dkh is not None

    if calref and not (0.5 <= tank_dkh <= 30.0):
        print(f"[ERR] --setref 값 {tank_dkh} 는 펌웨어 허용 범위(0.5~30.0) 밖입니다. 중단.")
        return

    now  = datetime.now()
    hour = now.hour

    mode = f"calref (ref dKH 역산, 수조 실측={tank_dkh:.3f} dKH)" if calref else "calkh (수조 dKH 측정)"
    print(f"AquaWiz KH 1회 측정 V4 — {port} @ {BAUD}baud  [{mode}]")
    if not calref:
        print(f"기록 파일: {DAT_FILE}")
    print(f"측정 시작: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # ★에러 래치(calkh·calref 공통): 마지막 줄이 에러 표식(전부 0)이면 측정하지 않고 에러 표식만 재기록·발행.
    #   (수동으로 마지막 에러 줄을 지우기 전까지 매 실행 반복 — 오류 상태 무인 반복측정 방지)
    #   ★calref 도 calkh 와 동일하게 래치 적용(에러 처리 완전 일치 → 상황 파악 직관화).
    if last_dat_is_error():
        print("[중단] dkh.dat 마지막 줄이 에러 표식(전부 0) — 측정 생략, 에러 표식 재기록.")
        print("       수동으로 마지막 에러 줄을 제거하기 전까지 매 실행 반복됩니다.")
        log_kh(hour, 0.0, 0.0, 0.0, 0.0, 0.0)
        publish_to_reefcore(0.0, 0.0)               # 0=에러도 reefCore 에 발행(상태 전달)
        return

    result = None
    try:
        # ★write_timeout=5 (2026-07-03): 반열림 BT COM(포트는 열리나 RF 사망)에 쓰면 Windows 가
        #   무한 블로킹 → 좀비 프로세스(7/2 21시 사례: keepalive write 에 갇혀 5h 뒤 스케줄러 강제종료,
        #   에러 기록·정리 전부 미실행). SerialTimeoutException ⊂ SerialException 이라 기존 except 가 처리.
        with serial.Serial(port, BAUD, timeout=1, write_timeout=5) as ser:
            time.sleep(2)
            ser.reset_input_buffer()
            result = run_measurement(ser, tank_dkh)
    except serial.SerialException as e:
        print(f"[ERR] 시리얼 오류: {e}")
    except Exception as e:
        print(f"[ERR] 예외 발생: {e}")

    # calref 전용 안내(EEPROM 저장 여부) — 데이터 기록·발행은 아래서 calkh 와 완전 동일하게 처리.
    if calref:
        if result and result[2] is not None:
            print(f"\n[완료] ref dKH 가 {result[2]:.3f} dKH 로 EEPROM 에 저장되었습니다.")
        else:
            print("\n[실패] ref dKH 교정이 완료되지 않았습니다(위 로그 확인).")

    # ★결과 저장·발행 — calkh·calref 완전 일치:
    #   완전한 결과면 측정/입력값 기록·발행(음수=미평탄도 발행), 아니면 0.0 에러 표식 기록·발행.
    #   (calref: result=(ref_ph, tank_ph, ref_kh=새 refDKH, tank_kh=입력값(미평탄이면 음수), temp))
    if result and all(v is not None for v in result):
        log_kh(hour, *result)
        publish_to_reefcore(result[3], result[4])   # tank_kh(양수/음수=미평탄), temp → reefCore(best-effort)
    else:
        log_kh(hour, 0.0, 0.0, 0.0, 0.0, 0.0)
        publish_to_reefcore(0.0, 0.0)               # 0=에러도 reefCore 에 발행(상태 전달)


if __name__ == '__main__':
    setup_logging()
    try:
        main()
    except KeyboardInterrupt:
        print("\n[중단] 사용자 인터럽트")
        sys.exit(0)
