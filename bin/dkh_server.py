#!/usr/bin/env python3
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

DAT_FILE = os.path.join(os.path.dirname(__file__), "dkh.dat")
HOST = "0.0.0.0"
PORT = 9999  # 2026-07-06 사용자 요청으로 9090→9999 변경

# 도저 수동 설정(대시보드→GitHub docs/doser_override.json) 폴링 — 이 서버가 상시
# 프로세스라 여기 얹는다(2026-07-06). 확인/적용 로직은 전부 doser_adjust.py(인자 없음
# = 오버라이드 확인 전용) 몫이고 서버는 주기 실행+적용 감지 후 동기화만 한다.
# 측정 후 래퍼의 확인 경로는 백업으로 그대로 있음 — 이 폴러가 지연을 8h→최대 5분으로 줄임.
POLL_S = 300
ADJUST_SCRIPT = r"C:\dkh\work\doser_adjust.py"
OVERRIDE_STATE_FILE = r"C:\dkh\work\doser_override_state.json"
SYNC_CMD = [
    r"C:\Windows\System32\wsl.exe", "-d", "Ubuntu", "--",
    "/home/tsyi/miniconda3/envs/openclaw/bin/python3",
    "/home/tsyi/work/reefwiz/bin/sync_dkh_dat.py",
]
CREATE_NO_WINDOW = 0x08000000

_log_file = r"C:\dkh\dkh_server.log" if os.name == "nt" else None
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    filename=_log_file,
)
log = logging.getLogger(__name__)


def read_last_dkh():
    with open(DAT_FILE, "r") as f:
        last_line = None
        for line in f:
            stripped = line.strip()
            if stripped:
                last_line = stripped
    if last_line is None:
        raise ValueError("dkh.dat is empty")
    parts = last_line.split()
    if len(parts) < 5:
        raise ValueError(f"malformed line: {last_line!r}")
    return float(parts[4])  # tank_kh


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/dkh":
            try:
                dkh = read_last_dkh()
            except Exception as e:
                log.warning("read error: %s", e)
                dkh = 0.0
            body = json.dumps({"dkh": dkh}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = b"Not Found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _state_stamp():
    """오버라이드 상태 파일의 (mtime, 내용) — 적용이 일어났는지 감지용."""
    try:
        with open(OVERRIDE_STATE_FILE, "rb") as f:
            return (os.path.getmtime(OVERRIDE_STATE_FILE), f.read())
    except OSError:
        return None


def poll_override_once(run=subprocess.run):
    """doser_adjust.py(오버라이드 확인 전용)를 한 번 실행. 적용이 일어났으면 True."""
    before = _state_stamp()
    run(
        [sys.executable, ADJUST_SCRIPT],
        cwd=os.path.dirname(ADJUST_SCRIPT) or None,
        capture_output=True,
        timeout=120,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return _state_stamp() != before


def override_poller():
    """5분마다 대시보드 수동 설정을 확인, 적용되면 곧바로 동기화(대시보드에 '적용됨' 반영).
    어떤 예외도 API 서버를 건드리지 않게 루프 안에서 삼킨다."""
    log.info("도저 오버라이드 폴러 시작 (%ds 주기)", POLL_S)
    while True:
        time.sleep(POLL_S)
        try:
            if poll_override_once():
                log.info("도저 수동 설정 적용 감지 → 동기화 실행")
                sync = subprocess.run(
                    SYNC_CMD, capture_output=True, timeout=300,
                    creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                log.info("동기화 종료 exit=%d", sync.returncode)
        except Exception as e:
            log.warning("오버라이드 폴링 실패(다음 주기 재시도): %r", e)


def make_server():
    while True:
        try:
            server = ThreadedHTTPServer((HOST, PORT), Handler)
            server.allow_reuse_address = True
            return server
        except OSError as e:
            log.error("bind failed (%s), retrying in 5s...", e)
            time.sleep(5)


def main():
    server = make_server()
    log.info("Listening on http://%s:%d/api/dkh", HOST, PORT)

    if os.name == "nt":  # 도저 폴링은 Windows(배포 환경)에서만 의미 있음
        threading.Thread(target=override_poller, daemon=True).start()

    def shutdown(sig, _frame):
        log.info("signal %d received, shutting down", sig)
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            server.serve_forever()
        except Exception as e:
            log.error("serve_forever crashed: %s — restarting in 3s", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
