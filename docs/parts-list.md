# AquaWiz 준비물 목록

> 관련 문서: [프로젝트 개요 (README)](../README.md) | [자동화 환경 구성](system-setup.md) | [사용 설명서](user-manual.md)

## 디바이스마트

| # | 사진 | 부품명 | 수량 | 금액 |
|---|------|--------|------|------|
| 1 | ![](images/parts/dm-15315999.jpg) | <a href="https://www.devicemart.co.kr/goods/view?no=15315999" target="_blank">아두이노 나노 호환보드 V3.0 CH340 C타입</a> | 1 | 5,500원 |
| 2 | ![](images/parts/dm-14592629.jpg) | <a href="https://www.devicemart.co.kr/goods/view?no=14592629" target="_blank">Gravity: I2C ADS1115 16-Bit ADC Module</a> | 1 | 23,000원 |
| 3 | ![](images/parts/dm-14593211.jpg) | <a href="https://www.devicemart.co.kr/goods/view?no=14593211" target="_blank">Gravity: Analog pH Sensor/Meter Kit V2</a> | 1 | 57,500원 |
| 4 | ![](images/parts/dm-1278835.jpg) | <a href="https://www.devicemart.co.kr/goods/view?no=1278835" target="_blank">2A L298 모터드라이버 모듈 (아두이노 호환)</a> | 3 | 8,100원 |
| 5 | ![](images/parts/dm-1376882.jpg) | <a href="https://www.devicemart.co.kr/goods/view?no=1376882" target="_blank">블루투스 모듈 HC-06 (DIP) 펌웨어 v1.8</a> | 1 | 5,500원 |
| 6 | ![](images/parts/dm-1321161.jpg) | <a href="https://www.devicemart.co.kr/goods/view?no=1321161" target="_blank">5V 고정 출력 강하형 DC-DC 3A 컨버터</a> | 1 | 3,500원 |
| 7 | ![](images/parts/dm-1345967.jpg) | <a href="https://www.devicemart.co.kr/goods/view?no=1345967" target="_blank">PWM 12V 2A DC모터 속도 제어 컨트롤러</a> | 4 | 8,000원 |
| 9 | ![](images/parts/dm-1322408.jpg) | <a href="https://www.devicemart.co.kr/goods/view?no=1322408" target="_blank">브레드보드 830핀 MB-102</a> | 1 | 1,400원 |
| | | **디바이스마트 소계** | | **124,320원** |


## AliExpress

| # | 사진 | 부품명 | 수량 | 금액 |
|---|------|--------|------|------|
| 10 | ![](images/parts/ali-pump.png) | <a href="https://www.aliexpress.com/item/1005001888639071.html" target="_blank">연동 펌프 NKP-DC-B06B (12V, yellow)</a> | 4 | ₩21,680 |
| | | **AliExpress 소계** | | **₩21,680** |


## 별도 구매 필요 부품

### 전자부품

| # | 부품명 | 사양 | 수량 | 용도 |
|---|--------|------|------|------|
| 11 | DS18B20 방수 온도센서 | PTFE 케이블, 1-Wire | 1 | Nernst 온도 보상 (위즈 탱크 침수) |
| 12 | <a href="https://smartstore.naver.com/vim/products/4583133452" target="_blank">USB 5V 에어 펌프</a> | DC 5V, USB 전원 | 2 | 참조수/수조수 폭기 (L298N3 제어) |
| 13 | 세라믹 콘덴서 100nF (104) | 50V 이상 | 4 | C1: Arduino, C2: ADS1115 VDD, C3: 아날로그 입력 RC필터, C4: DS18B20 |
| 14 | 저항 330Ω (주황-주황-갈색-금) | 1/4W | 1 | ADS1115 아날로그 입력 RC 필터 직렬 저항 (R6) |
| 15 | 저항 4.7kΩ (노랑-보라-빨강-금) | 1/4W | 1 | DS18B20 풀업 저항 (R2) |
| 16 | 저항 10kΩ (갈색-검정-주황-금) | 1/4W | 1 | HC-06 TX 전압분배기 (R4) |
| 17 | 저항 20kΩ (빨강-검정-주황-금) | 1/4W | 1 | HC-06 TX 전압분배기 (R5) |
| 18 | 12V DC 어댑터 | 12V 3A 이상 | 1 | 전체 시스템 전원 |
| 19 | DC 잭 (배럴커넥터) | 5.5x2.1mm | 1 | 어댑터 연결용 |
| 20 | 점퍼 와이어 | M-M, M-F 혼합 | 1세트 | 브레드보드 배선 |

### 비전자 부품 (실험 환경)

| # | 부품명 | 사양 | 수량 | 용도 |
|---|--------|------|------|------|
| 21 | 실리콘 에어 스톤 | 수족관용 | 2 | 에어 펌프 출력단 기포 분산 |
| 22 | 실리콘 호스 | 내경 2~4mm | 적량 | 펌프/에어 연결 |
| 23 | 비이커 | 200ml | 3 | 수조물 / pH 측정 / 3M KCl |
| 24 | 위즈 탱크 용기 | 다이소 락앤락 김치통 (5L) | 1 | 참조 해수 + 비이커 수납 (아래 참고) |
| 25 | 3M KCl 용액 | - | 적량 | pH 프로브 저장수 (건조 방지) |
| 26 | pH 보정 버퍼 용액 | pH 4.0 / pH 7.0 | 각 1 | pH 2점 보정 |

## 총 합계

| 구매처 | 금액 |
|--------|------|
| 디바이스마트 | 124,320원 |
| AliExpress | 21,680원 |
| 별도 구매 | 미정 |
| **주문 합계** | **146,000원** |
