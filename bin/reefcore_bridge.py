#!/usr/bin/env python3
"""AquaWiz dKH -> reefCore(reefChecker) 브리지.

dkh.dat 의 최신 측정값을 reefCore MQTT 브로커에 '최근 측정값' 요약으로 발행하면,
백엔드가 파싱해 해당 체커의 dKH 측정 레코드를 생성한다(검증 완료 2026-06-23).

자격증명은 코드에 두지 않는다(저장소 public). 환경변수로 주입:
  REEFCORE_USER  reefCore 계정 이메일(=MQTT username)
  REEFCORE_PASS  reefCore 계정 비번(=MQTT password)
선택:
  REEFCORE_MAC      기본 b0cbd88ec880
  REEFCORE_DKHFILE  기본 자동탐색(C:\\dkh\\work\\dkh.dat / /mnt/c/dkh/work/dkh.dat)

실행: python reefcore_bridge.py          # 새 측정이면 1회 발행(중복 스킵)
      python reefcore_bridge.py --force  # 중복 무시하고 강제 발행
dkh.dat 한 줄 형식: HH ref_pH tank_pH ref_kh tank_kh temp
"""
import os, ssl, sys, json, time, datetime
import paho.mqtt.client as mqtt

BROKER, PORT = "reef.anih.net", 8883
MAC   = os.environ.get("REEFCORE_MAC", "b0cbd88ec880")
TOPIC = f"reefcore-checker-{MAC[-6:]}/sensor/________________/state"  # '최근 측정값'
USER  = os.environ.get("REEFCORE_USER")
PW    = os.environ.get("REEFCORE_PASS")
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".reefcore_last")

def find_dkhfile():
    p = os.environ.get("REEFCORE_DKHFILE")
    if p and os.path.exists(p): return p
    for c in (r"C:\dkh\work\dkh.dat", "/mnt/c/dkh/work/dkh.dat",
              os.path.expanduser("~/work/reefkeeper/dkh.dat")):
        if os.path.exists(c): return c
    sys.exit("dkh.dat 를 찾을 수 없음 (REEFCORE_DKHFILE 지정)")

def main():
    if not USER or not PW:
        sys.exit("REEFCORE_USER / REEFCORE_PASS 환경변수가 필요합니다.")
    force = "--force" in sys.argv
    line = open(find_dkhfile()).read().strip().splitlines()[-1].strip()
    parts = line.split()
    dkh, temp = float(parts[4]), float(parts[5])
    if dkh <= 0:                              # 0=에러, 음수=평탄 미도달(V4 규약) → 스킵
        print(f"스킵: 비정상 dKH={dkh} ({line})"); return
    # 중복 발행 방지: 같은 dkh.dat 줄이면 스킵
    last = open(STATE).read().strip() if os.path.exists(STATE) else ""
    if line == last and not force:
        print("스킵: 이미 발행한 측정 (--force 로 강제)"); return

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = f"dKH: {dkh:.2f} dKH | {temp:.1f}°C @ {now}"

    c = mqtt.Client(client_id="reefkeeper-bridge", clean_session=True)
    c.username_pw_set(USER, PW)
    c.tls_set(cert_reqs=ssl.CERT_NONE); c.tls_insecure_set(True)  # 브로커 인증서 만료 상태
    c.connect(BROKER, PORT, keepalive=30); c.loop_start()
    info = c.publish(TOPIC, summary, qos=1, retain=False)
    info.wait_for_publish(timeout=10)
    c.loop_stop(); c.disconnect()
    if info.rc == 0:
        open(STATE, "w").write(line)
        print(f"발행 완료: {summary}")
    else:
        sys.exit(f"발행 실패 rc={info.rc}")

if __name__ == "__main__":
    main()
