#!/usr/bin/env python3
"""dkh.dat 한 줄의 단일 파서·포매터 (의존성 없음 — Windows 배포본에도 그대로 복사).

형식 (공백 구분, 한 줄에 한 측정):
  ★신형식(2026-08-16~): YYYY-MM-DD HH ref_pH tank_pH ref_kh tank_kh temp
    2026-08-16 05 7.735 7.678 8.830 7.746 28.9
  구형식(그 이전):      HH ref_pH tank_pH ref_kh tank_kh temp
    05 7.735 7.678 8.830 7.746 28.9

날짜 컬럼을 넣은 이유: 종전에는 파일에 시각(HH)만 있어서 "최근 N일"을 하루 3회
측정 가정의 **회차 근사**로 셀 수밖에 없었다(측정이 빠진 날이 있으면 창이 과거로
늘어나고, 추가 측정을 돌린 날이 있으면 창 안쪽이 밀려나 잘렸다). 대시보드는 git
커밋 시각(git blame)으로, 도저는 원격 dkh_series.json 접미 정렬로 날짜를 각각
복원해 쓰고 있었는데, 둘 다 우회로였고 실패 시 근사로 폴백했다. 날짜를 원본에
적으면 모든 소비자가 같은 사실을 직접 읽는다.

두 형식을 모두 읽는다 — 구형식 행은 date=None. (저장소 data/dkh.dat 의 과거 행은
2026-08-16 에 backfill_dkh_dates.py 로 날짜를 채워 넣었다.)

특수 표식(값 규약은 종전과 동일):
  - 5개 값 전부 0.000 → 에러 표식(측정 실패/타임아웃/KCl 소크 실패)
  - tank_kh 가 음수   → 평탄(평형) 미도달. 크기는 유지되므로 abs() 로 값만 취한다
"""
import re

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FIELDS = ("hh", "ref_ph", "tank_ph", "ref_kh", "tank_kh", "temp")


def split_date(parts):
    """(날짜 문자열 또는 None, 날짜를 뺀 나머지 필드). 형식 판별의 단일 지점."""
    if parts and DATE_RE.match(parts[0]):
        return parts[0], parts[1:]
    return None, list(parts)


def parse_parts(parts):
    """이미 split() 된 한 줄 → dict. 필드 부족·형식 오류면 None.

    반환: {date(str|None), hh(int), ref_ph, tank_ph, ref_kh, tank_kh, temp(float),
           is_error(bool: 5개 값 전부 0), is_flat(bool: tank_kh 가 음수가 아님)}
    """
    day, rest = split_date(parts)
    if len(rest) < 6:
        return None
    try:
        row = {
            "date": day, "hh": int(rest[0]),
            "ref_ph": float(rest[1]), "tank_ph": float(rest[2]),
            "ref_kh": float(rest[3]), "tank_kh": float(rest[4]), "temp": float(rest[5]),
        }
    except ValueError:
        return None
    row["is_error"] = all(row[k] == 0.0 for k in
                          ("ref_ph", "tank_ph", "ref_kh", "tank_kh", "temp"))
    row["is_flat"] = row["tank_kh"] >= 0
    return row


def parse(line):
    """한 줄(문자열) → dict 또는 None."""
    return parse_parts(line.split())


def format_line(day, hour, ref_ph, tank_ph, ref_kh, tank_kh, temp):
    """한 줄 문자열(개행 없음). day=None 이면 구형식으로 쓴다(테스트·호환용)."""
    body = (f"{hour:02d} {ref_ph:.3f} {tank_ph:.3f} "
            f"{ref_kh:.3f} {tank_kh:.3f} {temp:.1f}")
    return f"{day} {body}" if day else body


def load(path):
    """파일 전체 → [dict] (파싱 실패 줄은 건너뜀). 각 행에 1-base 줄번호 line 포함."""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = parse(line)
            if row is not None:
                row["line"] = lineno
                rows.append(row)
    return rows
