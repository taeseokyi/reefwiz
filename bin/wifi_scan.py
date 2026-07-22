#!/usr/bin/env python3
"""2.4GHz WiFi 혼잡도 시계열 수집기 (BT 링크 간섭 진단용, 2026-07-22).

`netsh wlan show networks mode=bssid` 의 결과(OS 주기 스캔 캐시를 읽을 뿐,
새 스캔을 강제하지 않으므로 RF 부하 사실상 0 = 측정/도저 BT 링크에 영향 없음)를
파싱해 2.4GHz 대역 AP 점유 지표를 한 줄 JSON 으로 로그에 append 한다.

BT 동글(CSR)은 2.4GHz 전 대역을 AFH 로 호핑하므로, 주변 AP 가 대역을 얼마나
점유하는지(회피 여지)가 링크 마진에 영향을 준다. 근본 원인은 습도(48h 이동평균
≥82%)로 확정됐고, WiFi 혼잡은 교락 후보라 시계열로 계속 추적해 상관을 본다.

라벨이 한글(로캘)이라도 깨지지 않게 값의 형태로 파싱한다:
  - 값이 `NN%`  -> 신호 세기
  - 값이 단일 정수 -> 채널 (속도 줄은 숫자 여러 개라 제외됨)
채널 <= 14 이면 2.4GHz.

원본은 저장소 bin/, 배포본은 C:\\dkh\\work\\ (수정 시 재복사 필수).
로그: C:\\dkh\\wifi_congestion.jsonl (WSL 에서 /mnt/c/dkh/wifi_congestion.jsonl 로 읽기 가능).
"""
import datetime
import json
import os
import re
import subprocess
import sys

LOG_PATH = os.environ.get("WIFI_SCAN_LOG", r"C:\dkh\wifi_congestion.jsonl")
MAC_RE = re.compile(r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}")
# 채널 c 를 중심으로 20MHz(±2 채널) 겹침 창 — 호퍼가 실제로 겪는 최악 점유 계산용
OVERLAP = 2


def run_netsh():
    """netsh 출력을 문자열로. 한글 Windows 콘솔 코드페이지(cp949)로 디코드."""
    out = subprocess.run(
        ["netsh", "wlan", "show", "networks", "mode=bssid"],
        capture_output=True, timeout=60,
    ).stdout
    for enc in ("cp949", "utf-8"):
        try:
            return out.decode(enc)
        except UnicodeDecodeError:
            continue
    return out.decode("cp949", errors="replace")


def parse(text):
    """BSSID 블록별로 (channel, signal%) 추출. SSID 는 참고용."""
    aps = []
    cur_ssid = None
    cur = None
    for line in text.splitlines():
        stripped = line.strip()
        # SSID 헤더: "SSID 3 : name"  (BSSID 줄과 구분: BSSID 는 MAC 이 값)
        m_ssid = re.match(r"SSID\s+\d+\s*:\s*(.*)$", stripped)
        if m_ssid and not MAC_RE.search(stripped):
            cur_ssid = m_ssid.group(1).strip()
            continue
        if MAC_RE.search(stripped) and re.match(r"BSSID", stripped):
            if cur:
                aps.append(cur)
            cur = {"ssid": cur_ssid, "bssid": MAC_RE.search(stripped).group(0).lower(),
                   "channel": None, "signal": None}
            continue
        if cur is None:
            continue
        # 값 = 마지막 콜론 뒤
        if ":" not in stripped:
            continue
        val = stripped.split(":", 1)[1].strip()
        if re.fullmatch(r"\d+%", val) and cur["signal"] is None:
            cur["signal"] = int(val[:-1])
        elif re.fullmatch(r"\d+", val) and cur["channel"] is None:
            cur["channel"] = int(val)
    if cur:
        aps.append(cur)
    return [a for a in aps if a["channel"] is not None]


def iface_state():
    """호스트 WiFi 인터페이스 연결 상태(자체 라디오 트래픽 여부 참고)."""
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                             capture_output=True, timeout=30).stdout
        for enc in ("cp949", "utf-8"):
            try:
                txt = out.decode(enc); break
            except UnicodeDecodeError:
                continue
        else:
            txt = out.decode("cp949", errors="replace")
        connected = "연결됨" in txt or "connected" in txt.lower()
        # 미연결일 때 상태 문자열엔 '연결되지 않음'/'disconnected'
        if "연결되지 않음" in txt or "disconnected" in txt.lower():
            connected = False
        return "connected" if connected else "disconnected"
    except Exception:
        return None


def compute(aps):
    band24 = [a for a in aps if a["channel"] <= 14]
    sig = [a["signal"] or 0 for a in band24]
    # 채널별 신호 합(겹침 미고려)
    ch_load = {}
    for a in band24:
        ch_load.setdefault(a["channel"], []).append(a["signal"] or 0)
    # 겹침 반영 피크: 각 채널 c 에서 |ch-c|<=2 인 AP 신호합의 최댓값
    peak_overlap = 0
    for c in range(1, 14):
        s = sum(a["signal"] or 0 for a in band24 if abs(a["channel"] - c) <= OVERLAP)
        peak_overlap = max(peak_overlap, s)
    return {
        "ap24": len(band24),
        "strong24": sum(1 for s in sig if s >= 50),
        "max_signal24": max(sig) if sig else 0,
        "sum_signal24": sum(sig),          # 대역 총 점유 프록시
        "peak_overlap": peak_overlap,      # 호퍼가 겪는 최악 채널 점유
        "channels": {str(c): sorted(v, reverse=True) for c, v in sorted(ch_load.items())},
    }


def main():
    now = datetime.datetime.now()
    rec = {"ts": now.isoformat(timespec="seconds")}
    try:
        aps = parse(run_netsh())
        rec.update(compute(aps))
        rec["iface"] = iface_state()
        rec["aps"] = [{"ssid": a["ssid"], "ch": a["channel"], "sig": a["signal"]}
                      for a in aps if a["channel"] <= 14]
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    line = json.dumps(rec, ensure_ascii=False)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"log write failed: {e}", file=sys.stderr)
    print(line)


if __name__ == "__main__":
    main()
