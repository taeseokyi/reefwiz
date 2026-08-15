#!/usr/bin/env python3
"""dkh.dat 구형식(날짜 없는) 행에 측정 날짜를 채워 넣는다 — 1회성 마이그레이션.

2026-08-16 에 dkh.dat 형식이 `YYYY-MM-DD HH ...`(dkh_dat.py)로 바뀌었다. 그 이전
행에는 날짜가 없어 소비자들이 회차 근사·git blame·원격 정렬로 복원해 써 왔다.
이 스크립트가 과거 행의 날짜를 확정해 파일에 직접 적으면 그 우회로가 전부 사라진다.

날짜 출처(정확한 것부터):
  ① data/dkh_plateau_archive.jsonl 의 run_started — 측정 로그에서 파싱한 실제 측정
     시작 시각이라 근사가 아니다. 2026-07-01 이후 회차를 덮는다.
  ② git blame 커밋 시각 복원(make_dkh_json.git_line_dates) — 그 이전 행용. 행은
     측정 종료 후 커밋되므로 커밋일≈측정일이고, 자정 넘김·일괄 커밋 구간은
     "하루 안에서 HH는 증가한다"는 성질로 보정한다(과거로 갈수록 근사).

①과 ②가 겹치는 구간은 일치 여부를 보고한다 — 불일치가 많으면 ②의 신뢰도를
의심해야 한다는 신호다.

사용:
  python3 bin/backfill_dkh_dates.py data/dkh.dat            # 미리보기(변경 없음)
  python3 bin/backfill_dkh_dates.py data/dkh.dat --write    # 실제 기록(.bak 백업)
"""
import argparse
import collections
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dkh_dat
import make_dkh_json


def archive_days(archive_path):
    """평탄 아카이브 → [(날짜, HH)] 시간순. 같은 회차 중복은 한 번만."""
    if not os.path.exists(archive_path):
        return []
    seen, out = set(), []
    with open(archive_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                run = json.loads(line)
            except ValueError:
                continue
            rs = run.get("run_started") or ""
            if len(rs) < 13:
                continue
            key = (rs[:10], int(rs[11:13]))
            if key not in seen:
                seen.add(key)
                out.append(key)
    return sorted(out)


def resolve(rows, arc, git_dates):
    """행별 날짜를 정한다 → (날짜 리스트[행 순서], 출처 카운터, 교차검증 통계).

    아카이브는 (날짜,HH) 집합이라 행과 1:1 로 짝지으려면 순서가 필요하다. 행도
    아카이브도 시간순이므로, git 복원 날짜를 후보로 놓고 같은 (날짜,HH) 가
    아카이브에 있으면 확정(=교차검증 성공), 없으면 git 값을 그대로 쓴다.
    """
    arc_set = set(arc)
    arc_hh_by_day = collections.defaultdict(set)
    for d, hh in arc:
        arc_hh_by_day[d].add(hh)
    out, src = [], collections.Counter()
    agree = disagree = 0
    for r in rows:
        g = git_dates.get(r["line"])
        if g is None:
            out.append(None)
            src["미상"] += 1
            continue
        if (g, r["hh"]) in arc_set:
            src["아카이브 일치"] += 1
            agree += 1
        elif g >= min(d for d, _ in arc) if arc else False:
            # 아카이브가 덮는 기간인데 매칭이 없다 — git 복원이 틀렸을 수 있다
            src["아카이브 불일치(git 사용)"] += 1
            disagree += 1
        else:
            src["git 복원(아카이브 이전)"] += 1
        out.append(g)
    return out, src, (agree, disagree)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dat_file")
    ap.add_argument("--archive", default=None, help="평탄 아카이브 경로(기본: data/ 옆)")
    ap.add_argument("--write", action="store_true", help="실제로 파일을 고친다(.bak 백업)")
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    archive = args.archive or os.path.join(repo, "data", "dkh_plateau_archive.jsonl")

    raw = open(args.dat_file, encoding="utf-8", errors="replace").read().splitlines()
    rows = dkh_dat.load(args.dat_file)
    todo = [r for r in rows if not r["date"]]
    print(f"{args.dat_file}: {len(rows)}행 중 날짜 없는 행 {len(todo)}개")
    if not todo:
        print("고칠 것이 없습니다.")
        return

    dates_list = make_dkh_json.git_line_dates(args.dat_file)
    if not dates_list:
        sys.exit("git blame 복원 실패 — 날짜를 정할 수 없습니다(저장소 안에서 실행하세요).")
    git_dates = {i + 1: d for i, d in enumerate(dates_list)}

    arc = archive_days(archive)
    print(f"아카이브 회차: {len(arc)}개"
          + (f" ({arc[0][0]} ~ {arc[-1][0]})" if arc else " (없음)"))

    resolved, src, (agree, disagree) = resolve(todo, arc, git_dates)
    for k, v in src.items():
        print(f"  {k}: {v}행")
    if agree + disagree:
        print(f"  교차검증: 일치 {agree} / 불일치 {disagree} "
              f"({agree / (agree + disagree) * 100:.1f}% 일치)")

    missing = [r for r, d in zip(todo, resolved) if d is None]
    if missing:
        print(f"경고: 날짜를 못 정한 행 {len(missing)}개 — 그 행은 구형식으로 남깁니다.")

    new_lines = list(raw)
    changed = 0
    for r, d in zip(todo, resolved):
        if d is None:
            continue
        parts = raw[r["line"] - 1].split()
        new_lines[r["line"] - 1] = " ".join([d] + parts)
        changed += 1

    print(f"\n미리보기(앞 3 / 뒤 3):")
    for r in todo[:3] + todo[-3:]:
        print(f"  {raw[r['line'] - 1]}\n    → {new_lines[r['line'] - 1]}")

    if not args.write:
        print(f"\n[미리보기] {changed}행이 바뀝니다. 실제 기록은 --write")
        return

    backup = args.dat_file + ".bak"
    shutil.copy2(args.dat_file, backup)
    with open(args.dat_file, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")
    print(f"\n기록 완료: {changed}행 (백업 {backup})")

    # 되읽어 검증 — 값이 하나라도 바뀌면 즉시 알 수 있어야 한다
    after = dkh_dat.load(args.dat_file)
    same = all(
        (a["hh"], a["ref_ph"], a["tank_ph"], a["ref_kh"], a["tank_kh"], a["temp"])
        == (b["hh"], b["ref_ph"], b["tank_ph"], b["ref_kh"], b["tank_kh"], b["temp"])
        for a, b in zip(rows, after))
    print(f"검증: 행 수 {len(rows)}→{len(after)}, 측정값 동일 = {same}, "
          f"날짜 없는 행 {sum(1 for r in after if not r['date'])}개")
    if not (same and len(rows) == len(after)):
        sys.exit("★검증 실패 — 백업(.bak)으로 되돌리세요.")


if __name__ == "__main__":
    main()
