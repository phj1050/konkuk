"""
건국대 도서관 SMUF: 원격 배정 확정(check-arrival) + 연장(extend) 프록시.

- 확정: rooms/{room_id}/check-arrival + {"methodCode": "GATE"}
- 연장: 아래 EXTEND_* 를 Network 캡처 후 채움 (비어 있으면 안내 응답만 반환)
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
# pyxis-auth-token (우선순위)
#  1) 환경 변수 PYXIS_AUTH_TOKEN
#  2) 프로젝트 폴더의 .env 파일 ( pip install python-dotenv 후 KEY=값 형식 )
#  3) 아래 문자열(비워 두지 말 것 — GitHub 에 올릴 때는 반드시 제거·.env 사용)
# 만료 시: .env 또는 Render 환경 변수만 갱신 (app.py 수정 불필요)
# ---------------------------------------------------------------------------
PYXIS_AUTH_TOKEN = (os.environ.get("PYXIS_AUTH_TOKEN") or "").strip() or "여기에_토큰_입력"

BASE = "https://library.konkuk.ac.kr/pyxis-api/1/api"

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)

CHECK_ARRIVAL_TEMPLATE = BASE + "/rooms/{room_id}/check-arrival"

# ---------------------------------------------------------------------------
# 연장 API — 도서관에서 [연장] 누를 때 Network 에 찍힌 Request URL / Payload 를 반영하세요.
# 템플릿에 {usage_id} 가 있으면 POST JSON 의 "usage_id" 로 치환합니다.
# 아직 모르면 빈 문자열로 두면 /extend 는 설정 방법을 JSON 으로 알려줍니다.
# ---------------------------------------------------------------------------
EXTEND_URL_TEMPLATE = ""
# 예시(가짜): f"{BASE}/seat-usages/{{usage_id}}/extend"
EXTEND_HTTP_METHOD = "POST"
# 연장 요청 본문. 캡처한 JSON 그대로 쓰거나, 필요 시 usage_id 만 넣는 형태로 수정.
EXTEND_PAYLOAD_TEMPLATE: dict | None = None
# None 이면 {"usage_id": ...} 를 그대로 도서관으로 전달(캡처한 키 이름에 맞게 프론트에서 보냄).


def _token_from_request(body: dict | None) -> str | None:
    """요청 헤더 또는 JSON 의 pyxis_auth_token. 없으면 None."""
    h = request.headers.get("X-Pyxis-Auth-Token") or request.headers.get("pyxis-auth-token")
    if h and str(h).strip():
        return str(h).strip()
    if body and isinstance(body, dict):
        t = body.get("pyxis_auth_token")
        if t is not None and str(t).strip():
            return str(t).strip()
    return None


def _effective_token(body: dict | None) -> str | None:
    t = _token_from_request(body)
    if t:
        return t
    env = (PYXIS_AUTH_TOKEN or "").strip()
    if env and env != "여기에_토큰_입력":
        return env
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


@app.route("/auto-confirm", methods=["POST"])
def auto_confirm():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}

    room_id = body.get("room_id")
    if room_id is None or (isinstance(room_id, str) and not str(room_id).strip()):
        return jsonify(
            {
                "success": False,
                "message": "열람실 번호(room_id)를 보내 주세요.",
                "status_code": None,
            }
        ), 400

    room_id_str = str(room_id).strip()
    url = CHECK_ARRIVAL_TEMPLATE.format(room_id=room_id_str)
    payload = {"methodCode": "GATE"}

    token = _effective_token(body)
    if not token:
        return jsonify(
            {
                "success": False,
                "message": "토큰이 없습니다. 서버 환경변수(.env)에 넣거나, 웹 화면의 토큰 칸에 붙여 넣으세요.",
                "status_code": None,
            }
        ), 400

    try:
        resp = requests.post(url, json=payload, headers=_headers(token), timeout=30)
    except requests.RequestException as exc:
        return jsonify(
            {
                "success": False,
                "message": f"도서관 서버 연결 실패: {exc}",
                "status_code": None,
            }
        ), 502

    result: dict = {}
    _attach_json(result, resp)
    code = 200 if result["success"] else (
        resp.status_code if resp.status_code >= 400 else 400
    )
    return jsonify(result), code


@app.route("/extend", methods=["POST"])
def extend_seat():
    if not EXTEND_URL_TEMPLATE or not str(EXTEND_URL_TEMPLATE).strip():
        return jsonify(
            {
                "success": False,
                "message": (
                    "연장 API URL 이 아직 설정되지 않았습니다. app.py 의 EXTEND_URL_TEMPLATE 에 "
                    "도서관 사이트에서 [연장] 클릭 시 Network 에 나온 Request URL 을 넣고, "
                    "필요하면 EXTEND_PAYLOAD_TEMPLATE 를 캡처한 본문으로 맞추세요."
                ),
                "status_code": None,
            }
        ), 501

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}

    usage_id = body.get("usage_id")
    url = EXTEND_URL_TEMPLATE.strip()
    if "{usage_id}" in url:
        if usage_id is None or (isinstance(usage_id, str) and not usage_id.strip()):
            return jsonify(
                {
                    "success": False,
                    "message": "연장 URL 에 {usage_id} 가 있으면 POST JSON 에 usage_id 가 필요합니다.",
                    "status_code": None,
                }
            ), 400
        url = url.format(usage_id=str(usage_id).strip())

    token = _effective_token(body)
    if not token:
        return jsonify(
            {
                "success": False,
                "message": "토큰이 없습니다. 서버 환경변수(.env)에 넣거나, 웹 화면의 토큰 칸에 붙여 넣으세요.",
                "status_code": None,
            }
        ), 400

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
            return jsonify(
                {"success": False, "message": f"지원하지 않는 EXTEND_HTTP_METHOD: {method}", "status_code": None}
            ), 500
    except requests.RequestException as exc:
        return jsonify(
            {
                "success": False,
                "message": f"도서관 서버 연결 실패: {exc}",
                "status_code": None,
            }
        ), 502

    result: dict = {}
    _attach_json(result, resp)
    code = 200 if result["success"] else (
        resp.status_code if resp.status_code >= 400 else 400
    )
    return jsonify(result), code


@app.route("/", methods=["GET"])
def root():
    """브라우저로 주소만 열었을 때도 동작 확인용."""
    return jsonify(
        {
            "ok": True,
            "hint": "GET /health 로 연장 설정 여부도 확인 가능",
            "extend_configured": bool(EXTEND_URL_TEMPLATE and EXTEND_URL_TEMPLATE.strip()),
        }
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "extend_configured": bool(EXTEND_URL_TEMPLATE and EXTEND_URL_TEMPLATE.strip())})


if __name__ == "__main__":
    # Render 등 클라우드는 PORT 환경 변수를 줌. 로컬은 기본 5000.
    _port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=_port, debug=True)
