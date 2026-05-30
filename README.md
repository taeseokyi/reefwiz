# AquaWiz - 고정밀 pH/dKH 자동 측정 시스템

> **:warning: 경고:** 이 프로젝트의 하드웨어, 소프트웨어, 문서는 **어떠한 공식 검증도 거치지 않았습니다.** 측정 정확도, 전기적 안전성, 장기 신뢰성에 대한 보증이 없으며, 사용으로 발생하는 모든 결과(장비 손상, 생물 피해, 화재, 감전 등)에 대한 **책임은 전적으로 사용자 본인**에게 있습니다.

산호 수조(리프 탱크)를 위한 Arduino Nano 기반 **자동 경도(dKH) 측정기**입니다.
탄산염 화학법을 이용하여 참조 해수와 수조수의 pH 차이로부터 dKH를 자동 산출합니다.

![셋팅 구성](docs/images/setup-demo.svg)

### 주요 특징

- **[원버튼 자동 측정](docs/system-setup.md#자동-측정-시퀀스)** — 샘플링 → 폭기(CO2 평형) → pH 측정 → dKH 계산 → 정리까지 13단계 자동 시퀀스
- **[탄산염 화학법](#측정-원리)** — `KH_tank = KH_ref × 10^(-ΔpH)`, 참조수와 동시 탈기로 CO2 변수 제거
- **[16비트 고정밀 ADC](docs/parts-list.md)** — ADS1115 + 64회 오버샘플링으로 pH 0.001 단위 분해능
- **[Nernst 온도 보상](docs/user-manual.md#22-온도-오프셋-설정)** — DS18B20 실시간 수온 측정, pH 전극 온도 보정
- **[블루투스 제어](docs/user-manual.md)** — HC-06으로 스마트폰에서 원격 제어/모니터링
- **[밀폐 참조수](docs/system-setup.md#위즈-탱크)** — 락앤락 김치통으로 증발/오염 차단, 경도 장기 안정 유지
- **[3D 프린팅 하우징](docs/system-setup.md#하우징-3d-프린팅)** — OpenSCAD 파라메트릭 설계, 부품 실측 후 즉시 출력

## 한눈에 보기

| 시스템 구성도 | 호스 연결도 |
|:---:|:---:|
| [![구성도](docs/images/system-setup.svg)](docs/system-setup.md#시스템-구성도) | [![호스](docs/images/piping-diagram.svg)](docs/system-setup.md#호스-연결) |

| 회로도 (Fritzing) | 하우징 (3D 프린팅) |
|:---:|:---:|
| [![회로도](hardware/fritzing/고정밀%20ph%20측정기-bread_bb.png)](#회로도) | [![제어기](hardware/housing/controller-box.png)](docs/system-setup.md#하우징-3d-프린팅) [![펌프](hardware/housing/pump-air-box.png)](docs/system-setup.md#하우징-3d-프린팅) |

## 측정 원리

```
KH_tank = KH_ref × 10^(-ΔpH)
ΔpH = pH_ref − pH_tank
```

탈기 후 두 샘플의 CO2 농도가 동일해지면, pH 차이는 순수하게 알칼리니티(dKH) 차이만을 반영합니다.

## 하드웨어

| 부품 | 모델 | 역할 |
|------|------|------|
| MCU | Arduino Nano V3.0 (ATmega328P) | 메인 컨트롤러 |
| pH 센서 | DFRobot SEN0161-V2 | pH 전압 측정 |
| ADC | Adafruit ADS1115 (16-bit) | I2C 고정밀 ADC |
| 온도 센서 | DS18B20 (PTFE) | Nernst 온도 보상 |
| 블루투스 | HC-06 | 하드웨어 Serial (9600 baud) |
| 모터 드라이버 | L298N x 3 | 펌프 4개 + 솔레노이드 2개 |
| 도징 펌프 | Kamoer NKP-DC-B06S x 4 | 참조수/수조수 도징 |
| 솔레노이드 밸브 | x 2 | 참조/수조 에어 교대 공급 |
| 전원 | 12V DC + Buck Converter (5V, 6V) | 전원 공급 |

- 구매 링크 포함 상세 목록: [준비물 목록](docs/parts-list.md)
- 구성 요소 상세 / 펌프 역할: [자동화 환경 구성 — 구성 요소](docs/system-setup.md#구성-요소)
- 3D 프린팅 하우징: [자동화 환경 구성 — 하우징](docs/system-setup.md#하우징-3d-프린팅)

### 회로도

![브레드보드 회로도](hardware/fritzing/고정밀%20ph%20측정기-bread_bb.png)

Fritzing 소스: <a href="hardware/fritzing/고정밀%20ph%20측정기-bread.fzz" target="_blank">고정밀 ph 측정기-bread.fzz</a> | PDF: <a href="hardware/fritzing/고정밀%20ph%20측정기-bread_bb.pdf" target="_blank">브레드보드 도면</a>

**핀 배치 요약:**

```
D0  (RX)  ← HC-06 TX        D1  (TX)  → HC-06 RX (전압분배기)
D4~D7     → L298N1 IN1~IN4  D8~D11    → L298N2 IN1~IN4
D12       → SOL1 (참조 에어)  D13       → SOL2 (수조 에어)
A0  (D14) ← DS18B20 DQ      A4/A5     ↔ ADS1115 I2C
```

### 전원 / 접지

```
12V DC Jack
  ├── L298N1, L298N2 (모터 전원, 12V → 펌프)
  ├── Buck 12V→5V (Arduino, ADS1115, HC-06)
  └── Buck 12V→6V (XL4015 가변)
        ├── 도징 펌프 x4 (PWM 컨트롤러 경유)
        └── L298N3 (솔레노이드 전원, 6V)
접지: Star Ground Point (DGND/AGND 분리 후 한 점 결합)
```

## 빌드 및 업로드

| 라이브러리 | 제작자 |
|------------|--------|
| DFRobot_PH | DFRobot |
| Adafruit ADS1X15 | Adafruit |
| OneWire | Paul Stoffregen |
| DallasTemperature | Miles Burton |

1. Arduino IDE에서 [`firmware/aquawiz_ph_meter_final/aquawiz_ph_meter_final.ino`](firmware/aquawiz_ph_meter_final/aquawiz_ph_meter_final.ino) 열기
2. 보드: **Arduino Nano**, 프로세서: **ATmega328P** 선택
3. **HC-06을 D0/D1에서 분리** 후 업로드 → 완료 후 재연결

## 사용 방법

### 초기 설정

```
1. pH 2점 보정:   enterph → calph (pH7) → exitph → enterph → calph (pH4) → exitph
2. 온도 오프셋:   settemp:-0.3
3. 참조 dKH:      setref:8.5
```

### 자동 시퀀스 (원버튼 측정)

```
seq:settime:14|m3b:5|m1f:30|m4f:10|air:1800:5|ref|m4b:10|m2f:10|tank|calckh|m2b:10|m1b:30|m3f:5
```

각 단계의 상세 설명은 [자동화 환경 구성 — 측정 시퀀스](docs/system-setup.md#자동-측정-시퀀스)를 참조하세요.

### 주요 명령어

| 분류 | 명령어 |
|------|--------|
| pH 측정 | `ref`, `tank`, `calckh`, `status`, `khhist` |
| pH 보정 | `enterph`, `calph`, `exitph` |
| 설정 | `settime:HH`, `setref:x`, `settemp:x` |
| 모터 | `m1f:초`, `m1b:초`, `m1s` (m1~m4) |
| 에어 | `air:총초:주기초`, `airoff` |
| 시퀀스 | `seq:cmd1\|cmd2\|...`, `seqstop` |

전체 명령어 및 상세 설명은 [사용 설명서](docs/user-manual.md)를 참조하세요.

## 문서

| 문서 | 설명 |
|------|------|
| [자동화 환경 구성](docs/system-setup.md) | 구성 요소, 호스 연결, 측정 시퀀스, 하우징 |
| [사용 설명서](docs/user-manual.md) | 전체 명령어, 보정, 오류 메시지, 팁 |
| [준비물 목록](docs/parts-list.md) | 부품 사진, 구매 링크, 금액 |

## 산호 수조 권장 dKH 범위

| 유형 | dKH |
|------|-----|
| 자연 해수 | 6.5 ~ 7.5 |
| 산호 수조 권장 | 8 ~ 12 |
| SPS 경산호 최적 | 8 ~ 9 |
| LPS/소프트 코랄 | 7 ~ 11 |

## 라이선스

이 프로젝트는 개인 용도로 제작되었습니다.
