#!/usr/bin/env python3
"""
AquaWiz 컨트롤러 시계 동기화 (pyserial 직접 전송)

기존 adu-srial-port.ahk(아두이노 IDE GUI 자동화) 대체용.
지정한 시리얼 포트로 'set time HH:mm:ss' 를 보내고 응답을 로그에 남긴다.

사용법:
  python set_time.py doser         # ★장치 이름 → bt_config.json 에서 포트 자동 해석(권장, 스케줄러용)
  python set_time.py measure       # (측정기도 이름으로 지정 가능)
  python set_time.py COM9          # 포트 직접 지정(하위호환)
  python set_time.py 9             # 숫자만 주면 COM9 로 해석
  python set_time.py doser 115200  # baud 지정 (기본 9600)

★포트가 바뀌면 이 인자를 고치지 말고 bt_config.json 만 고친다(단일 설정, [[bt_config]]).
스케줄러 작업 인자를 'doser' 로 두면 이후 포트 변경 시 작업 재정의(관리자)가 영영 불필요.

시리얼 규약: 9600 baud, 연결 후 2초 대기 + 입력버퍼 비우기.
줄바꿈은 LF('\\n')만 — 도저(ca_reactor) 펌웨어는 '\\r'가 붙으면 명령을 실행하지 않고
echo만 한다(아두이노 시리얼모니터 "새 줄" 설정과 동일). measure_kh 포트와 규약 다름.
"""

import os
import sys
import time
from datetime import datetime

import serial

BAUD     = 9600
LOG_FILE = r"C:\dkh\set_time.log" if os.name == "nt" else None


def log(msg):
    """stdout + (Windows) 파일에 한 줄 기록. pythonw 로 실행돼도 흔적이 남도록."""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def norm_port(arg):
    arg = arg.strip()
    if arg.lower() in ("measure", "doser"):   # 장치 이름 → bt_config.json 에서 포트 해석
        from bt_config import get_port
        return get_port(arg.lower())
    return f"COM{arg}" if arg.isdigit() else arg


def main():
    if len(sys.argv) < 2:
        log("[ERR] 포트 인자 필요 (예: COM9 또는 9)")
        sys.exit(1)

    port = norm_port(sys.argv[1])
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else BAUD
    cmd  = "set time"

    try:
        with serial.Serial(port, baud, timeout=1) as ser:
            time.sleep(2)              # 연결/리셋 안정화
            ser.reset_input_buffer()
            cmd = "set time " + datetime.now().strftime("%H:%M:%S")  # 전송 직전 캡처(정확도)
            ser.write((cmd + "\n").encode())   # LF only — '\r' 붙으면 미실행

            # 응답 최대 3초 수집 (전달 검증용)
            resp = []
            deadline = time.time() + 3
            while time.time() < deadline:
                if ser.in_waiting:
                    ln = ser.readline().decode("utf-8", errors="replace").strip()
                    if ln:
                        resp.append(ln)
                else:
                    time.sleep(0.05)

        detail = " / ".join(resp) if resp else "(무응답)"
        log(f"[OK] {port}@{baud} ← '{cmd}' | resp: {detail}")
    except serial.SerialException as e:
        log(f"[ERR] {port}@{baud} 시리얼 오류: {e}")
        sys.exit(2)
    except Exception as e:
        log(f"[ERR] {port}@{baud} 예외: {e}")
        sys.exit(3)


if __name__ == "__main__":
    main()
