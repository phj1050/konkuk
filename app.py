from __future__ import annotations

import os
import re

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
CORS(app)

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

DEFAULT_LIBRARY_LATITUDE = float(os.environ.get("LIBRARY_LATITUDE", "37.539182674872"))
DEFAULT_LIBRARY_LONGITUDE = float(os.environ.get("LIBRARY_LONGITUDE", "127.074711902268"))

EXTEND_URL_TEMPLATE = ""
EXTEND_HTTP_METHOD = "POST"
EXTEND_PAYLOAD_TEMPLATE: dict | None = None


def auto_login() -> tuple[str | None, str | None]:
    login_id = (os.environ.get("LIBRARY_ID") or "").strip()
    password = (os.environ.get("LIBRARY_PW") or "").strip()
    if not login_id or not password:
        return None, "LIBRARY_ID or LIBRARY_PW is missing."

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
            last_detail = f"{login_url}: request failed ({exc})"
            continue

        for key, val in resp.headers.items():
            if key.lower() == "pyxis-auth-token" and val and str(val).strip():
                return str(val).strip(), None

        try:
            body = resp.json()
        except ValueError:
            last_detail = f"{login_url}: HTTP {resp.status_code} (non-JSON)"
            continue

        token = _extract_access_token_from_json(body)
        if token:
            return token, None

        last_detail = f"{login_url}: HTTP {resp.status_code} / {body}"

    return None, last_detail or "login response does not contain auth token"


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


def _to_float(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).strip())
    except ValueError:
        return None


def _build_serial_variants(raw_serial: str) -> list[str]:
    raw = (raw_serial or "").strip()
    if not raw:
        return []

    variants: list[str] = []

    def _add(v: str) -> None:
        v = (v or "").strip()
        if v and v not in variants:
            variants.append(v)

    _add(raw)

    # 69:15:B8:56:08:01:04:E0 -> 6915B856080104E0
    compact = re.sub(r"[^0-9A-Fa-f]", "", raw).upper()
    if compact:
        _add(compact)

    # Reverse byte order variant: 6915... -> E004...
    if compact and len(compact) % 2 == 0:
        bytes_list = [compact[i:i + 2] for i in range(0, len(compact), 2)]
        reversed_compact = "".join(reversed(bytes_list))
        _add(reversed_compact)

    return variants


def _infer_business_success(http_ok: bool, parsed: object | None, message: str | None) -> bool:
    if not http_ok:
        return False

    if isinstance(parsed, dict):
        # Some APIs return top-level success=true but data.success=false.
        # Prefer nested business result when available.
        nested = parsed.get("data")
        if isinstance(nested, dict):
            for key in ("success", "result", "ok"):
                if key in nested and isinstance(nested.get(key), bool):
                    return bool(nested[key])

        for key in ("success", "result", "ok"):
            if key in parsed and isinstance(parsed.get(key), bool):
                return bool(parsed[key])

        for scope in (nested, parsed):
            if not isinstance(scope, dict):
                continue
            status = scope.get("status")
            if isinstance(status, str):
                lowered = status.lower().strip()
                if lowered in {"fail", "failed", "error"}:
                    return False
                if lowered in {"ok", "success", "succeeded"}:
                    return True

    text = (message or "").strip()
    if not text:
        return True

    fail_patterns = (
        r"\uC815\uBCF4\uAC00\s*\uC5C6",
        r"\uC2E4\uD328",
        r"\uBD88\uAC00",
        r"\uC624\uB958",
        r"error",
        r"invalid",
        r"not\s*found",
        r"denied",
    )
    if any(re.search(pat, text, flags=re.IGNORECASE) for pat in fail_patterns):
        return False

    return True


def _attach_json(result: dict, resp: requests.Response) -> None:
    http_ok = resp.status_code in (200, 201)
    result["http_ok"] = http_ok
    result["status_code"] = resp.status_code
    parsed: object | None = None
    try:
        data = resp.json()
        parsed = data
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
        result["message"] = text[:2000] if text else f"No body (HTTP {resp.status_code})"

    result["success"] = _infer_business_success(http_ok, parsed, result.get("message"))

    if result.get("message") is None:
        result["message"] = "OK" if result["success"] else f"HTTP {resp.status_code}"


def _build_checkarrival_payload(auth_method: str, nfc_serial: str | None) -> tuple[dict | None, str | None]:
    return _build_checkarrival_payload_with_coords(
        auth_method=auth_method,
        nfc_serial=nfc_serial,
        latitude=DEFAULT_LIBRARY_LATITUDE,
        longitude=DEFAULT_LIBRARY_LONGITUDE,
    )


def _build_checkarrival_payload_with_coords(
    auth_method: str,
    nfc_serial: str | None,
    latitude: float,
    longitude: float,
) -> tuple[dict | None, str | None]:
    method = auth_method.upper().strip()

    if method == "GPS":
        return {
            "methodCode": "GPS",
            "latitude": latitude,
            "longitude": longitude,
        }, None

    if method == "RF_TAG":
        serial = (nfc_serial or "").strip()
        if not serial:
            return None, "nfc_serial is required for RF_TAG"
        return {
            "methodCode": "RF_TAG",
            "serialNo": serial,
        }, None

    if method == "GATE":
        return {"methodCode": "GATE"}, None

    return None, f"unsupported auth_method: {auth_method}"


@app.route("/auto-confirm", methods=["POST"])
def auto_confirm():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}

    room_id = body.get("room_id")
    if room_id is None or (isinstance(room_id, str) and not str(room_id).strip()):
        return jsonify({
            "success": False,
            "message": "room_id is required",
            "status_code": None,
        }), 400

    auth_method = str(body.get("auth_method") or "GPS").strip()
    auth_method_norm = auth_method.upper()
    nfc_serial = body.get("nfc_serial") or body.get("serialNo")
    debug = bool(body.get("debug"))

    latitude = _to_float(body.get("latitude"))
    longitude = _to_float(body.get("longitude"))
    if auth_method_norm == "GPS":
        if body.get("latitude") is not None and latitude is None:
            return jsonify({"success": False, "message": "latitude must be a number", "status_code": None}), 400
        if body.get("longitude") is not None and longitude is None:
            return jsonify({"success": False, "message": "longitude must be a number", "status_code": None}), 400
    if latitude is None:
        latitude = DEFAULT_LIBRARY_LATITUDE
    if longitude is None:
        longitude = DEFAULT_LIBRARY_LONGITUDE

    payload, payload_err = _build_checkarrival_payload_with_coords(
        auth_method=auth_method_norm,
        nfc_serial=nfc_serial,
        latitude=latitude,
        longitude=longitude,
    )
    if payload is None:
        return jsonify({
            "success": False,
            "message": payload_err,
            "status_code": None,
        }), 400

    token, err = auto_login()
    if not token:
        return jsonify({
            "success": False,
            "message": f"auto login failed: {err}",
            "status_code": None,
        }), 502

    room_id_str = str(room_id).strip()
    url = CHECK_ARRIVAL_TEMPLATE.format(room_id=room_id_str)

    serial_variants: list[str] = []
    if auth_method_norm == "RF_TAG":
        serial_variants = _build_serial_variants(str(nfc_serial or ""))
        if not serial_variants:
            return jsonify({
                "success": False,
                "message": "nfc_serial is required for RF_TAG",
                "status_code": None,
            }), 400
    else:
        serial_variants = [""]

    final_result: dict | None = None
    last_resp: requests.Response | None = None
    attempt_logs: list[dict[str, object]] = []

    for serial in serial_variants:
        request_payload = dict(payload)
        if auth_method_norm == "RF_TAG":
            request_payload["serialNo"] = serial

        try:
            resp = requests.post(url, json=[request_payload], headers=_headers(token), timeout=30)
        except requests.RequestException as exc:
            return jsonify({
                "success": False,
                "message": f"library API request failed: {exc}",
                "status_code": None,
            }), 502

        last_resp = resp
        result: dict = {"auth_method_used": auth_method_norm}
        _attach_json(result, resp)

        attempt_logs.append({
            "serialNo": serial if auth_method_norm == "RF_TAG" else None,
            "status_code": resp.status_code,
            "success": result.get("success"),
            "message": result.get("message"),
        })

        final_result = result
        if result.get("success") is True:
            break

    if final_result is None or last_resp is None:
        return jsonify({
            "success": False,
            "message": "unknown error: no response from library API",
            "status_code": None,
        }), 502

    if debug:
        final_result["debug"] = {
            "request_url": url,
            "request_payload": [payload],
            "response_content_type": last_resp.headers.get("Content-Type"),
            "gps_used": {"latitude": latitude, "longitude": longitude},
            "attempt_logs": attempt_logs,
        }
    elif auth_method_norm == "RF_TAG":
        final_result["attempted_serial_count"] = len(serial_variants)
        final_result["attempted_serials"] = serial_variants

    code = 200 if final_result["success"] else (last_resp.status_code if last_resp.status_code >= 400 else 400)
    return jsonify(final_result), code


@app.route("/extend", methods=["POST"])
def extend_seat():
    if not EXTEND_URL_TEMPLATE or not str(EXTEND_URL_TEMPLATE).strip():
        return jsonify({
            "success": False,
            "message": "EXTEND_URL_TEMPLATE is not configured.",
            "status_code": None,
        }), 501

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}

    token, err = auto_login()
    if not token:
        return jsonify({
            "success": False,
            "message": f"auto login failed: {err}",
            "status_code": None,
        }), 502

    usage_id = body.get("usage_id")
    url = EXTEND_URL_TEMPLATE.strip()
    if "{usage_id}" in url:
        if usage_id is None or (isinstance(usage_id, str) and not usage_id.strip()):
            return jsonify({
                "success": False,
                "message": "usage_id is required for this extend URL",
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
                "message": f"unsupported EXTEND_HTTP_METHOD: {method}",
                "status_code": None,
            }), 500
    except requests.RequestException as exc:
        return jsonify({
            "success": False,
            "message": f"library API request failed: {exc}",
            "status_code": None,
        }), 502

    result: dict = {}
    _attach_json(result, resp)
    code = 200 if result["success"] else (resp.status_code if resp.status_code >= 400 else 400)
    return jsonify(result), code


@app.route("/", methods=["GET"])
def root():
    creds = bool((os.environ.get("LIBRARY_ID") or "").strip() and (os.environ.get("LIBRARY_PW") or "").strip())
    return jsonify({
        "ok": True,
        "auto_login_configured": creds,
        "extend_configured": bool(EXTEND_URL_TEMPLATE and EXTEND_URL_TEMPLATE.strip()),
        "supported_auth_methods": ["GPS", "RF_TAG", "GATE"],
        "library_coords": {"latitude": DEFAULT_LIBRARY_LATITUDE, "longitude": DEFAULT_LIBRARY_LONGITUDE},
    })


@app.route("/health", methods=["GET"])
def health():
    creds = bool((os.environ.get("LIBRARY_ID") or "").strip() and (os.environ.get("LIBRARY_PW") or "").strip())
    return jsonify({
        "ok": True,
        "auto_login_configured": creds,
        "extend_configured": bool(EXTEND_URL_TEMPLATE and EXTEND_URL_TEMPLATE.strip()),
    })


if __name__ == "__main__":
    _port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=_port, debug=True)
