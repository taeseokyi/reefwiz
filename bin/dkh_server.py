#!/usr/bin/env python3
import json
import logging
import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

DAT_FILE = os.path.join(os.path.dirname(__file__), "dkh.dat")
HOST = "0.0.0.0"
PORT = 9999  # 2026-07-06 사용자 요청으로 9090→9999 변경

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
