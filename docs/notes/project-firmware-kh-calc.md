# project-firmware-kh-calc

> 펌웨어 dKH 차동 계산식과 ref_pH/tank_pH 상승=전극 드리프트 진단

---

펌웨어: `firmware/reefwiz_meter_m1/reefwiz_meter_m1.ino` (Arduino Nano, ATmega328P). README.md에 위치 명시.

**dKH 계산식 (`calcAndSaveKH`, 차동 방식):**
```c
deltaPH = refPH - tankPH;
tankDKH = refDKH * pow(10.0, -deltaPH);   // refDKH = EEPROM 앵커
```
- **dKH는 ΔpH(refPH-tankPH)에만 의존, 절대 pH는 수학적으로 상쇄됨.**
- `ref_kh`가 dkh.dat에서 매 행 고정인 이유 = EEPROM 기준 앵커(`REF_DKH_ADDR`, `calref`로 갱신). 측정값 아님.
- **★앵커 이력: 8.448은 측정 테스트용 임의값(정확값 아님, 사용자 6/29). 첫 실교정 = 2026-06-29 calref로 한나체커 7.000 기준 앵커=8.830 EEPROM 저장.** 검산: 8.830×10^(7.786−7.886=−0.1)=7.01 ✓. 교정 후 독립 검증 3행 6.957/6.935/6.993(평균 6.96, span0.065)=목표 충족. → 8.448→8.830 "+0.38 점프"는 드리프트/증발 아니라 임의값→첫 실교정일 뿐.
- pH는 단일 프로브(ADS1115+DFRobot_PH)가 참조수→수조수 순차 측정, `nernstPH()` 온도보상, slope/offset은 EEPROM 캘리 고정.

**ref_pH/tank_pH 단조 상승 진단 (확정):**
- 참조·수조 pH가 매 사이클 0.001 이내로 같이, 시간당 ~+0.01씩 매끈하게 상승 = **pH 유리전극 장기 드리프트**(막 비대칭전위 + KCl 기준전극 junction 노화). 공통모드 → 원인은 공통 프로브 1개. 수질 변화 아님.
- 참조수(고정 기준액)까지 똑같이 오르는 게 결정적 증거. 온도는 반대로 내려갔음.
- 가끔의 하강 리셋(예: 4.282→4.181)은 재캘리브레이션(`enterph`/`calph`/`exitph`)·전극 재안정화 이벤트.
- **dKH 신뢰도 영향 없음**: 차동식이 절대 pH 드리프트를 상쇄 → tank_kh 8.45 안정 유지. README도 "pH 프로브 반복성=전체 오차 97%"라 명시, 차동 설계가 이 드리프트 대응책.

**Why:** dkh.dat에서 ref_kh 고정·pH 단조상승이 정상인지 묻는 질문이 반복됨. 코드로 검증한 결론.

**How to apply:** "pH 왜 오르나" → 전극 드리프트로 설명, dKH는 무영향. 절대 pH 필요하거나 드리프트 과대 시 `calph` 재캘(pH4/7), KCl junction 점검, 장기적 프로브 교체. ΔpH 보존돼도 절대 pH가 ADS1115 입력범위/readPH 선형구간 벗어날 위험은 주기적 재캘로 방지. 관련: [project-measure-kh-script](project-measure-kh-script.md)
