#!/usr/bin/env python3
r"""AquaWiz BT 포트 단일 설정 로더.

포트가 바뀌면(윈도우 업데이트 BT 재열거·동글 교체·재페어링 등) 소스 코드를
고치지 말고 **설정 파일 하나만** 고친다. measure_kh_once / doser_adjust /
bt_health / set_time 이 전부 이 로더로 포트를 읽으므로 한 곳만 바꾸면 반영된다.

설정 파일 탐색 순서(먼저 읽히는 것 사용):
  1) 환경변수 AQUAWIZ_BT_CONFIG 가 가리키는 경로
  2) C:\dkh\bt_config.json   ← 윈도우 실전 위치. 코드 배포(cp bin→work)로 안 덮이게
                               일부러 work\ 바깥(로그들과 같은 C:\dkh\)에 둔다.
  3) 이 모듈과 같은 폴더의 bt_config.json  ← 저장소/시뮬레이터(WSL)용
설정을 하나도 못 읽으면 _FALLBACK 으로 안전하게 동작한다(측정이 import 단계에서
죽지 않도록). 즉 설정 파일이 없어도 최소한 마지막으로 알려진 포트로는 돈다.

설정 파일 형식(JSON):
  {
    "devices": {
      "measure": {"port": "COM10", "mac": "98DA600FC57A", "desc": "측정기 HC-06"},
      "doser":   {"port": "COM11", "mac": "98DA60056895", "desc": "도저 ca_reactor"}
    }
  }
포트만 바꾸면 되고 mac/desc 는 참고·확인용(MAC→COM 매핑은
`Get-PnpDevice -Class Ports -PresentOnly` 의 InstanceId 끝 MAC 으로 확인).

진단: `python -c "import bt_config; print(bt_config.all_ports(), bt_config.config_path())"`
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

_CANDIDATES = [
    os.environ.get("AQUAWIZ_BT_CONFIG"),
    r"C:\dkh\bt_config.json",
    os.path.join(_HERE, "bt_config.json"),
]

# 설정 파일을 하나도 못 읽을 때의 최후 폴백(2026-07-21 CSR USB 동글 기준).
_FALLBACK = {"measure": "COM10", "doser": "COM11"}


def _load():
    """(설정 dict, 사용한 경로) 반환. 하나도 못 읽으면 (None, None)."""
    for path in _CANDIDATES:
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f), path
        except (OSError, ValueError):
            continue
    return None, None


def get_port(key):
    """장치 key('measure'|'doser')의 COM 포트를 반환.

    설정 파일 우선, 못 읽거나 값이 비면 _FALLBACK. 폴백에도 없는 key 는 KeyError.
    """
    data, _ = _load()
    if data:
        try:
            port = data["devices"][key]["port"]
            if port:
                return port
        except (KeyError, TypeError):
            pass
    return _FALLBACK[key]


def config_path():
    """현재 실제로 읽히는 설정 파일 경로(진단용). 없으면 None(=폴백 사용 중)."""
    return _load()[1]


def all_ports():
    """{key: port} 전체 매핑(진단/검증용)."""
    return {k: get_port(k) for k in _FALLBACK}
