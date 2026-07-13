#!/usr/bin/env python3
"""CO₂ 편향 의심 플래그(①플래그, 2026-07-13) 회귀 테스트.

★플래그 판정/전파/도저 제외 로직 변경 시 배포 *전* 항상 실행해 전부 PASS 확인.
실행: cd bin && python3 test_co2_flag.py   (WSL, 저장소 안 — 네트워크·장치 불필요)

검증 범위:
  [1] classify_co2_suspect 단위 — 임계·AND 결합·판정 불능 입력 안전성
  [2] 소급 검증 — 실제 docs/dkh_plateau_history.json 에서 기대 런만 True
      (2026-07-13 수동 분석과의 일치를 상설 테스트로 고정)
  [3] make_dkh_json (date,hh) 매칭 — 주입·결번 생략·--plateau 하위호환
  [4] sync_dkh_dat lazy 백필 — 필드 부여·upsert/42런 잘림 불변
  [5] doser_adjust 접미 정렬·제외 — k 오프셋, 폴백, 인덱스 간격 보존, 가드
  [6] 스모크 — 실제 docs/dkh_series.json + data/dkh.dat 로 정렬 성립

테스트 패치는 전부 in-memory(모듈 변수 교체·인자 주입)만 사용 — 소스 실전 상수 불변.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doser_adjust
import parse_plateau_log

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATEAU_JSON = os.path.join(REPO, "docs", "dkh_plateau_history.json")
SERIES_JSON = os.path.join(REPO, "docs", "dkh_series.json")
DAT_FILE = os.path.join(REPO, "data", "dkh.dat")

_passed = 0
_failed = 0


def check(name, cond, detail=''):
    global _passed, _failed
    mark = 'PASS' if cond else 'FAIL'
    if cond: _passed += 1
    else: _failed += 1
    print(f"   [{mark}] {name}" + (f" — {detail}" if detail and not cond else ''))


def mk_run(flat_n, net_mph, n_ref=None):
    """ref_flat_n=flat_n, ref 곡선 순변화=net_mph 인 합성 run dict."""
    n_ref = n_ref if n_ref is not None else (flat_n or 8)
    ref = [{"n": i + 1, "ph": 7.800, "elapsed": 9 + 39 * i} for i in range(n_ref)]
    if ref:
        ref[-1]["ph"] = round(7.800 + net_mph / 1000.0, 3)
    return {"run_started": "2026-07-13 05:00:02", "mode": "calkh", "completed": True,
            "tank": [], "ref": ref, "tank_flat_n": 40, "ref_flat_n": flat_n}


# ---------------------------------------------------------------- [1] 판정 단위
def test_classify():
    print("\n[1] classify_co2_suspect 단위")
    cases = [
        ("정상(8, +1)", 8, 1, False),
        ("편향 전형(33, -20)", 33, -20, True),
        ("경계 안(21, -7)", 21, -7, True),
        ("flat_n 경계 밖(20, -7)", 20, -7, False),
        ("AND: flat_n만 초과(24, +2)", 24, 2, False),
        ("AND: net만 하강(8, -5)", 8, -5, False),
        ("net 경계(21, -3)", 21, -3, True),
        ("net 경계 밖(21, -2)", 21, -2, False),
    ]
    for name, flat_n, net, want in cases:
        got, got_net = parse_plateau_log.classify_co2_suspect(mk_run(flat_n, net))
        check(name, got == want and got_net == net, f"got ({got}, {got_net})")

    got, net = parse_plateau_log.classify_co2_suspect(mk_run(None, -20, n_ref=30))
    check("ref_flat_n=None → False(net은 계산)", got is False and net == -20, f"({got},{net})")
    got, net = parse_plateau_log.classify_co2_suspect(mk_run(30, 0, n_ref=1))
    check("ref 1점 → (False, None)", got is False and net is None, f"({got},{net})")
    got, net = parse_plateau_log.classify_co2_suspect({"run_started": "x"})
    check("구형식(ref 없음) dict 안전", got is False and net is None, f"({got},{net})")
    got, net = parse_plateau_log.classify_co2_suspect({"ref": [{"bad": 1}, {"bad": 2}], "ref_flat_n": 30})
    check("ref 원소 형식 오류 안전", got is False and net is None, f"({got},{net})")


# ------------------------------------------------------- [2] 실데이터 소급 검증
# 분 단위(초는 런마다 00~02초로 흔들림) — 2026-07-13 수동 분석으로 확정한 7런
EXPECTED_SUSPECT = {
    "2026-07-05 21:00", "2026-07-06 05:00", "2026-07-08 05:00",
    "2026-07-08 13:00", "2026-07-11 05:00", "2026-07-12 05:00",
    "2026-07-13 05:00",
}


def test_retro():
    print("\n[2] 소급 검증 — 실제 dkh_plateau_history.json (2026-07-13 수동 분석 고정)")
    with open(PLATEAU_JSON) as f:
        runs = json.load(f)
    got = {r["run_started"][:16] for r in runs
           if parse_plateau_log.classify_co2_suspect(r)[0]}
    covered = {r["run_started"][:16] for r in runs}
    expected = EXPECTED_SUSPECT & covered  # 14일 롤링으로 오래된 런이 밀려나도 유효
    check(f"의심 런 집합 일치({len(expected)}건, 보관 {len(runs)}런)", got == expected,
          f"got-expected={sorted(got - expected)} expected-got={sorted(expected - got)}")
    check("기준 런이 보관분에 존재(테스트 유효성)", len(expected) >= 3,
          "14일 롤링으로 기준 런이 모두 밀려남 — EXPECTED_SUSPECT 갱신 필요")


# ------------------------------------------------- [3] make_dkh_json 매칭 주입
def run_make(dat_text, argv_extra, cwd):
    dat = os.path.join(cwd, "dkh.dat")
    with open(dat, "w") as f:
        f.write(dat_text)
    out_series = os.path.join(cwd, "series.json")
    out_latest = os.path.join(cwd, "latest.json")
    r = subprocess.run(
        [sys.executable, os.path.join(REPO, "bin", "make_dkh_json.py"), dat,
         "--json", out_latest, "--series-json", out_series] + argv_extra,
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    with open(out_series) as f:
        series = json.load(f)
    with open(out_latest) as f:
        latest = json.load(f)
    return series, latest


def test_make_json():
    print("\n[3] make_dkh_json --plateau (date,hh) 매칭")
    dat = ("05 7.8 7.7 8.83 7.219 28.8\n"   # git 이력 없음 → date 없음 → 매칭 불가 검증용
           "13 7.8 7.7 8.83 7.328 28.8\n")
    with tempfile.TemporaryDirectory() as td:
        # date 없는 행(git 이력 없음)은 매칭 불가 → 필드 생략
        hist = [dict(mk_run(33, -20), run_started="2026-07-08 05:00:02", co2_suspect=True, ref_net_mph=-20)]
        hp = os.path.join(td, "hist.json")
        with open(hp, "w") as f:
            json.dump(hist, f)
        series, latest = run_make(dat, ["--plateau", hp], td)
        check("date 없으면 매칭 불가 → 필드 생략",
              all("co2_suspect" not in r for r in series), json.dumps(series))
        # --plateau 미지정 = 현행과 동일(하위호환)
        series2, _ = run_make(dat, [], td)
        check("--plateau 미지정 시 현행 출력과 동일(필드 없음)",
              all("co2_suspect" not in r for r in series2))

    # date 매칭은 plateau_flags 단위 + 수동 주입 경로로 검증(같은 코드 경로,
    # git 이력 없는 임시 파일로는 date 복원이 안 되므로)
    import make_dkh_json
    with tempfile.TemporaryDirectory() as td:
        hp = os.path.join(td, "hist.json")
        hist = [
            dict(mk_run(33, -20), run_started="2026-07-08 05:00:02"),       # 필드 없음→재계산 True
            dict(mk_run(8, 2), run_started="2026-07-08 13:00:02", co2_suspect=False, ref_net_mph=2),
            dict(mk_run(8, 2), run_started="2026-07-08 21:00:02", co2_suspect=True, ref_net_mph=2),  # 기록 우선
            {"run_started": None, "ref": []},                                # 키 생성 불가 → 무시
        ]
        with open(hp, "w") as f:
            json.dump(hist, f)
        flags = make_dkh_json.plateau_flags(hp)
        check("필드 없는 런 재계산 True", flags.get(("2026-07-08", 5)) is True, str(flags))
        check("기록된 False 그대로", flags.get(("2026-07-08", 13)) is False, str(flags))
        check("기록값이 재계산보다 우선", flags.get(("2026-07-08", 21)) is True, str(flags))
        check("run_started 없는 항목 무시", len(flags) == 3, str(flags))
        check("plateau 파일 없음 → None(폴백)", make_dkh_json.plateau_flags(os.path.join(td, "no.json")) is None)

        rows = [{"line": 1, "hh": 5, "date": "2026-07-08", "tank_kh": 7.219},
                {"line": 2, "hh": 21, "date": "2026-07-09", "tank_kh": 7.5},
                {"line": 3, "hh": 13, "tank_kh": 7.3}]  # date 없음
        for r in rows:
            key = (r.get("date"), r["hh"])
            if key in flags:
                r["co2_suspect"] = flags[key]
        check("(date,hh) 일치 행 주입", rows[0].get("co2_suspect") is True)
        check("이력 결번 행 필드 생략", "co2_suspect" not in rows[1])
        check("date 없는 행 필드 생략", "co2_suspect" not in rows[2])


# ------------------------------------------------------ [4] sync lazy 백필
def test_sync_backfill():
    print("\n[4] sync_dkh_dat lazy 백필")
    import sync_dkh_dat
    with tempfile.TemporaryDirectory() as td:
        # 필드 없는 과거 2런 + 파싱 결과와 동일한 마지막 런(내용 무변경 상황)
        old1 = {k: v for k, v in mk_run(33, -20).items()}
        old1["run_started"] = "2026-07-12 05:00:02"
        old2 = {k: v for k, v in mk_run(8, 2).items()}
        old2["run_started"] = "2026-07-12 13:00:02"
        last = parse_plateau_log.parse_last_run(_mk_log("2026-07-12 21:00:02", 8, 2))
        hist_no_field = [old1, old2, {k: v for k, v in last.items()
                                      if k not in ("co2_suspect", "ref_net_mph")}]

        src = os.path.join(td, "measure_kh.log")
        with open(src, "w", encoding="utf-8") as f:
            f.write(_mk_log("2026-07-12 21:00:02", 8, 2))
        dst = os.path.join(td, "hist.json")
        with open(dst, "w") as f:
            json.dump(hist_no_field, f, ensure_ascii=False)

        orig_src, orig_dst = sync_dkh_dat.PLATEAU_SRC, sync_dkh_dat.PLATEAU_DST
        sync_dkh_dat.PLATEAU_SRC, sync_dkh_dat.PLATEAU_DST = src, dst
        try:
            changed = sync_dkh_dat.sync_plateau()
            with open(dst) as f:
                out = json.load(f)
            check("백필만 있어도 changed=True", changed is True)
            check("전 항목 co2_suspect 보유", all("co2_suspect" in r for r in out))
            check("백필 판정 정확(True/False)",
                  out[0]["co2_suspect"] is True and out[1]["co2_suspect"] is False,
                  json.dumps([r["co2_suspect"] for r in out]))
            check("upsert 유지(3런, run_started 순서)",
                  [r["run_started"] for r in out] == [r["run_started"] for r in hist_no_field])

            changed2 = sync_dkh_dat.sync_plateau()
            check("재실행(백필 완료+내용 동일) → changed=False", changed2 is False)

            # 42런 잘림 불변
            many = [dict(old2, run_started=f"2026-06-{d:02d} 05:00:02") for d in range(1, 31)] + out
            with open(dst, "w") as f:
                json.dump([{k: v for k, v in r.items() if k not in ("co2_suspect", "ref_net_mph")}
                           for r in many], f, ensure_ascii=False)
            sync_dkh_dat.sync_plateau()
            with open(dst) as f:
                out2 = json.load(f)
            check(f"MAX_RUNS({sync_dkh_dat.MAX_RUNS}) 잘림 불변", len(out2) <= sync_dkh_dat.MAX_RUNS,
                  f"{len(out2)}런")
        finally:
            sync_dkh_dat.PLATEAU_SRC, sync_dkh_dat.PLATEAU_DST = orig_src, orig_dst


def _mk_log(run_started, flat_n, net_mph):
    """measure_kh.log 형식의 합성 한 런(ref만 의미, tank는 2점)."""
    lines = [f"===== measure_kh_once V4 {run_started} =====", "[calkh] 시작"]
    lines += [f"[tank] 1회 pH:7.600 (윈도우 1/4, 9s)", f"[tank] 2회 pH:7.601 (윈도우 2/4, 48s)"]
    lines += ["[평탄] tank 2회 — span4=0≤2 AND net8=1≤1 → 평형 (pH 7.601)"]
    for i in range(flat_n):
        ph = 7.800 + (net_mph / 1000.0 if i == flat_n - 1 else 0.0)
        lines.append(f"[ref] {i + 1}회 pH:{ph:.3f} span4:1mpH net8:0mpH ({9 + 39 * i}s)")
    lines.append(f"[평탄] ref {flat_n}회 — span4=0≤2 AND net8=1≤1 → 평형 (pH {ph:.3f})")
    lines.append("[LOG] 21 7.800 7.601 8.830 7.500 29.0")
    return "\n".join(lines) + "\n"


# ------------------------------------------- [5] doser 접미 정렬·제외·가드
def dat_text(rows):
    return "".join(f"{hh:02d} 7.800 7.700 8.830 {kh} {temp}\n" for hh, kh, temp in rows)


def series_of(rows, suspect_idx=()):
    out = []
    for i, (hh, kh, temp) in enumerate(rows):
        kh = float(kh)
        if kh == 0.0:
            continue  # make_dkh_json 은 에러 행을 건너뜀
        r = {"hh": hh, "ref_kh": 8.83, "tank_kh": abs(kh), "temp": temp,
             "is_flat": kh >= 0, "date": "2026-07-13"}
        if i in suspect_idx:
            r["co2_suspect"] = True
        out.append(r)
    return out


def test_doser():
    print("\n[5] doser_adjust 접미 정렬·제외·가드")
    base = [(5, 7.2, 28.8), (13, 7.3, 29.0), (21, 7.4, 29.1),
            (5, 7.25, 28.9), (13, 7.35, 29.2), (21, 7.45, 29.0),
            (5, 7.22, 28.7), (13, 7.31, 29.1), (21, 7.41, 29.2),
            (5, 7.21, 28.8), (13, 7.33, 29.0), (21, 7.42, 29.1)]

    with tempfile.TemporaryDirectory() as td:
        dat = os.path.join(td, "dkh.dat")

        # k=1 (원격에 마지막 행 없음 — 실전 기본): 새벽(5시) 행 3개 플래그
        with open(dat, "w") as f:
            f.write(dat_text(base))
        lines = doser_adjust.read_dat_lines(dat)
        series = series_of(base[:-1], suspect_idx={0, 3, 6})
        excl = doser_adjust.fetch_co2_excluded(lines, series=series)
        check("k=1 정렬 성공", excl == {0, 3, 6}, str(excl))
        pts, n_co2 = doser_adjust.read_recent_kh(dat, rows=12, co2_excluded=excl)
        check("창 안 제외 수=3", n_co2 == 3, str(n_co2))
        check("제외 행이 pts 에서 빠짐", all(i not in (0, 3, 6) for i, _ in pts), str(pts))
        check("남은 행 인덱스 간격 보존(원위치 유지)",
              [i for i, _ in pts] == [1, 2, 4, 5, 7, 8, 9, 10, 11], str([i for i, _ in pts]))

        # k=0 (원격이 완전 최신)
        excl0 = doser_adjust.fetch_co2_excluded(lines, series=series_of(base, suspect_idx={9}))
        check("k=0 정렬 성공", excl0 == {9}, str(excl0))

        # k=3 (sync 두 사이클 실패 후)
        excl3 = doser_adjust.fetch_co2_excluded(lines, series=series_of(base[:-3], suspect_idx={0}))
        check("k=3 정렬 성공", excl3 == {0}, str(excl3))

        # 값 불일치 → None 폴백
        bad = series_of(base[:-1], suspect_idx={0})
        bad[2]["tank_kh"] = 9.999
        check("값 불일치 → None(제외 없이 폴백)",
              doser_adjust.fetch_co2_excluded(lines, series=bad) is None)
        # series 조회 실패/빈 배열 → None
        check("series=[] → None", doser_adjust.fetch_co2_excluded(lines, series=[]) is None)
        check("series 형식 오류 → None",
              doser_adjust.fetch_co2_excluded(lines, series=[{"x": 1}]) is None)

        # 에러 행(전부 0)이 로컬에 껴 있어도 정렬 유지(series 는 그 행이 없음)
        with_err = base[:6] + [(21, 0.0, 0.0)] + base[6:]
        with open(dat, "w") as f:
            f.write(dat_text(with_err).replace("21 7.800 7.700 8.830 0.0 0.0",
                                               "21 0.000 0.000 0.000 0.000 0.0"))
        lines_err = doser_adjust.read_dat_lines(dat)
        excl_err = doser_adjust.fetch_co2_excluded(lines_err, series=series_of(base[:-1], suspect_idx={6}))
        # base[6:] 는 에러 행 뒤라 로컬 줄 인덱스가 +1 밀림 → {7}
        check("에러 행 혼재 시 정렬·인덱스 보정", excl_err == {7}, str(excl_err))

        # 음수(미평탄) 행 — series 에는 abs 로 실림, 로컬 유효성에서는 탈락
        neg = base[:11] + [(21, -7.42, 29.1)]
        with open(dat, "w") as f:
            f.write(dat_text(neg))
        lines_neg = doser_adjust.read_dat_lines(dat)
        excl_neg = doser_adjust.fetch_co2_excluded(lines_neg, series=series_of(neg, suspect_idx={0}))
        check("음수(미평탄) 행 abs 매칭", excl_neg == {0}, str(excl_neg))

        # 수준(최근 3점 중앙값) 반영 — 마지막 3 유효점 중 1개 제외 시
        with open(dat, "w") as f:
            f.write(dat_text(base))
        pts_all, _ = doser_adjust.read_recent_kh(dat, rows=12)
        pts_ex, _ = doser_adjust.read_recent_kh(dat, rows=12, co2_excluded={9})
        import statistics
        lvl_all = statistics.median(kh for _, kh in pts_all[-3:])
        lvl_ex = statistics.median(kh for _, kh in pts_ex[-3:])
        check("수준 계산에 제외 반영", lvl_all != lvl_ex and lvl_ex == 7.41,
              f"all={lvl_all} ex={lvl_ex}")

        # MIN_VALID 가드: 12행 중 5개 제외 → 7점 < 10
        pts_few, n_few = doser_adjust.read_recent_kh(dat, rows=12, co2_excluded={0, 3, 6, 9, 1})
        check("제외 후 점수로 MIN_VALID 판단 가능", len(pts_few) == 7 and n_few == 5,
              f"{len(pts_few)}점/{n_few}제외")
        check("CO2_EXCLUDE_MAX 상수 존재(>9 중단 가드)", doser_adjust.CO2_EXCLUDE_MAX == 9)


# ------------------------------------------------------------- [6] 실데이터 스모크
def test_smoke():
    print("\n[6] 스모크 — 실제 dkh_series.json + data/dkh.dat 접미 정렬")
    if not (os.path.exists(SERIES_JSON) and os.path.exists(DAT_FILE)):
        check("실데이터 존재(스킵 아님)", False, "docs/dkh_series.json 또는 data/dkh.dat 없음")
        return
    with open(SERIES_JSON) as f:
        series = json.load(f)
    lines = doser_adjust.read_dat_lines(DAT_FILE)
    excl = doser_adjust.fetch_co2_excluded(lines, series=series)
    # 저장소의 dat 과 series 는 같은 커밋 계열이라 k=0 부근에서 정렬돼야 정상
    check("실데이터 정렬 성공(None 아님)", excl is not None, "정렬 실패")
    if excl is not None:
        pts, n_co2 = doser_adjust.read_recent_kh(DAT_FILE, co2_excluded=excl)
        print(f"      (실데이터: 창 21행 중 CO₂ 제외 {n_co2}점, 잔여 {len(pts)}점)")
        check("제외 후에도 MIN_VALID 충족", len(pts) >= doser_adjust.MIN_VALID,
              f"{len(pts)} < {doser_adjust.MIN_VALID}")


def main():
    test_classify()
    test_retro()
    test_make_json()
    test_sync_backfill()
    test_doser()
    test_smoke()
    print(f"\n결과: {_passed} PASS / {_failed} FAIL")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
