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
  C:\dkh\python313\python.exe -X utf8 bt_health.py            # 5초마다 연속 핑+로깅
  C:\dkh\python313\python.exe -X utf8 bt_health.py --once     # 지금 연결됐는지 1회 확인
  C:\dkh\python313\python.exe -X utf8 bt_health.py --interval 2 --port COM9
"""
import sys, time, argparse, datetime
import serial

from bt_config import get_port

PORT     = get_port('measure')   # BT 포트는 bt_config.json 단일 설정에서 로드(포트 바뀌면 설정만 수정). --port로 오버라이드 가능
BAUD     = 9600
PING_CMD = 'status'          # 부작용 없음: 상태만 출력, 액추에이터/샘플링 없음
STOP     = '============'    # printStatus() 종단 마커
PING_TO  = 3.0               # 핑 1회 응답 대기 상한(초). 정상 RTT는 보통 수백 ms.
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


def open_port(port):
    return serial.Serial(port, BAUD, timeout=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default=PORT)
    ap.add_argument('--interval', type=float, default=5.0, help='핑 간격(초)')
    ap.add_argument('--once', action='store_true', help='1회만 확인하고 종료')
    ap.add_argument('--logfile', default=LOGFILE)
    args = ap.parse_args()

    if args.once:
        try:
            with open_port(args.port) as ser:
                time.sleep(0.3)
                ok, rtt, n, last = ping(ser)
        except serial.SerialException as e:
            print(f'{now()}  [열기실패] {args.port}: {e}  (포트 점유중이거나 BT 미연결)')
            sys.exit(2)
        if ok:
            print(f'{now()}  [연결 OK] {args.port}  RTT={rtt:.0f}ms  ({n}줄)')
            sys.exit(0)
        elif n == 0:
            print(f'{now()}  [연결 끊김] {args.port}  무응답 {PING_TO:.0f}s — 펌웨어 안 보임(RF down)')
            sys.exit(1)
        else:
            print(f'{now()}  [부분응답] {args.port}  {n}줄 받고 종단 미수신 (마지막:"{last}") — 전송 중 드롭')
            sys.exit(1)

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
