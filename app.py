"""
건국대 도서관 SMUF: 원격 배정 확정(check-arrival) + 연장(extend) 프록시.

- 매 요청마다 auto_login() 으로 토큰 발급 후 check-arrival / extend 호출
- 자격 증명: 환경 변수 LIBRARY_ID, LIBRARY_PW (.env 권장, GitHub 에 올리지 말 것)
- auth_method: "GPS" (고정 좌표) 또는 "RF_TAG" (NFC 고유번호) 선택 가능
"""
from __future__ import annotations

import os

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# .env 예시 (프로젝트 폴더에 .env 파일 생성, 한 줄씩):
#
#   LIBRARY_ID=건국대통합로그인아이디
#   LIBRARY_PW=비밀번호
#
# - 공백 없이 = 양쪽에 붙여 쓰기
# - 비밀번호에 # 이나 따옴표가 있으면 값 전체를 큰따옴표로 감싸기
# - Render: Dashboard → Environment → Add LIBRARY_ID, LIBRARY_PW
# ---------------------------------------------------------------------------

BASE = "https://library.konkuk.ac.kr/pyxis-api/1/api"

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)

LOGIN_URLS = (
    "https://library.konkuk.ac.kr/pyxis-api/api/login",
    "https://library.konkuk.ac.kr/pyxis-api/1/api/login",
)

CHECK_ARRIVAL_TEMPLATE = BASE + "/rooms/{room_id}/check-arrival"

# 건국대 상허기념도서관 위경도 (GPS 인증용 고정값)
LIBRARY_LATITUDE  = 37.5407
LIBRARY_LONGITUDE = 127.0793

EXTEND_URL_TEMPLATE = ""
EXTEND_HTTP_METHOD = "POST"
EXTEND_PAYLOAD_TEMPLATE: dict | None = None


def auto_login() -> tuple[str | None, str | None]:
    """
    도서관 로그인 API 호출 후 토큰 문자열 반환.
    성공: (token, None) / 실패: (None, 사람이 읽을 수 있는 메시지)
    """
    login_id = (os.environ.get("LIBRARY_ID") or "").strip()
    password = (os.environ.get("LIBRARY_PW") or "").strip()
    if not login_id or not password:
        return None, "LIBRARY_ID 또는 LIBRARY_PW 가 환경 변수(.env)에 설정되어 있지 않습니다."

    payload = {
        "loginId": login_id,
        "password": password,
        "isFamilyLogin": False,
        "isMobile": True,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    last_detail = ""

    for login_url in LOGIN_URLS:
        try:
            resp = requests.post(login_url, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            last_detail = f"{login_url}: 연결 실패 ({exc})"
            continue

        for key, val in resp.headers.items():
            if key.lower() == "pyxis-auth-token" and val and str(val).strip():
                return str(val).strip(), None

        try:
            body = resp.json()
        except ValueError:
            last_detail = f"{login_url}: HTTP {resp.status_code} (JSON 아님)"
            continue

        token = _extract_access_token_from_json(body)
        if token:
            return token, None

        last_detail = f"{login_url}: HTTP {resp.status_code} — {body}"

    return None, last_detail or "로그인 응답에서 pyxis-auth-token / accessToken 을 찾지 못했습니다."


def _extract_access_token_from_json(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    t = body.get("accessToken")
    if t and str(t).strip():
        return str(t).strip()
    data = body.get("data")
    if isinstance(data, dict):
        t = data.get("accessToken")
        if t and str(t).strip():
            return str(t).strip()
    return None


def _headers(token: str) -> dict[str, str]:
    return {
        "pyxis-auth-token": token,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }


def _attach_json(result: dict, resp: requests.Response) -> None:
    ok = resp.status_code in (200, 201)
    result["success"] = ok
    result["status_code"] = resp.status_code
    try:
        data = resp.json()
        result["data"] = data
        if isinstance(data, dict):
            if data.get("message") is not None:
                result["message"] = str(data["message"])
            elif "error" in data:
                result["message"] = str(data.get("error"))
            else:
                result["message"] = str(data)
        else:
            result["message"] = str(data)
    except ValueError:
        text = (resp.text or "").strip()
        result["data"] = None
        result["message"] = text[:2000] if text else f"본문 없음 (HTTP {resp.status_code})"

    if result.get("message") is None:
        result["message"] = "OK" if ok else f"HTTP {resp.status_code}"


def _build_checkarrival_payload(auth_method: str, nfc_serial: str | None) -> tuple[dict | None, str | None]:
    """
    auth_method 에 따라 check-arrival 페이로드를 생성.
    성공: (payload_dict, None) / 실패: (None, 오류 메시지)
    """
    method = auth_method.upper().strip()

    if method == "GPS":
        return {
            "methodCode": "GPS",
            "latitude": LIBRARY_LATITUDE,
            "longitude": LIBRARY_LONGITUDE,
        }, None

    elif method == "RF_TAG":
        serial = (nfc_serial or "").strip()
        if not serial:
            return None, "NFC(RF_TAG) 인증에는 nfc_serial(고유번호) 값이 필요합니다."
        return {
            "methodCode": "RF_TAG",
            "serialNo": serial,
        }, None

    elif method == "GATE":
        # 기존 방식: 좌석 상태가 안 바뀌는 경우가 있어 비권장이지만 호환을 위해 남겨 둠
        return {"methodCode": "GATE"}, None

    else:
        return None, (
            f"지원하지 않는 auth_method: '{auth_method}'. "
            "'GPS', 'RF_TAG', 'GATE' 중 하나를 선택하세요."
        )


@app.route("/auto-confirm", methods=["POST"])
def auto_confirm():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}

    # --- room_id 검증 ---
    room_id = body.get("room_id")
    if room_id is None or (isinstance(room_id, str) and not str(room_id).strip()):
        return jsonify({
            "success": False,
            "message": "열람실 번호(room_id)를 보내 주세요.",
            "status_code": None,
        }), 400

    # --- auth_method 결정 (기본값 GPS) ---
    auth_method = str(body.get("auth_method") or "GPS").strip()
    nfc_serial = body.get("nfc_serial") or body.get("serialNo")

    # --- 페이로드 조립 ---
    payload, payload_err = _build_checkarrival_payload(auth_method, nfc_serial)
    if payload is None:
        return jsonify({
            "success": False,
            "message": payload_err,
            "status_code": None,
        }), 400

    # --- 자동 로그인 ---
    token, err = auto_login()
    if not token:
        return jsonify({
            "success": False,
            "message": f"자동 로그인 실패: {err}",
            "status_code": None,
        }), 502

    # --- check-arrival 호출 ---
    room_id_str = str(room_id).strip()
    url = CHECK_ARRIVAL_TEMPLATE.format(room_id=room_id_str)

    try:
        resp = requests.post(url, json=payload, headers=_headers(token), timeout=30)
    except requests.RequestException as exc:
        return jsonify({
            "success": False,
            "message": f"도서관 서버 연결 실패: {exc}",
            "status_code": None,
        }), 502

    result: dict = {"auth_method_used": auth_method}
    _attach_json(result, resp)
    code = 200 if result["success"] else (
        resp.status_code if resp.status_code >= 400 else 400
    )
    return jsonify(result), code


@app.route("/extend", methods=["POST"])
def extend_seat():
    if not EXTEND_URL_TEMPLATE or not str(EXTEND_URL_TEMPLATE).strip():
        return jsonify({
            "success": False,
            "message": (
                "연장 API URL 이 아직 설정되지 않았습니다. app.py 의 EXTEND_URL_TEMPLATE 에 "
                "도서관 사이트에서 [연장] 클릭 시 Network 에 나온 Request URL 을 넣으세요."
            ),
            "status_code": None,
        }), 501

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}

    token, err = auto_login()
    if not token:
        return jsonify({
            "success": False,
            "message": f"자동 로그인 실패: {err}",
            "status_code": None,
        }), 502

    usage_id = body.get("usage_id")
    url = EXTEND_URL_TEMPLATE.strip()
    if "{usage_id}" in url:
        if usage_id is None or (isinstance(usage_id, str) and not usage_id.strip()):
            return jsonify({
                "success": False,
                "message": "연장 URL 에 {usage_id} 가 있으면 POST JSON 에 usage_id 가 필요합니다.",
                "status_code": None,
            }), 400
        url = url.format(usage_id=str(usage_id).strip())

    if EXTEND_PAYLOAD_TEMPLATE is not None:
        payload = dict(EXTEND_PAYLOAD_TEMPLATE)
    else:
        payload = dict(body)
        payload.pop("pyxis_auth_token", None)

    method = (EXTEND_HTTP_METHOD or "POST").upper()
    try:
        if method == "POST":
            resp = requests.post(url, json=payload, headers=_headers(token), timeout=30)
        elif method == "PATCH":
            resp = requests.patch(url, json=payload, headers=_headers(token), timeout=30)
        elif method == "PUT":
            resp = requests.put(url, json=payload, headers=_headers(token), timeout=30)
        else:
            return jsonify({
                "success": False,
                "message": f"지원하지 않는 EXTEND_HTTP_METHOD: {method}",
                "status_code": None,
            }), 500
    except requests.RequestException as exc:
        return jsonify({
            "success": False,
            "message": f"도서관 서버 연결 실패: {exc}",
            "status_code": None,
        }), 502

    result: dict = {}
    _attach_json(result, resp)
    code = 200 if result["success"] else (
        resp.status_code if resp.status_code >= 400 else 400
    )
    return jsonify(result), code


@app.route("/", methods=["GET"])
def root():
    creds = bool(
        (os.environ.get("LIBRARY_ID") or "").strip()
        and (os.environ.get("LIBRARY_PW") or "").strip()
    )
    return jsonify({
        "ok": True,
        "auto_login_configured": creds,
        "extend_configured": bool(EXTEND_URL_TEMPLATE and EXTEND_URL_TEMPLATE.strip()),
        "supported_auth_methods": ["GPS", "RF_TAG", "GATE"],
        "library_coords": {"latitude": LIBRARY_LATITUDE, "longitude": LIBRARY_LONGITUDE},
    })


@app.route("/health", methods=["GET"])
def health():
    creds = bool(
        (os.environ.get("LIBRARY_ID") or "").strip()
        and (os.environ.get("LIBRARY_PW") or "").strip()
    )
    return jsonify({
        "ok": True,
        "auto_login_configured": creds,
        "extend_configured": bool(EXTEND_URL_TEMPLATE and EXTEND_URL_TEMPLATE.strip()),
    })


if __name__ == "__main__":
    _port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=_port, debug=True)
