# -*- coding: utf-8 -*-
r"""
bt_health.py — HC-06 블루투스 SPP 링크 건강 모니터 (COM9)

배경: 측정(measure_kh_once.py)이 HC-06 블루투스 SPP(COM9)로 돈다. 펌웨어는
살아있는데 RF 링크가 간헐적으로 끊겨 측정이 '[START] 후 침묵'으로 실패한다.
이 스크립트는 부작용 없는 'status' 명령을 주기적으로 보내 왕복응답(RTT)과
드롭을 타임스탬프와 함께 기록 → 드롭 빈도/시간대/패턴을 본다.

★단일 연결 제약: HC-06 SPP는 동시 1연결만 가능. 예약 측정(05/09/13/21시)과
  겹치면 충돌하니 모니터는 측정 사이 유휴 구간에서만 돌릴 것.

사용 (Windows 파이썬, cp949 회피 위해 -X utf8):
  C:\dkh\python313\python.exe -X utf8 bt_health.py            # 측정기 5초마다 연속 핑+로깅
  C:\dkh\python313\python.exe -X utf8 bt_health.py --once     # 측정기 1회 확인
  C:\dkh\python313\python.exe -X utf8 bt_health.py --doser    # 도저 1회 확인(read-only ls, 도징 없음)
  C:\dkh\python313\python.exe -X utf8 bt_health.py --both     # 측정기+도저 둘 다 1회 확인
  C:\dkh\python313\python.exe -X utf8 bt_health.py --interval 2 --port COM9
"""
import sys, time, re, argparse, datetime
import serial

from bt_config import get_port

PORT     = get_port('measure')   # BT 포트는 bt_config.json 단일 설정에서 로드(포트 바뀌면 설정만 수정). --port로 오버라이드 가능
DOSER_PORT = get_port('doser')   # 도저 포트(--doser/--both). --doser-port로 오버라이드 가능
BAUD     = 9600
PING_CMD = 'status'          # 부작용 없음: 상태만 출력, 액추에이터/샘플링 없음
STOP     = '============'    # printStatus() 종단 마커
PING_TO  = 3.0               # 핑 1회 응답 대기 상한(초). 정상 RTT는 보통 수백 ms.
DOSER_CMD  = 'ls'            # 도저 부작용 없음: 설정값만 출력(도징 유발 없음). ★LF only(펌웨어가 '\r' 붙으면 실행 안 함)
DOSER_DONE = '왼쪽 동작(RUN)'  # ls 응답의 마지막 줄(왼쪽 RUN=lrt) = 완료 마커
DOSER_TO   = 3.0             # 도저 ls 응답 대기 상한(초)
LOGFILE  = r'C:\dkh\bt_health.log'


def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log(line, fh):
    msg = f'{now()}  {line}'
    print(msg)
    if fh:
        fh.write(msg + '\n'); fh.flush()


def ping(ser):
    """status 1회 송신 → 종단까지 수신. (성공여부, RTT_ms, 받은줄수, 마지막줄)"""
    ser.reset_input_buffer()
    t0 = time.time()
    ser.write((PING_CMD + '\r\n').encode())
    deadline = t0 + PING_TO
    n = 0; last = ''
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='replace').strip()
            if line:
                n += 1; last = line
                if STOP in line:
                    return True, (time.time() - t0) * 1000.0, n, last
        else:
            time.sleep(0.01)
    # 타임아웃: n==0 이면 완전 무응답(링크 down), n>0 이면 부분응답(전송 중 드롭)
    return False, (time.time() - t0) * 1000.0, n, last


def ping_doser(ser):
    """도저 'ls'(read-only) 1회 송신 → 완료 마커까지 수신. (성공여부, RTT_ms, 받은줄수, 요약).
    측정기 status와 달리 단일 종단 마커가 없어 '왼쪽 동작(RUN)' 줄(마지막)을 완료로 본다."""
    ser.reset_input_buffer()
    t0 = time.time()
    ser.write((DOSER_CMD + '\n').encode())   # LF only
    deadline = t0 + DOSER_TO
    lines = []
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='replace').strip()
            if line:
                lines.append(line)
                if DOSER_DONE in line:
                    lrt = lgt = None
                    for ln in lines:
                        if '왼쪽 동작(RUN)' in ln:
                            m = re.search(r'(\d+)', ln); lrt = m.group(1) if m else None
                        elif '왼쪽 휴지(GAP)' in ln:
                            m = re.search(r'(\d+)', ln); lgt = m.group(1) if m else None
                    return True, (time.time() - t0) * 1000.0, len(lines), f'lrt={lrt}ms lgt={lgt}'
        else:
            time.sleep(0.02)
    # 타임아웃: n==0 완전 무응답 / n>0 부분응답(전송 중 드롭)
    return False, (time.time() - t0) * 1000.0, len(lines), ''


def open_port(port):
    return serial.Serial(port, BAUD, timeout=1)


def check_measure(port):
    """측정기 1회 확인. 반환: 0 OK / 1 드롭 / 2 열기실패."""
    try:
        with open_port(port) as ser:
            time.sleep(0.3)
            ok, rtt, n, last = ping(ser)
    except serial.SerialException as e:
        print(f'{now()}  [측정기 열기실패] {port}: {e}  (포트 점유중이거나 BT 미연결)')
        return 2
    if ok:
        print(f'{now()}  [측정기 OK] {port}  RTT={rtt:.0f}ms  ({n}줄)')
        return 0
    elif n == 0:
        print(f'{now()}  [측정기 끊김] {port}  무응답 {PING_TO:.0f}s — 펌웨어 안 보임(RF down)')
        return 1
    else:
        print(f'{now()}  [측정기 부분응답] {port}  {n}줄 받고 종단 미수신 (마지막:"{last}") — 전송 중 드롭')
        return 1


def check_doser(port):
    """도저 1회 확인(read-only ls). 반환: 0 OK / 1 드롭 / 2 열기실패."""
    try:
        with open_port(port) as ser:
            time.sleep(0.3)
            ok, rtt, n, summary = ping_doser(ser)
    except serial.SerialException as e:
        print(f'{now()}  [도저 열기실패] {port}: {e}  (포트 점유중이거나 BT 미연결)')
        return 2
    if ok:
        print(f'{now()}  [도저 OK] {port}  RTT={rtt:.0f}ms  ({n}줄  {summary})')
        return 0
    elif n == 0:
        print(f'{now()}  [도저 끊김] {port}  ls 무응답 {DOSER_TO:.0f}s — 펌웨어 안 보임(RF down)')
        return 1
    else:
        print(f'{now()}  [도저 부분응답] {port}  {n}줄, ls 완료 미수신 — 전송 중 드롭')
        return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default=PORT, help='측정기 포트(기본 bt_config measure)')
    ap.add_argument('--doser-port', default=DOSER_PORT, help='도저 포트(기본 bt_config doser)')
    ap.add_argument('--interval', type=float, default=5.0, help='핑 간격(초)')
    ap.add_argument('--once', action='store_true', help='측정기 1회 확인하고 종료')
    ap.add_argument('--doser', action='store_true', help='도저 1회 확인(read-only ls)하고 종료')
    ap.add_argument('--both', action='store_true', help='측정기+도저 둘 다 1회 확인하고 종료')
    ap.add_argument('--logfile', default=LOGFILE)
    args = ap.parse_args()

    if args.both:
        rc_m = check_measure(args.port)
        rc_d = check_doser(args.doser_port)
        sys.exit(0 if rc_m == 0 and rc_d == 0 else 1)

    if args.doser:
        sys.exit(check_doser(args.doser_port))

    if args.once:
        sys.exit(check_measure(args.port))

    # 연속 모니터
    fh = open(args.logfile, 'a', encoding='utf-8')
    log(f'==== bt_health 시작 port={args.port} interval={args.interval}s ====', fh)
    pings = drops = partials = openfail = 0
    rtt_sum = rtt_max = 0.0
    last_state = None  # 'up' / 'down'
    ser = None
    try:
        while True:
            cycle = time.time()
            # 포트 확보(끊겼으면 재오픈 시도 = HC-06 재연결 유도)
            if ser is None:
                try:
                    ser = open_port(args.port)
                    time.sleep(0.3)
                    log(f'[재오픈 성공] {args.port}', fh)
                except serial.SerialException as e:
                    openfail += 1
                    if last_state != 'down':
                        log(f'[열기실패] {args.port}: {e}', fh)
                        last_state = 'down'
                    time.sleep(args.interval)
                    continue

            try:
                ok, rtt, n, last = ping(ser)
            except serial.SerialException as e:
                log(f'[예외] {e} — 포트 닫고 재오픈 예정', fh)
                try: ser.close()
                except Exception: pass
                ser = None
                continue

            pings += 1
            if ok:
                rtt_sum += rtt; rtt_max = max(rtt_max, rtt)
                if last_state != 'up':
                    log(f'[연결 OK] RTT={rtt:.0f}ms (이전 상태에서 복구)', fh)
                    last_state = 'up'
                # 정상은 조용히(요약은 종료 시). 너무 조용하면 주석 해제:
                # log(f'ok RTT={rtt:.0f}ms', fh)
            else:
                if n == 0:
                    drops += 1
                    log(f'★[드롭] 무응답 {PING_TO:.0f}s — RF 링크 끊김. 포트 재오픈 시도', fh)
                else:
                    partials += 1
                    log(f'★[부분드롭] {n}줄 후 끊김(마지막:"{last}") = 측정 중 끊김과 동일 양상. 재오픈 시도', fh)
                last_state = 'down'
                try: ser.close()
                except Exception: pass
                ser = None  # 다음 루프에서 재오픈

            # 간격 유지
            sleep = args.interval - (time.time() - cycle)
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        pass
    finally:
        ok_n = pings - drops - partials
        avg = (rtt_sum / ok_n) if ok_n else 0.0
        log('---- 요약 ----', fh)
        log(f'총 핑 {pings}  정상 {ok_n}  드롭(무응답) {drops}  부분드롭 {partials}  열기실패 {openfail}', fh)
        if ok_n:
            log(f'정상 RTT 평균 {avg:.0f}ms  최대 {rtt_max:.0f}ms', fh)
        if pings:
            log(f'드롭률 {100.0*(drops+partials)/pings:.1f}%', fh)
        if ser:
            try: ser.close()
            except Exception: pass
        fh.close()


if __name__ == '__main__':
    main()
