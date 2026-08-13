# reefCore 생태계 연동 — AquaWiz dKH 올리기

> 관련 문서: [프로젝트 개요 (README)](../README.md) | [자동화 구성](system-setup.md) | [측정 대장](measurement-ledger.md)

AquaWiz(reefWiz)가 측정한 **탄산경도(dKH)** 를 **reefCore 생태계**의 내 체커(reefChecker)에 측정값으로 올리는 연동입니다. 2026-06-23 메커니즘 검증·실측 성공.

> ⚠️ **자격증명·시크릿은 이 문서에 값으로 적지 않습니다.** 모두 환경변수명으로만 표기하며, 공개 저장소에 실제 값을 커밋하지 않습니다.

## 1. 생태계 구성

| 구성요소 | 역할 |
|---|---|
| **reefCore** | 허브/클라우드 (앱·계정·DB). 사이트 `reef.anih.net` (React SPA) |
| **reefChecker** | Hanna Checker HC 비색계 기반 ESPHome 기기 (dKH/Ca/Mg/NO3/PO4/pH 측정) |
| **reefWiz** | 본 프로젝트(AquaWiz). dKH를 위 생태계로 공급 |

## 2. 아키텍처

- **REST API**: `https://reefapi.anih.net`. 인증 `POST /auth/login {email, password}` → JWT(`Authorization: Bearer …`).
- **측정값 주입 경로**: 현재 채택 경로는 **기기가 MQTT로 발행 → 백엔드가 수집**. (정정 2026-06-25: 과거 "REST 생성 엔드포인트 없음"이라 적었으나, 실제로는 `POST /devices/{mac}/measurements {mode,value,unit,temp,measured_at,memo}`(ManualMeasurementRequest)가 **존재**한다. 이 경로는 retain 자체가 없어 유령 중복 위험이 없고 `measured_at` 을 명시할 수 있는 **더 깔끔한 대안**이나, REST 인증(JWT)이 필요하다. 현 conf 자격은 **MQTT 전용**(`/mqtt/auth` 로 검증)이라 `/auth/login` 에는 401 — 웹 계정 비번이 있어야 전환 가능. 미채택, 향후 옵션.)
- **MQTT 브로커**: `reef.anih.net:8883` (MQTT over TLS). 여러 기기가 쓰는 **공유 브로커**.
- **MQTT 인증**: reefCore **계정 자격** 사용 — username = 계정 이메일(`$REEFCORE_USER`), password = 계정 비번(`$REEFCORE_PASS`).
- **기기 등록**(참고): `POST reefapi.anih.net/devices/register {mac, email, device_type}`. `device_type` ∈ `reefcore`/`checker`/`ato`/`module`. 펌웨어에 박힌 등록 키(`$REEFCORE_REG_KEY`)로 인가하는 것으로 추정.

## 3. dKH 올리는 메커니즘 (검증됨)

체커는 ESPHome 표준 토픽으로 상태를 발행합니다:

```
reefcore-checker-<mac6>/sensor|select|switch|number/<엔티티>/state
```

이 중 **"최근 측정값" 센서 토픽**에 아래 형식의 요약 문자열을 발행하면, 백엔드가 이를 파싱해 `{mode, value, unit, temp, measured_at}` 측정 레코드를 생성합니다:

```
토픽   : reefcore-checker-<mac6>/sensor/________________/state
                                  └ 한글 엔티티명이 sanitize되어 언더스코어로 보임
페이로드: "<mode>: <value> <unit> | <temp>°C @ <YYYY-MM-DD HH:MM>"
예시    : "dKH: 8.43 dKH | 27.2°C @ 2026-06-23 13:39"
```

- 발행 시 **고유 client_id**(`reefwiz-*`)를 쓰면 체커의 MQTT 세션을 끊지 않습니다.
- 대상 체커 MAC은 `$REEFCORE_MAC`(기본값=내 체커)로 지정. 토픽의 `<mac6>`는 MAC 끝 6자리.

## 4. 구현 — 수동 발행 도구 `bin/reefcore_bridge.py`

> **★2026-08-12 변경: 측정 스크립트의 자동 발행 제거(사용자 지시).**
> 이전에는 `measure_kh_once.py` 가 `dkh.dat` 기록 직후 `publish_to_reefcore()` 로 측정당 1회
> 자동 발행했다. 지금은 그 코드(`publish_to_reefcore`·`_publishable`·`_reefcore_creds`)가
> **측정 소스에서 완전히 제거**됐다 — 측정은 `dkh.dat` 기록까지만 하고 reefCore 로 아무것도
> 보내지 않는다. `C:\dkh\reefcore.conf` 나 paho 설치 여부와도 무관해졌다.
> 발행이 필요하면 아래 브리지를 **수동으로** 실행한다.

`bin/reefcore_bridge.py` 가 같은 MQTT 발행을 1회 실행하는 독립 도구다(디버그·보충용).

- **best-effort**: 자격 미설정·paho 미설치·연결 실패 등에서 조용히/우아하게 끝난다.
- 발행은 **`retain=False`**·`qos=1`, 고유 client_id(`reefwiz-*`)라 체커 세션을 끊지 않는다.
  - ⚠️ **`retain=False` 가 중요하다.** 이 토픽은 단순 상태가 아니라 "수신 시 측정 레코드를 생성"하는 이벤트 토픽이라, `retain=True` 면 브로커가 보관한 옛 측정값을 **백엔드 재접속 때마다 재전달 → 유령 중복 레코드**를 만들 수 있다. 백엔드는 상시 접속(`/debug/state` 의 `mqtt_connected:true`)이라 `retain=False` 여도 발행이 정상 도달한다(2026-06-25 실측: retain=False 발행 후 `/debug/state` 의 '최근 측정값' 토픽이 즉시 갱신됨). 과거 `retain=True` 로 남았던 retained 메시지는 빈 retained 발행으로 클리어 완료.

자격증명은 **환경변수**(`$REEFCORE_USER`/`$REEFCORE_PASS`/`$REEFCORE_MAC`)로 준다.

## 5. 참고 — dKH 값 규약

`dkh.dat` 의 수조 dKH 는 **양수=정상 측정, 음수=평탄 미도달, 0=측정 에러**(V4 규약)다.
브리지로 수동 발행할 때는 이 규약을 아는 값만 올리는 편이 안전하다 — reefCore 백엔드가
음수/0 dKH 를 파싱·저장한다는 보장은 없다.

## 6. 보안 / 운영 주의

- **자격증명은 환경변수로만.** `$REEFCORE_USER`/`$REEFCORE_PASS`를 코드·문서·저장소에 값으로 남기지 않습니다(저장소 public).
- **브로커 인증서 만료**: 포트별로 인증서가 다르다. **웹(443)은 갱신됨**(Let's Encrypt YE2, ~2026-08-31)이나 **MQTT 브로커(8883)는 별개 인증서로 2026-05-03 만료** 상태(브로커가 갱신본을 안 물고 옛 인증서로 기동 중인 전형적 케이스). 따라서 브리지는 현재 `tls_verify=0`(CERT_NONE)로 접속. 운영자가 8883 브로커에 갱신 인증서 적용+리로드하면, conf 에 `tls_verify=1` 만 추가해 검증 ON(재배포 불요). ※KISTI 등 SSL 검사 게이트웨이(swg.*)는 보통 443만 가로채고 8883은 통과시키므로, 브라우저가 보는 "유효"는 웹(443) 인증서다.
- 공유 브로커라 구독 시 본인 체커 토픽(`reefcore-checker-<mac6>/#`)으로 한정하세요. (참고 2026-06-25: 브로커는 `POST /mqtt/auth`·`/mqtt/acl`·`/mqtt/superuser` HTTP 훅으로 인증/ACL을 백엔드에 위임하는 구조 — MAC 토픽 격리가 실제로 ACL로 강제될 여지가 있다(미검증). 다만 `GET /debug/state` 는 **인증 없이 전체 기기명 목록과 내 체커 전 토픽 상태를 노출**한다(자가 검증엔 유용했으나 정보 노출 면은 운영자 영역).)
