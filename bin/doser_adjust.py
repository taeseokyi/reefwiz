#!/usr/bin/env python3
"""AquaWiz dKH 기반 AFR 도저 자동 조정 (매일 13시 측정 종료 후, 래퍼가 호출 — 2026-07-11 주말 확대).

동작 한 줄 요약: 최근 7일 dKH 수준·추세를 보고, 목표(기본 7.2, 대시보드에서 변경 가능)에
일주일에 걸쳐 접근하도록 왼쪽 펌프(AFR 50% 용액)의 1회 가동시간(lrt, ms)을 소폭 조정한다.

- 도저 펌웨어 = ca_reactor_output_controller_v8 (포트=bt_config.json 의 doser, 9600, LF만 — CR 붙으면 미실행).
  `lrt <ms>` 는 EEPROM 에 저장되지만 **동작 타이머 인터벌은 `refresh all` 을 보내야
  반영된다**(사용자 확인 2026-07-06; 펌웨어상 setInterval 이 refresh_all_timers() 에만
  있음). 따라서 적용 = `lrt` 전송→에코 검증→`refresh all`→ack 확인 순서. refresh 는
  양쪽 펌프의 휴지(GAP) 타이머도 리셋하므로 다음 도징은 조정 시점 기준 ~4h 뒤로 재정렬됨
  (하루 횟수는 유지). 호스트가 죽어도 도저는 EEPROM 값으로 자율 동작(실패 안전).
  현재 lrt 는 매번 `ls` 로 장치에서 직접 읽는다(사용자 수동 변경도 자동 반영).
- 권고 모드: 계산이 성공한 첫 ADVISORY_RUNS(2)회는 기록만 하고 lrt 를 바꾸지 않는다.
  ★AUTO_APPLY=False(사용자 지시 2026-07-06): 그 이후로도 자동 적용하지 않고 계속
  권고만 남긴다(수동 오버라이드 적용은 사용자 지시이므로 그대로 동작). 자동 적용을
  재개하려면 AUTO_APPLY=True 로 바꾸고 배포본 재복사.
- 안전 레일: 유효 측정 부족 시 중단 / 1회 조정 스텝 ±30% / lrt 절대범위 2000~24000ms
  (사용자 지정 상한 3배=원액 18mL/일) / 변화 200ms 미만은 스킵(데드밴드·EEPROM 마모).
  ※자동 조정은 이 레일 안에서만 움직인다(정지 불가). 정지(lrt 0)는 대시보드 수동 설정
  0mL/일로만 가능(plan_lrt). gap(lgt)은 어느 경로에서도 건드리지 않는다.
- ★CO₂ 편향 의심 제외(2026-07-13): 새벽 실내 CO₂ 축적으로 dKH가 −0.07~−0.24 낮게
  나오는 측정(판정=ref 곡선 형태, parse_plateau_log.classify_co2_suspect 단일 소스)을
  추세·수준 계산에서 제외한다. 플래그는 GitHub 의 docs/dkh_series.json(co2_suspect
  필드)에서 읽고, 날짜 없는 로컬 dkh.dat 과는 값 시퀀스 접미(suffix) 정렬로 대응
  — 실행 시점(측정 직후, sync 전)에 원격에 이번 회차 행이 없는 게 정상이라 오프셋
  k를 0부터 늘려가며 맞춘다. 조회/정렬 실패 시 제외 없이 종전 계산으로 폴백(권고
  전용이라 안전), 제외가 창의 다수(>CO2_EXCLUDE_MAX)면 판정기 오작동 의심으로 중단.
- 기록: C:\\dkh\\work\\doser_history.json (sync가 docs/로 복사→대시보드 카드),
  상세 로그 C:\\dkh\\doser_adjust.log.
- ★목표 dKH 설정(2026-07-06): 대시보드가 docs/doser_config.json 에 {target_dkh} 를
  커밋한다. 일회성 오버라이드와 달리 영속 설정 — 매 자동 조정 회차마다 읽어 목표로
  쓴다(없음/범위 밖/조회 실패 = 기본 TARGET_DKH). 이력 항목에 target 필드로 기록.
- ★수동 오버라이드(2026-07-06): 대시보드에서 사용자가 입력한 값은 GitHub API 커밋으로
  docs/doser_override.json 에 올라온다. 이 스크립트는 **매 측정 종료 후**(래퍼가 매회 호출)
  그 파일을 GitHub API 로 읽어(Pages 배포 지연 회피), 아직 적용 안 한 id 면 도저에 적용
  하고 이력(mode=manual)에 남긴다 → sync 로 대시보드에 "적용됨" 표시. 새 오버라이드가
  있는 회차는 자동 조정을 건너뛴다(수동 우선). 적용 성공한 id 만 상태 파일에 기록되므로
  실패(BT 순단 등)하면 다음 측정 후 자동 재시도된다. 값 0 = 정지(lrt 0, 무배출);
  1.5~18mL/일은 종전대로. 대시보드가 (0,1.5) 사이는 막지만 오면 방어적으로 하한 처리.
- CLI: (인자 없음)=오버라이드 확인만 / --slot-adjust=오버라이드 확인+정기 자동 조정
  (래퍼가 매일 13시 회차에만 붙임) / --check(장치 조회만) / --dry-run(계산만, 무접속).

원본은 저장소 bin/, 배포본은 C:\\dkh\\work\\ (수정 시 재복사 필수).
"""
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

DAT_FILE = r"C:\dkh\work\dkh.dat"
HISTORY_FILE = r"C:\dkh\work\doser_history.json"
OVERRIDE_STATE_FILE = r"C:\dkh\work\doser_override_state.json"
LOG_FILE = r"C:\dkh\doser_adjust.log" if os.name == "nt" else None

# 수동 오버라이드는 대시보드가 GitHub API 로 커밋한 파일. Pages URL 이 아니라 GitHub API
# raw 로 읽는 이유 = Pages 는 배포까지 수 분 지연이 있고 CDN 캐시도 낌(API 는 즉시 반영).
# 무인증 읽기 60회/h 제한이나 우리는 하루 3회라 여유.
OVERRIDE_URL = ("https://api.github.com/repos/taeseokyi/reefwiz/contents/"
                "docs/doser_override.json?ref=master")
CONFIG_URL = ("https://api.github.com/repos/taeseokyi/reefwiz/contents/"
              "docs/doser_config.json?ref=master")
SERIES_URL = ("https://api.github.com/repos/taeseokyi/reefwiz/contents/"
              "docs/dkh_series.json?ref=master")

from bt_config import get_port

PORT = get_port('doser')  # BT 포트는 bt_config.json 단일 설정에서 로드(포트 바뀌면 설정만 수정)
BAUD = 9600

TARGET_DKH = 7.2          # 기본 목표 (AquaWiz 측정 기준, 편향 보정 없음) — doser_config.json 이 우선
TARGET_LO, TARGET_HI = 6.0, 9.0  # 대시보드 목표 설정 허용 범위(밖이면 무시하고 기본값)
DAILY_RATE_CAP = 0.25     # dKH/일 — 사용자 지정 일일 변화 상한 (허용 0.5의 절반)
APPROACH_DAYS = 7.0       # 오차를 이 기간에 걸쳐 좁힌다
SENS = 0.0058             # dKH/(원액mL·일) — 6/29 볼루스 55mL→+0.32 실측 유도

MS_PER_ML = 4000          # 8000ms=2mL 실측 캘리브레이션
DOSES_PER_DAY = 6
DILUTION = 0.5            # 통에는 50% 희석액 — 원액 환산 계수
LRT_MIN = 2000            # 0.5mL/회 미만은 기계적 신뢰 어려움
LRT_MAX = 24000           # 사용자 지정 하드 상한 = 현재의 3배(원액 18mL/일)
STEP_MAX_FRAC = 0.30      # 1회 조정 최대 ±30%
DEADBAND_MS = 200

ROWS = 21                 # 최근 21행 ≈ 7일(하루 3회)
MIN_VALID = 10
VALID_LO, VALID_HI = 4.0, 12.0
ROW_DAYS = 8.0 / 24.0     # 행 간격 8시간 가정

CO2_EXCLUDE_MAX = 9       # 창 안 CO₂ 제외가 이보다 많으면 판정기 오작동 의심 → 중단
CO2_ALIGN_MIN_OVERLAP = 3 # 접미 정렬로 인정할 최소 겹침 행 수
CO2_ALIGN_MAX_LAG = 6     # 원격에 아직 없는 최신 로컬 행 수 허용치(보통 1=방금 측정분)

ADVISORY_RUNS = 2         # 계산 성공 기준 처음 2회는 권고만(적용 안 함)
AUTO_APPLY = False        # ★False=자동 적용 영구 꺼짐, 계속 권고만(사용자 지시 2026-07-06).
                          #   수동 오버라이드(대시보드)는 이 스위치와 무관하게 적용된다.
HISTORY_MAX = 52


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    try:
        print(line)
    except Exception:
        pass  # 파이프 인코딩(cp949) 예외 등이 로그 파일 기록까지 막으면 안 됨(2026-07-08 보강)


def lrt_to_ml_day(lrt_ms):
    """1회 가동시간(ms) → 하루 원액 투입량(mL)."""
    return lrt_ms / MS_PER_ML * DOSES_PER_DAY * DILUTION


def ml_day_to_lrt(ml_day):
    return ml_day / (DOSES_PER_DAY * DILUTION) * MS_PER_ML


def plan_lrt(ml_day):
    """수동 설정값(원액 mL/일) → 목표 lrt(ms), 클램프 메모. gap(lgt)은 건드리지 않는다.

    - 0 이하 = 정지: lrt 0(펌프 0ms 가동=무배출). 게이트 타이머가 언제 점화하든 결과가
      항상 0이라 롤오버 주기 밀림과 무관 = 지터 없는 정지.
    - 양수 = [LRT_MIN, LRT_MAX] 클램프(0.5mL/회 미만 미세 도징은 기계적 신뢰 어려움).
      대시보드는 0 또는 1.5~18mL/일만 보내지만, 방어적으로 (0,1.5) 도 하한으로 올린다.
    """
    if ml_day <= 0:
        return 0, "정지"
    raw = int(round(ml_day_to_lrt(ml_day) / 100.0) * 100)
    lrt = max(LRT_MIN, min(LRT_MAX, raw))
    return lrt, ("" if lrt == raw else f"범위 클램프 {raw}→{lrt}ms")


def read_dat_lines(path=None):
    if path is None:
        path = DAT_FILE  # 기본 인자에 박으면 테스트에서 모듈 변수 교체가 안 먹음
    with open(path, encoding="utf-8", errors="replace") as f:
        return [ln.split() for ln in f.read().splitlines() if ln.strip()]


def read_recent_kh(path=None, rows=ROWS, co2_excluded=None):
    """dkh.dat 마지막 rows행에서 (행위치, tank_kh) 유효값만. 0.0=에러, 음수=미평탄 제외.

    co2_excluded(전체 파일 기준 줄 인덱스 집합, fetch_co2_excluded 반환)에 든 행은
    CO₂ 편향 의심으로 추가 제외한다. 행위치 인덱스 i 는 제외돼도 건너뛰기만 하므로
    Theil-Sen 의 8h 간격 시간축이 그대로 유지된다(기존 유효성 탈락과 같은 방식).
    반환: (pts, 창 안에서 CO₂ 로 제외된 행 수)
    """
    lines = read_dat_lines(path)
    base = len(lines) - min(rows, len(lines))
    pts, n_co2 = [], 0
    for i, parts in enumerate(lines[-rows:]):
        try:
            kh = float(parts[4])
        except (IndexError, ValueError):
            continue
        if not (VALID_LO < kh < VALID_HI):
            continue
        if co2_excluded and (base + i) in co2_excluded:
            n_co2 += 1
            continue
        pts.append((i, kh))
    return pts, n_co2


def _series_key(hh, tank_kh, temp):
    """정렬 비교 키 — series 는 tank_kh 를 abs() 로 내보내므로 로컬도 abs 로 맞춘다."""
    return (int(hh), round(abs(float(tank_kh)), 3), round(float(temp), 1))


def fetch_co2_excluded(lines, series=None):
    """원격 dkh_series.json 과 접미 정렬로 CO₂ 편향 의심 행의 줄 인덱스 집합을 얻는다.

    로컬 dkh.dat 에는 날짜가 없어 원격 series(날짜·co2_suspect 보유)와 키 매칭이
    불가 → (hh, |tank_kh|, temp) 값 시퀀스의 접미 일치로 정렬한다. 실행 시점(측정
    직후, sync 전)에 원격에는 이번 회차 행이 아직 없으므로 오프셋 k(원격에 없는
    최신 로컬 행 수)를 0부터 CO2_ALIGN_MAX_LAG 까지 늘려가며 최소 k 를 채택하고,
    원격에 없는 최신 k행은 미의심 취급. 실패(조회 불가/정렬 불일치) 시 None —
    호출부는 제외 없이 종전 계산으로 폴백한다.
    series 인자는 테스트용 주입(None 이면 GitHub API 조회).
    """
    if series is None:
        series = fetch_repo_json(SERIES_URL, "CO₂플래그")
    if not isinstance(series, list) or not series:
        return None
    try:
        s_keys = [_series_key(r["hh"], r["tank_kh"], r["temp"]) for r in series]
    except (KeyError, TypeError, ValueError):
        log("[CO₂플래그] series 형식 오류 — 제외 없이 계산")
        return None

    local = []  # (줄 인덱스, 비교 키) — series 와 같은 유효 규칙(전부 0=에러 행 제외)
    for i, parts in enumerate(lines):
        try:
            ref_kh, tank_kh = float(parts[3]), float(parts[4])
            key = _series_key(parts[0], tank_kh, parts[5])
        except (IndexError, ValueError):
            continue
        if ref_kh == 0.0 and tank_kh == 0.0:
            continue
        local.append((i, key))

    for k in range(0, CO2_ALIGN_MAX_LAG + 1):
        cand = local[:len(local) - k] if k else local
        t = min(len(cand), len(s_keys))
        if t < CO2_ALIGN_MIN_OVERLAP:
            break
        if [key for _, key in cand[-t:]] == s_keys[-t:]:
            return {idx for (idx, _), row in zip(cand[-t:], series[-t:])
                    if row.get("co2_suspect")}
    log("[CO₂플래그] 원격 series 와 정렬 실패 — 제외 없이 계산")
    return None


def theil_sen_per_day(pts):
    """쌍별 기울기 중앙값(dKH/일). 행 간격은 8h 균일 가정."""
    slopes = [
        (kj - ki) / ((j - i) * ROW_DAYS)
        for a, (i, ki) in enumerate(pts)
        for (j, kj) in (pts[b] for b in range(a + 1, len(pts)))
    ]
    return statistics.median(slopes)


def compute(level, slope, cur_lrt, target=TARGET_DKH):
    """새 lrt와 계산 근거를 돌려준다. 적용 여부와 무관한 순수 계산(테스트 용이)."""
    error = target - level
    desired_rate = max(-DAILY_RATE_CAP, min(DAILY_RATE_CAP, error / APPROACH_DAYS))
    delta_rate = desired_rate - slope
    delta_ml = delta_rate / SENS                      # 원액 mL/일
    cur_ml = lrt_to_ml_day(cur_lrt)
    raw_lrt = ml_day_to_lrt(cur_ml + delta_ml)

    notes = []
    step_cap = cur_lrt * STEP_MAX_FRAC
    if abs(raw_lrt - cur_lrt) > step_cap:
        raw_lrt = cur_lrt + (step_cap if raw_lrt > cur_lrt else -step_cap)
        notes.append("스텝 ±30% 제한")
    if raw_lrt < LRT_MIN:
        raw_lrt, _ = LRT_MIN, notes.append(f"하한 {LRT_MIN}ms")
    elif raw_lrt > LRT_MAX:
        raw_lrt, _ = LRT_MAX, notes.append(f"상한 {LRT_MAX}ms")
    new_lrt = int(round(raw_lrt / 100.0) * 100)
    if abs(new_lrt - cur_lrt) < DEADBAND_MS:
        new_lrt = cur_lrt
        notes.append("데드밴드(<200ms) — 변경 없음")
    return {
        "error": round(error, 3),
        "desired_rate": round(desired_rate, 4),
        "delta_rate": round(delta_rate, 4),
        "delta_ml": round(delta_ml, 1),
        "new_lrt": new_lrt,
        "notes": notes,
    }


# ---------- 시리얼 (set_time.py 패턴: 2초 안정화, 입력버퍼 비움, LF만) ----------

LRT_RE = re.compile(r"왼쪽 동작\(RUN\) 시간 설정 값:\s*(\d+)")
LGT_RE = re.compile(r"왼쪽 휴지\(GAP\) 시간 설정 값:\s*(\d+)")


def open_doser():
    import serial
    ser = serial.Serial(PORT, BAUD, timeout=1, write_timeout=5)
    time.sleep(2)
    ser.reset_input_buffer()
    return ser


def send_cmd(ser, cmd, wait=3.0):
    """명령 한 줄 전송 후 wait초 동안 응답 라인 수집."""
    ser.write((cmd + "\n").encode())   # LF only — '\r' 붙으면 펌웨어가 실행 안 함
    lines, deadline = [], time.time() + wait
    while time.time() < deadline:
        if ser.in_waiting:
            ln = ser.readline().decode("utf-8", errors="replace").strip()
            if ln:
                lines.append(ln)
        else:
            time.sleep(0.05)
    return lines


def query_left(ser):
    """`ls`로 왼쪽 펌프 설정 (lrt_ms, lgt_min) 조회. 파싱 실패 시 (None, None)."""
    text = "\n".join(send_cmd(ser, "ls"))
    m_rt, m_gt = LRT_RE.search(text), LGT_RE.search(text)
    return (int(m_rt.group(1)) if m_rt else None,
            int(m_gt.group(1)) if m_gt else None)


def apply_lrt(ser, new_lrt, old_lrt, retries=3):
    """lrt 전송 → 저장값 에코 검증 → `refresh all` 로 타이머 반영 → ack 확인. 성공 True.

    lrt 만으로는 EEPROM/변수만 바뀌고 동작 타이머 인터벌은 그대로다(사용자 확인).
    refresh all 은 GAP 타이머도 리셋하므로 다음 도징이 지금 기준 ~4h 뒤로 재정렬된다.
    끝내 실패하면 EEPROM 만 새 값인 어중간한 상태(재부팅 시 미검증 반영)를 피하려고
    이전 값으로 best-effort 롤백한다.
    """
    for attempt in range(1, retries + 1):
        echo = "\n".join(send_cmd(ser, f"lrt {new_lrt}"))
        m = LRT_RE.search(echo)
        if m and int(m.group(1)) == new_lrt:
            ack = "\n".join(send_cmd(ser, "refresh all"))
            if "Refreshed all timers!" in ack:
                confirmed, _ = query_left(ser)
                if confirmed == new_lrt:
                    return True
            log(f"[재시도 {attempt}/{retries}] refresh all 확인 실패 | ack: {ack!r}")
        else:
            log(f"[재시도 {attempt}/{retries}] lrt {new_lrt} 에코 검증 실패 | echo: {echo!r}")
        time.sleep(1)
    rollback = "\n".join(send_cmd(ser, f"lrt {old_lrt}"))
    m = LRT_RE.search(rollback)
    log(f"[롤백] lrt {old_lrt} 복원 {'성공' if m and int(m.group(1)) == old_lrt else '실패(링크 사망?)'}")
    return False


# ---------- 수동 오버라이드 (대시보드 → GitHub → 여기) ----------

def fetch_repo_json(url, label):
    """저장소 파일을 GitHub API raw 로 읽는다. 없음(404=정상)/실패 = None."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.raw+json",
        "User-Agent": "reefwiz-doser-adjust",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log(f"[{label}] 조회 실패 HTTP {e.code}")
        return None
    except Exception as e:
        log(f"[{label}] 조회 실패: {e!r}")
        return None


def fetch_target():
    """대시보드가 저장한 목표 dKH(doser_config.json). 없음/형식 오류/범위 밖 = 기본값."""
    data = fetch_repo_json(CONFIG_URL, "목표설정")
    if data is None:
        return TARGET_DKH
    try:
        t = float(data["target_dkh"])
    except (KeyError, TypeError, ValueError):
        log(f"[목표설정] 형식 오류 무시: {data!r} — 기본 {TARGET_DKH} 사용")
        return TARGET_DKH
    if not (TARGET_LO <= t <= TARGET_HI):
        log(f"[목표설정] 범위({TARGET_LO}~{TARGET_HI}) 밖 {t} 무시 — 기본 {TARGET_DKH} 사용")
        return TARGET_DKH
    return t


def fetch_override():
    """docs/doser_override.json 을 GitHub API 로 읽는다. 없음/실패 = None."""
    data = fetch_repo_json(OVERRIDE_URL, "오버라이드")
    if data is None:
        return None
    try:
        ml = float(data["ml_day"])
        oid = str(data["id"])
    except (KeyError, TypeError, ValueError):
        log(f"[오버라이드] 형식 오류 무시: {data!r}")
        return None
    return {"ml_day": ml, "id": oid}


def load_applied_override_id():
    try:
        with open(OVERRIDE_STATE_FILE, encoding="utf-8") as f:
            return json.load(f).get("applied_id")
    except (OSError, ValueError):
        return None


def save_applied_override_id(oid):
    with open(OVERRIDE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"applied_id": oid, "applied_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}"}, f)


def apply_manual_override(ov):
    """사용자 지정 값(원액 mL/일)을 도저에 적용. 성공 시 상태 저장(재시도 방지).
    0 = 정지(lrt 0). 그 외는 plan_lrt 가 [LRT_MIN, LRT_MAX] 로 클램프(gap 불변)."""
    new_lrt, plan_note = plan_lrt(ov["ml_day"])
    note = "대시보드 " + ("정지(lrt 0)" if new_lrt == 0 else "수동 설정")
    if plan_note and new_lrt != 0:
        note += " | " + plan_note

    try:
        ser = open_doser()
    except Exception as e:
        log(f"[수동] {PORT} 연결 실패: {e} — 다음 측정 후 재시도")
        return False
    with ser:
        cur_lrt, cur_lgt = query_left(ser)
        if cur_lrt is None:
            log("[수동] ls 파싱 실패 — 다음 측정 후 재시도")
            return False
        applied = True if new_lrt == cur_lrt else apply_lrt(ser, new_lrt, cur_lrt)

    append_history({
        "ts": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "mode": "manual",
        "override_id": ov["id"],
        "requested_ml": ov["ml_day"],
        "lrt_old": cur_lrt,
        "lrt_new": new_lrt,
        "lgt_min": cur_lgt,
        "ml_day_old": round(lrt_to_ml_day(cur_lrt), 2),
        "ml_day_new": round(lrt_to_ml_day(new_lrt), 2),
        "applied": applied,
        "note": note if applied else note + " | 적용 실패 — 다음 측정 후 재시도",
    })
    if applied:
        save_applied_override_id(ov["id"])
    log(f"[수동] {ov['ml_day']}mL/일 요청(id={ov['id']}) → lrt {cur_lrt}→{new_lrt}ms 적용={applied}")
    return applied


# ---------- 이력 ----------

def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            h = json.load(f)
        return h if isinstance(h, list) else []
    except (OSError, ValueError):
        return []


def append_history(entry):
    history = load_history()
    history.append(entry)
    history = history[-HISTORY_MAX:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)


def computed_run_count(history):
    """권고 카운트 = 계산까지 성공한 실행 횟수(중단 abort는 제외)."""
    return sum(1 for e in history if e.get("mode") in ("advisory", "auto"))


# ---------- 메인 ----------

def record_abort(note):
    log(f"[중단] {note} — 도저 변경 없음")
    append_history({
        "ts": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "mode": "abort", "applied": False, "note": note,
    })


def main():
    if "--check" in sys.argv:
        with open_doser() as ser:
            lrt, lgt = query_left(ser)
        print(f"왼쪽 펌프: lrt={lrt}ms lgt={lgt}min "
              f"(≈{lrt_to_ml_day(lrt):.1f}mL 원액/일)" if lrt else "ls 파싱 실패")
        return

    if "--dry-run" in sys.argv:
        co2 = fetch_co2_excluded(read_dat_lines())  # 조회 실패=None → 제외 없이 폴백
        pts, n_co2 = read_recent_kh(co2_excluded=co2)
        co2_txt = ("CO₂ 플래그 조회 실패 — 제외 없음" if co2 is None
                   else f"CO₂ 의심 {n_co2}점 제외")
        if len(pts) < MIN_VALID:
            print(f"유효 측정 부족: {len(pts)}/{MIN_VALID} | {co2_txt}")
            return
        level = statistics.median(kh for _, kh in pts[-3:])
        slope = theil_sen_per_day(pts)
        cur_lrt = int(sys.argv[sys.argv.index("--lrt") + 1]) if "--lrt" in sys.argv else 8000
        target = fetch_target()  # 무접속=도저 미접속. 네트워크 실패 시 기본값이라 오프라인 OK
        r = compute(level, slope, cur_lrt, target)
        print(f"유효 {len(pts)}점 | {co2_txt} | 수준 {level:.3f} | 목표 {target} "
              f"| 추세 {slope:+.3f}/일 | 오차 {r['error']:+.3f}")
        print(f"목표 접근속도 {r['desired_rate']:+.4f}/일 → 필요 Δ {r['delta_rate']:+.4f}/일 "
              f"= 원액 {r['delta_ml']:+.1f}mL/일")
        print(f"lrt {cur_lrt} → {r['new_lrt']}ms "
              f"({lrt_to_ml_day(cur_lrt):.1f} → {lrt_to_ml_day(r['new_lrt']):.1f}mL 원액/일)"
              + (" | " + ", ".join(r["notes"]) if r["notes"] else ""))
        return

    # 1) 수동 오버라이드 — 매 측정 종료 후 확인, 아직 적용 안 한 id 면 적용하고 이번
    #    회차의 자동 조정은 생략(수동 우선). 실패 시 상태 미저장 → 다음 측정 후 재시도.
    ov = fetch_override()
    if ov and ov["id"] != load_applied_override_id():
        apply_manual_override(ov)
        return

    # 2) 정기 자동 조정 — 매일 13시 회차(래퍼가 --slot-adjust 부여)만
    if "--slot-adjust" not in sys.argv:
        return

    co2 = fetch_co2_excluded(read_dat_lines())
    pts, n_co2 = read_recent_kh(co2_excluded=co2)
    co2_note = ("CO₂ 플래그 조회 실패 — 제외 없이 계산" if co2 is None
                else f"CO₂ 의심 {n_co2}점 제외" if n_co2 else "")
    if n_co2 > CO2_EXCLUDE_MAX:
        record_abort(f"CO₂ 제외 과다({n_co2}점>{CO2_EXCLUDE_MAX}) — 판정기 점검 필요")
        return
    if len(pts) < MIN_VALID:
        record_abort(f"유효 측정 부족({len(pts)}/{MIN_VALID})"
                     + (f" | {co2_note}" if co2_note else ""))
        return

    level = statistics.median(kh for _, kh in pts[-3:])
    slope = theil_sen_per_day(pts)
    target = fetch_target()

    # 실전 실행: 장치 조회 → 계산 → (권고 or 적용) → 기록
    try:
        ser = open_doser()
    except Exception as e:
        record_abort(f"{PORT} 연결 실패: {e}")
        return
    with ser:
        cur_lrt, cur_lgt = query_left(ser)
        if cur_lrt is None:
            record_abort("ls 응답 파싱 실패(BT 순단?)")
            return

        r = compute(level, slope, cur_lrt, target)
        mode = ("advisory" if not AUTO_APPLY
                or computed_run_count(load_history()) < ADVISORY_RUNS else "auto")
        applied = False
        note = ", ".join(r["notes"])
        if co2_note:
            note = (note + " | " if note else "") + co2_note

        if mode == "auto" and r["new_lrt"] != cur_lrt:
            applied = apply_lrt(ser, r["new_lrt"], cur_lrt)
            if not applied:
                note = (note + " | " if note else "") + "적용 실패(에코 검증 불통) — 기존값 유지"
        elif mode == "advisory":
            note = (note + " | " if note else "") + "권고 모드(적용 안 함)"

    entry = {
        "ts": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "mode": mode,
        "level": round(level, 3),
        "slope_per_day": round(slope, 3),
        "target": target,
        "error": r["error"],
        "lrt_old": cur_lrt,
        "lrt_new": r["new_lrt"],
        "lgt_min": cur_lgt,
        "ml_day_old": round(lrt_to_ml_day(cur_lrt), 2),
        "ml_day_new": round(lrt_to_ml_day(r["new_lrt"]), 2),
        "applied": applied,
        "excluded_co2": n_co2,
        "note": note,
    }
    append_history(entry)
    log(f"[{mode}] 수준 {level:.3f} 목표 {target} 추세 {slope:+.3f}/일 | lrt {cur_lrt}→{r['new_lrt']}ms "
        f"(원액 {entry['ml_day_old']}→{entry['ml_day_new']}mL/일) | 적용={applied}"
        + (f" | {note}" if note else ""))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"[예외] {e!r} — 도저 변경 없음")
        sys.exit(1)
