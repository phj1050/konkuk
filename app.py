from __future__ import annotations

import os
import re
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
CORS(app)

PYXIS_API_ROOT = "https://library.konkuk.ac.kr/pyxis-api"

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)

LOGIN_URLS = (
    "https://library.konkuk.ac.kr/pyxis-api/api/login",
    "https://library.konkuk.ac.kr/pyxis-api/1/api/login",
)

DEFAULT_LIBRARY_LATITUDE = float(os.environ.get("LIBRARY_LATITUDE", "37.539182674872"))
DEFAULT_LIBRARY_LONGITUDE = float(os.environ.get("LIBRARY_LONGITUDE", "127.074711902268"))

EXTEND_URL_TEMPLATE = ""
EXTEND_HTTP_METHOD = "POST"
EXTEND_PAYLOAD_TEMPLATE: dict | None = None
KST = ZoneInfo("Asia/Seoul")
DEFAULT_HOMEPAGE_IDS = [1, 2, 3, 4, 5, 6, 7, 8, 9]


def auto_login_session() -> tuple[str | None, requests.Session | None, str | None]:
    login_id = (os.environ.get("LIBRARY_ID") or "").strip()
    password = (os.environ.get("LIBRARY_PW") or "").strip()
    if not login_id or not password:
        return None, None, "LIBRARY_ID or LIBRARY_PW is missing."

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

    session = requests.Session()

    for login_url in LOGIN_URLS:
        try:
            resp = session.post(login_url, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            last_detail = f"{login_url}: request failed ({exc})"
            continue

        for key, val in resp.headers.items():
            if key.lower() == "pyxis-auth-token" and val and str(val).strip():
                return str(val).strip(), session, None

        try:
            body = resp.json()
        except ValueError:
            last_detail = f"{login_url}: HTTP {resp.status_code} (non-JSON)"
            continue

        token = _extract_access_token_from_json(body)
        if token:
            return token, session, None

        last_detail = f"{login_url}: HTTP {resp.status_code} / {body}"

    return None, None, last_detail or "login response does not contain auth token"


def auto_login() -> tuple[str | None, str | None]:
    token, _session, err = auto_login_session()
    return token, err


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


def _build_base(homepage_id: int) -> str:
    return f"{PYXIS_API_ROOT}/{homepage_id}/api"


def _check_arrival_url(homepage_id: int, room_id: str) -> str:
    return _build_base(homepage_id) + f"/rooms/{room_id}/check-arrival"


def _seat_charges_url(homepage_id: int) -> str:
    return _build_base(homepage_id) + "/seat-charges"


def _to_float(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).strip())
    except ValueError:
        return None


def _to_bool(val: object, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    text = str(val).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_homepage_ids(raw: object) -> list[int]:
    if raw is None:
        return list(DEFAULT_HOMEPAGE_IDS)
    tokens: list[str] = []
    if isinstance(raw, list):
        tokens = [str(x).strip() for x in raw]
    else:
        tokens = [s.strip() for s in str(raw).split(",")]

    out: list[int] = []
    for t in tokens:
        if not t:
            continue
        try:
            num = int(t)
        except ValueError:
            continue
        if num > 0 and num not in out:
            out.append(num)
    return out or list(DEFAULT_HOMEPAGE_IDS)


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


def _collect_key_values(node: object, target_key: str) -> list[object]:
    found: list[object] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == target_key:
                found.append(v)
            found.extend(_collect_key_values(v, target_key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_key_values(item, target_key))
    return found


def _dedupe_preserve(values: list[object]) -> list[object]:
    result: list[object] = []
    seen: set[str] = set()
    for v in values:
        key = repr(v)
        if key in seen:
            continue
        seen.add(key)
        result.append(v)
    return result


def _parse_kst_datetime(text: object) -> datetime | None:
    if text is None:
        return None
    try:
        dt = datetime.strptime(str(text).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=KST)


def _extract_active_charge(data: object) -> dict | None:
    # Some responses are wrapped as:
    # {success, code, message, data:{totalCount, list:[...]}}
    if isinstance(data, dict):
        maybe_inner = data.get("data")
        if isinstance(maybe_inner, (dict, list)):
            data = maybe_inner

    items: list[object] | None = None
    if isinstance(data, dict):
        maybe_list = data.get("list")
        if isinstance(maybe_list, list):
            items = maybe_list
        else:
            # Some responses may already be a charge dict or use a different key.
            for key in ("seatCharges", "charges", "items"):
                alt = data.get(key)
                if isinstance(alt, list):
                    items = alt
                    break
    elif isinstance(data, list):
        items = data

    if not isinstance(items, list):
        return None

    typed = [it for it in items if isinstance(it, dict)]
    if not typed:
        return None

    checkinable = [it for it in typed if bool(it.get("isCheckinable"))]
    candidates = checkinable if checkinable else typed

    def _score(item: dict) -> tuple:
        expiry = _parse_kst_datetime(item.get("checkinExpiryDate"))
        created = _parse_kst_datetime(item.get("dateCreated"))
        return (
            1 if bool(item.get("isCheckinable")) else 0,
            expiry or datetime.min.replace(tzinfo=KST),
            created or datetime.min.replace(tzinfo=KST),
        )

    candidates.sort(key=_score, reverse=True)
    return candidates[0]


def _fetch_seat_context(
    session: requests.Session,
    token: str,
    homepage_ids: list[int],
) -> tuple[dict | None, str | None]:
    traces: list[dict[str, object]] = []

    for homepage_id in homepage_ids:
        url = _seat_charges_url(homepage_id)
        try:
            resp = session.get(url, headers=_headers(token), timeout=30)
        except requests.RequestException as exc:
            traces.append({"homepage_id": homepage_id, "ok": False, "error": str(exc)})
            continue

        wrapped: dict = {}
        _attach_json(wrapped, resp)
        data = wrapped.get("data")
        data_inner = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)) else data
        total_count: int | None = None
        if isinstance(data_inner, dict):
            if isinstance(data_inner.get("totalCount"), int):
                total_count = int(data_inner.get("totalCount"))
            elif isinstance(data_inner.get("list"), list):
                total_count = len(data_inner.get("list"))
        elif isinstance(data_inner, list):
            total_count = len(data_inner)
        traces.append({
            "homepage_id": homepage_id,
            "ok": bool(wrapped.get("success")),
            "status_code": wrapped.get("status_code"),
            "total_count": total_count,
            "message": wrapped.get("message"),
            "data_type": type(data_inner).__name__,
            "data_keys": list(data_inner.keys())[:20] if isinstance(data_inner, dict) else None,
        })

        if not wrapped.get("success"):
            continue

        active = _extract_active_charge(data_inner)
        if active:
            return {
                "wrapped": wrapped,
                "active": active,
                "homepage_id": homepage_id,
                "traces": traces,
                "seat_charges_url": url,
            }, None

    return None, (
        "no active reservation found in seat-charges. "
        "Possible causes: account mismatch (LIBRARY_ID/PW), expired reservation, or different API shape. "
        f"(traces={traces})"
    )


def _scan_seat_charges(session: requests.Session, token: str, homepage_ids: list[int]) -> list[dict]:
    rows: list[dict] = []
    for homepage_id in homepage_ids:
        url = _seat_charges_url(homepage_id)
        row: dict[str, object] = {"homepage_id": homepage_id, "url": url}
        try:
            resp = session.get(url, headers=_headers(token), timeout=30)
        except requests.RequestException as exc:
            row["request_error"] = str(exc)
            rows.append(row)
            continue

        wrapped: dict = {}
        _attach_json(wrapped, resp)
        row["status_code"] = wrapped.get("status_code")
        row["success"] = wrapped.get("success")
        row["message"] = wrapped.get("message")
        data = wrapped.get("data")
        data_inner = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)) else data
        row["data_type"] = type(data_inner).__name__
        if isinstance(data_inner, dict):
            row["data_keys"] = list(data_inner.keys())[:30]
            maybe_list = data_inner.get("list")
            row["list_len"] = len(maybe_list) if isinstance(maybe_list, list) else None
            if isinstance(maybe_list, list) and maybe_list and isinstance(maybe_list[0], dict):
                row["first_item_keys"] = list(maybe_list[0].keys())[:30]
                row["first_item"] = maybe_list[0]
        elif isinstance(data_inner, list):
            row["list_len"] = len(data_inner)
            if data_inner and isinstance(data_inner[0], dict):
                row["first_item_keys"] = list(data_inner[0].keys())[:30]
                row["first_item"] = data_inner[0]

        rows.append(row)
    return rows


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

    auth_method = str(body.get("auth_method") or "GPS").strip()
    auth_method_norm = auth_method.upper()
    nfc_serial = body.get("nfc_serial") or body.get("serialNo")
    debug = bool(body.get("debug"))
    use_active_context = _to_bool(body.get("use_active_context"), default=True)
    homepage_ids = _parse_homepage_ids(body.get("homepage_ids") or os.environ.get("HOMEPAGE_IDS"))
    room_id_input = body.get("room_id")
    room_id_input_str = str(room_id_input).strip() if room_id_input is not None else ""

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

    token, session, err = auto_login_session()
    if not token or session is None:
        return jsonify({
            "success": False,
            "message": f"auto login failed: {err}",
            "status_code": None,
        }), 502

    seat_ctx, seat_ctx_err = _fetch_seat_context(session, token, homepage_ids=homepage_ids)
    active_charge: dict | None = None
    active_homepage_id: int | None = None
    active_room_id: object | None = None
    allowed_methods: list[str] = []
    expiry_text: object | None = None

    if seat_ctx:
        active_charge = seat_ctx["active"]
        active_homepage_id = int(seat_ctx["homepage_id"])
        active_room_id = (
            active_charge.get("room", {}).get("id")
            if isinstance(active_charge.get("room"), dict)
            else None
        )
        allowed_methods_raw = active_charge.get("arrivalConfirmMethods")
        if isinstance(allowed_methods_raw, list):
            allowed_methods = [str(m).strip().upper() for m in allowed_methods_raw if str(m).strip()]
        expiry_text = active_charge.get("checkinExpiryDate")
        expiry_dt = _parse_kst_datetime(expiry_text)
        now_kst = datetime.now(KST)
        if expiry_dt and now_kst > expiry_dt:
            return jsonify({
                "success": False,
                "message": f"check-in expired at {expiry_text} (KST)",
                "status_code": 400,
                "active_room_id": active_room_id,
            }), 400

    if use_active_context and active_room_id is not None:
        room_id_str = str(active_room_id).strip()
    elif room_id_input_str:
        room_id_str = room_id_input_str
    elif active_room_id is not None:
        room_id_str = str(active_room_id).strip()
    else:
        return jsonify({
            "success": False,
            "message": (seat_ctx_err or "room_id is missing and no active room found from seat-charges")
            + " / You can retry with room_id manually and uncheck active-context.",
            "status_code": None,
            "homepage_ids_tried": homepage_ids,
        }), 400

    if allowed_methods and auth_method_norm not in allowed_methods:
        return jsonify({
            "success": False,
            "message": f"{auth_method_norm} is not allowed for active reservation",
            "status_code": 400,
            "allowed_methods": allowed_methods,
            "active_room_id": active_room_id,
        }), 400

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

    homepage_candidates: list[int]
    if active_homepage_id is not None:
        homepage_candidates = [active_homepage_id]
    else:
        homepage_candidates = homepage_ids

    for homepage_id in homepage_candidates:
        url = _check_arrival_url(homepage_id, room_id_str)
        for serial in serial_variants:
            request_payload = dict(payload)
            if auth_method_norm == "RF_TAG":
                request_payload["serialNo"] = serial

            payload_shapes: list[tuple[str, object]] = [
                ("object", request_payload),
                ("array", [request_payload]),
            ]

            for payload_shape, post_body in payload_shapes:
                try:
                    resp = session.post(url, json=post_body, headers=_headers(token), timeout=30)
                except requests.RequestException as exc:
                    return jsonify({
                        "success": False,
                        "message": f"library API request failed: {exc}",
                        "status_code": None,
                    }), 502

                last_resp = resp
                result = {
                    "auth_method_used": auth_method_norm,
                    "homepage_id_used": homepage_id,
                    "payload_shape_used": payload_shape,
                }
                _attach_json(result, resp)

                attempt_logs.append({
                    "homepage_id": homepage_id,
                    "payload_shape": payload_shape,
                    "serialNo": serial if auth_method_norm == "RF_TAG" else None,
                    "status_code": resp.status_code,
                    "success": result.get("success"),
                    "message": result.get("message"),
                })

                final_result = result
                if result.get("success") is True:
                    break

            if final_result and final_result.get("success") is True:
                break
        if final_result and final_result.get("success") is True:
            break

    if final_result is None or last_resp is None:
        return jsonify({
            "success": False,
            "message": "unknown error: no response from library API",
            "status_code": None,
        }), 502

    if debug:
        final_result["debug"] = {
            "request_url": _check_arrival_url(int(final_result.get("homepage_id_used") or homepage_candidates[0]), room_id_str),
            "request_payload": [payload],
            "response_content_type": last_resp.headers.get("Content-Type"),
            "gps_used": {"latitude": latitude, "longitude": longitude},
            "attempt_logs": attempt_logs,
            "seat_context": {
                "use_active_context": use_active_context,
                "room_id_input": room_id_input_str or None,
                "active_homepage_id": active_homepage_id,
                "active_room_id": active_room_id,
                "allowed_methods": allowed_methods,
                "checkin_expiry_date": expiry_text,
                "homepage_ids_tried": homepage_ids,
                "seat_charges_trace": seat_ctx.get("traces") if seat_ctx else None,
                "seat_context_error": seat_ctx_err if not seat_ctx else None,
            },
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

    token, session, err = auto_login_session()
    if not token or session is None:
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
            resp = session.post(url, json=payload, headers=_headers(token), timeout=30)
        elif method == "PATCH":
            resp = session.patch(url, json=payload, headers=_headers(token), timeout=30)
        elif method == "PUT":
            resp = session.put(url, json=payload, headers=_headers(token), timeout=30)
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


@app.route("/debug-seat-context", methods=["GET"])
def debug_seat_context():
    homepage_ids = _parse_homepage_ids(request.args.get("homepage_ids") or os.environ.get("HOMEPAGE_IDS"))
    token, session, err = auto_login_session()
    if not token or session is None:
        return jsonify({
            "success": False,
            "message": f"auto login failed: {err}",
            "status_code": None,
        }), 502

    seat_ctx, seat_ctx_err = _fetch_seat_context(session, token, homepage_ids=homepage_ids)
    if not seat_ctx:
        return jsonify({
            "success": False,
            "message": seat_ctx_err or "failed to fetch seat context",
            "status_code": None,
            "homepage_ids_tried": homepage_ids,
        }), 400

    result: dict = dict(seat_ctx["wrapped"])
    data = result.get("data")
    active = seat_ctx["active"]
    usage_ids = _dedupe_preserve(_collect_key_values(data, "usageId"))
    room_ids_direct = _dedupe_preserve(_collect_key_values(data, "roomId"))
    room_obj_ids = _dedupe_preserve(_collect_key_values(data, "id"))

    result["hints"] = {
        "seat_charges_url": seat_ctx.get("seat_charges_url"),
        "active_homepage_id": seat_ctx.get("homepage_id"),
        "seat_charges_trace": seat_ctx.get("traces"),
        "found_usage_ids": usage_ids,
        "found_room_ids": room_ids_direct,
        "found_any_id_values": room_obj_ids[:50],
        "active_charge": active,
        "tip": "Use active_charge.room.id for room_id. Use one of active_charge.arrivalConfirmMethods.",
    }

    code = 200 if result["success"] else 400
    return jsonify(result), code


@app.route("/debug-seat-context-raw", methods=["GET"])
def debug_seat_context_raw():
    homepage_ids = _parse_homepage_ids(request.args.get("homepage_ids") or os.environ.get("HOMEPAGE_IDS"))
    token, session, err = auto_login_session()
    if not token or session is None:
        return jsonify({
            "success": False,
            "message": f"auto login failed: {err}",
            "status_code": None,
        }), 502

    rows = _scan_seat_charges(session, token, homepage_ids)
    return jsonify({
        "success": True,
        "homepage_ids_tried": homepage_ids,
        "rows": rows,
    }), 200


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




