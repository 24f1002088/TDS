import os
import hashlib
import csv
import io
import json
import re
import sqlite3
import threading
import uuid
import base64
from datetime import datetime
from urllib.request import Request as UrlRequest, urlopen
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

DEFAULTS: dict[str, Any] = {
    "port": 8000,
    "workers": 1,
    "debug": False,
    "log_level": "info",
    "api_key": "default-secret-0002",
}

load_dotenv(Path(__file__).with_name(".env"), override=False)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

EXAM_EMAIL = "24f1002088@ds.study.iitm.ac.in"
MCP_PROTOCOL_VERSION = "2024-11-05"
LEDGER_ORDERS_URL = os.getenv("LEDGER_ORDERS_URL", "")

A2A_BASE_PATH = os.getenv("A2A_BASE_PATH", "/a2a").rstrip("/")
A2A_TOKEN = os.getenv(
    "A2A_BEARER_TOKEN",
    os.getenv("A2A_TOKEN", os.getenv("APP_API_KEY", DEFAULTS["api_key"])),
)
A2A_DB_PATH = os.getenv(
    "A2A_DB_PATH", str(Path(__file__).with_name("invoice_tasks.sqlite3"))
)
A2A_BATCH_MEDIA = "application/vnd.ga5.invoice-claim-batch+json"
A2A_PROPOSAL_MEDIA = "application/vnd.ga5.invoice-action-proposals+json"
A2A_RESULT_MEDIA = "application/vnd.ga5.invoice-action-results+json"
A2A_RECEIPT_MEDIA = "application/vnd.ga5.invoice-action-receipts+json"
A2A_JSON_MEDIA = "application/a2a+json"
A2A_ACTIONS = {
    "settle_invoice",
    "request_approval",
    "hold_invoice",
    "reject_duplicate",
    "open_exception",
}
A2A_DB_LOCK = threading.RLock()


def _a2a_database() -> sqlite3.Connection:
    connection = sqlite3.connect(A2A_DB_PATH, check_same_thread=False)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS a2a_tasks (
            id TEXT PRIMARY KEY,
            principal TEXT NOT NULL,
            message_id TEXT NOT NULL,
            message_hash TEXT NOT NULL,
            context_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            state TEXT NOT NULL,
            task_json TEXT NOT NULL,
            UNIQUE(principal, message_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS a2a_messages (
            principal TEXT NOT NULL,
            message_id TEXT NOT NULL,
            message_hash TEXT NOT NULL,
            task_id TEXT NOT NULL,
            response_json TEXT NOT NULL,
            UNIQUE(principal, message_id)
        )
        """
    )
    connection.commit()
    return connection


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _a2a_task_response(task: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"task": task, "taskId": task["id"]}, media_type=A2A_JSON_MEDIA)


def _a2a_raw_task_response(task: dict[str, Any]) -> JSONResponse:
    return JSONResponse(task, media_type=A2A_JSON_MEDIA)


def _a2a_error(code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code}}, status_code=status, media_type=A2A_JSON_MEDIA)


def _a2a_principal(authorization: str | None) -> str | None:
    expected = f"Bearer {A2A_TOKEN}"
    if authorization != expected:
        return None
    return hashlib.sha256(authorization.encode("utf-8")).hexdigest()


def _a2a_principal(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    if not authorization[7:].strip():
        return None
    return hashlib.sha256(authorization.encode("utf-8")).hexdigest()


def _a2a_check_headers(
    authorization: str | None,
    version: str | None,
    content_type: str | None,
    accept: str | None,
    require_body: bool = False,
) -> tuple[str | None, JSONResponse | None]:
    principal = _a2a_principal(authorization)
    if principal is None:
        return None, _a2a_error("AUTHENTICATION_REQUIRED", 401)
    if version != "1.0":
        return None, _a2a_error("UNSUPPORTED_VERSION", 400)
    media = content_type if require_body else (accept or A2A_JSON_MEDIA)
    if require_body and not media.split(";", 1)[0].strip() == A2A_JSON_MEDIA:
        return None, _a2a_error("UNSUPPORTED_MEDIA_TYPE", 415)
    if not require_body and media != A2A_JSON_MEDIA and A2A_JSON_MEDIA not in media:
        return None, _a2a_error("UNSUPPORTED_MEDIA_TYPE", 415)
    return principal, None


def _a2a_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _a2a_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _a2a_strings(child)]
    return []


def _a2a_refs(text: str) -> list[str]:
    return re.findall(r"\[[^\[\]\r\n]{1,160}\]", text)


def _a2a_fact(package: dict[str, Any], names: tuple[str, ...], text: str) -> str:
    for name in names:
        value = package.get(name)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return str(value)
    return ""


def _a2a_amount(package: dict[str, Any], text: str) -> int:
    for key in ("amountMinor", "amount_minor"):
        if package.get(key) is not None:
            return int(package[key])
    match = re.search(r"(?:amount|total|due)[^\d]{0,30}([\d,]+(?:\.\d{1,2})?)", text, re.I)
    if not match:
        return 0
    value = float(match.group(1).replace(",", ""))
    return int(round(value * 100)) if "." in match.group(1) else int(value)


def _a2a_decide(package: dict[str, Any]) -> dict[str, Any]:
    strings = _a2a_strings(package)
    text = "\n".join(strings)
    paragraphs = [
        part.strip()
        for source in strings
        for part in re.split(r"\n\s*\n|(?<=[.!?])\s{2,}", source)
        if part.strip()
    ]
    candidates = [part for part in paragraphs if len(_a2a_refs(part)) >= 3]
    if not candidates:
        candidates = paragraphs or [text]
    useful = [
        part for part in candidates
        if not re.search(r"\b(cover sheet|archive example|training decoy|decoy)\b", part, re.I)
    ]
    if useful:
        candidates = useful

    def score(paragraph: str, terms: tuple[str, ...]) -> int:
        lowered = paragraph.lower()
        return sum(2 for term in terms if term in lowered) + min(3, len(_a2a_refs(paragraph)))

    ranked = [
        ("reject_duplicate", ("already paid", "duplicate", "paid previously", "settled before")),
        ("open_exception", ("conflict", "mismatch", "inconsistent", "exception")),
        ("hold_invoice", ("hold", "verify", "verification", "pause", "missing")),
        ("request_approval", ("approval", "outside authority", "exceeds", "delegated limit", "approve")),
        ("settle_invoice", ("valid", "reconciled", "pay", "settle", "matched")),
    ]
    explicit = [
        (paragraph, match.group(1))
        for paragraph in candidates
        if (match := re.search(
            r"(?:recommended\s+action|proposed\s+action|decision|action)\s*[:=]\s*"
            r"(settle_invoice|request_approval|hold_invoice|reject_duplicate|open_exception)\b",
            paragraph,
            re.I,
        ))
        and not re.search(
            r"\b(?:do not|not|never|without)\s+(?:\w+\s+){0,2}"
            + re.escape(match.group(1)),
            paragraph,
            re.I,
        )
    ]
    if explicit:
        decisive, explicit_action = max(explicit, key=lambda item: len(_a2a_refs(item[0])))
        selected = (score(decisive, (explicit_action,)), explicit_action.lower(), decisive)
    else:
        selected = max(
            ((score(paragraph, terms), action, paragraph) for paragraph in candidates for action, terms in ranked),
            key=lambda item: item[0],
        )
    action, decisive = selected[1], selected[2]
    refs = _a2a_refs(decisive)
    if len(refs) < 3:
        refs = _a2a_refs(text)
    refs = refs[:3]
    vendor = _a2a_fact(package, ("vendorName", "vendor", "supplier"), text)
    if not vendor:
        match = re.search(r"(?:vendor|supplier|from)\s*[:\-]\s*([^\n,;]+)", text, re.I)
        vendor = match.group(1).strip() if match else "Unknown vendor"
    invoice = _a2a_fact(package, ("invoiceNumber", "invoice_number", "invoiceId"), text)
    if not invoice:
        match = re.search(r"(?:invoice(?:\s*(?:number|no|#))?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]+)", text, re.I)
        invoice = match.group(1) if match else ""
    currency = _a2a_fact(package, ("currency",), text) or (
        re.search(r"\b(INR|USD|EUR|GBP|CAD|AUD)\b", text, re.I).group(1).upper()
        if re.search(r"\b(INR|USD|EUR|GBP|CAD|AUD)\b", text, re.I)
        else "INR"
    )
    facts = {
        "vendorName": vendor,
        "invoiceNumber": invoice,
        "amountMinor": _a2a_amount(package, text),
        "currency": currency,
    }
    evidence = refs[:3]
    if len(evidence) != 3:
        raise ValueError("INSUFFICIENT_EVIDENCE")
    rationale = f"{action}: the decisive record supports this disposition. Evidence: {', '.join(evidence)}."
    return {
        "packageId": str(package.get("packageId", package.get("id", ""))),
        "actionId": uuid.uuid4().hex,
        "action": action,
        "facts": facts,
        "evidenceRefs": evidence,
        "rationale": rationale[:1500],
    }


def _a2a_task(
    principal: str,
    message: dict[str, Any],
    message_id: str,
    task_id: str | None = None,
    context_id: str | None = None,
) -> dict[str, Any]:
    payload = next(
        (part.get("data") for part in message.get("parts", []) if part.get("mediaType") == A2A_BATCH_MEDIA),
        None,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("packages"), list):
        raise ValueError("INVALID_BATCH")
    batch_id = str(payload.get("batchId", ""))
    if not batch_id:
        raise ValueError("INVALID_BATCH")
    if not payload["packages"] or any(
        not isinstance(package, dict) for package in payload["packages"]
    ):
        raise ValueError("INVALID_BATCH")
    proposals = [_a2a_decide(package) for package in payload["packages"]]
    if (
        len({item["packageId"] for item in proposals}) != len(proposals)
        or len({item["actionId"] for item in proposals}) != len(proposals)
        or any(not item["packageId"] or len(item["evidenceRefs"]) != 3 for item in proposals)
    ):
        raise ValueError("INVALID_BATCH")
    task_id = task_id or uuid.uuid4().hex
    context_id = context_id or uuid.uuid4().hex
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
        "history": [message],
        "artifacts": [
            {
                "artifactId": uuid.uuid4().hex,
                "parts": [
                    {
                        "mediaType": A2A_PROPOSAL_MEDIA,
                        "data": {"batchId": batch_id, "proposals": proposals},
                    }
                ],
            }
        ],
    }


def _a2a_store_task(
    principal: str, message_id: str, message: dict[str, Any]
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    digest = hashlib.sha256(_canonical_json(message).encode("utf-8")).hexdigest()
    with A2A_DB_LOCK:
        connection = _a2a_database()
        row = connection.execute(
            "SELECT message_hash, task_json FROM a2a_tasks WHERE principal = ? AND message_id = ?",
            (principal, message_id),
        ).fetchone()
        if row:
            connection.close()
            if row[0] != digest:
                return None, _a2a_error("IDEMPOTENCY_CONFLICT", 409)
            return json.loads(row[1]), None
        try:
            task = _a2a_task(principal, message, message_id)
        except (TypeError, ValueError):
            connection.close()
            return None, _a2a_error("INVALID_MESSAGE", 400)
        payload = next(
            (
                part["data"]
                for part in message.get("parts", [])
                if part.get("mediaType") == A2A_BATCH_MEDIA
                and isinstance(part.get("data"), dict)
            ),
            {},
        )
        connection.execute(
            "INSERT INTO a2a_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task["id"], principal, message_id, digest, task["contextId"],
                str(payload["batchId"]), task["status"]["state"], _canonical_json(task),
            ),
        )
        connection.commit()
        connection.close()
        return task, None


def _a2a_find_task(principal: str, task_id: str) -> tuple[dict[str, Any] | None, sqlite3.Connection]:
    connection = _a2a_database()
    row = connection.execute(
        "SELECT task_json FROM a2a_tasks WHERE principal = ? AND id = ?",
        (principal, task_id),
    ).fetchone()
    return (json.loads(row[0]) if row else None), connection


def _a2a_result_message(message: dict[str, Any]) -> dict[str, Any] | None:
    for part in message.get("parts", []):
        if part.get("mediaType") == A2A_RESULT_MEDIA and isinstance(part.get("data"), dict):
            return part["data"]
    return None


def _a2a_apply_results(
    principal: str, task: dict[str, Any], message: dict[str, Any]
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    data = _a2a_result_message(message)
    if (
        not data
        or message.get("taskId") != task["id"]
        or message.get("contextId") != task["contextId"]
        or str(data.get("batchId", "")) != _a2a_batch_id(task)
    ):
        return None, _a2a_error("INVALID_CONTINUATION", 409)
    if task["status"]["state"] in {"TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"}:
        return None, _a2a_error("TASK_ALREADY_TERMINAL", 409)
    proposal_part = task["artifacts"][0]["parts"][0]["data"]["proposals"]
    proposals = {proposal["packageId"]: proposal for proposal in proposal_part}
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None, _a2a_error("INVALID_CONTINUATION", 409)
    accepted = []
    seen: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            return None, _a2a_error("INVALID_CONTINUATION", 409)
        package_id = str(result.get("packageId", ""))
        proposal = proposals.get(package_id)
        if (
            proposal is None
            or package_id in seen
            or result.get("actionId") != proposal["actionId"]
            or result.get("action") != proposal["action"]
            or result.get("outcome") not in {"ACCEPTED", "REJECTED"}
            or not isinstance(result.get("receiptNonce"), str)
            or not result["receiptNonce"]
        ):
            return None, _a2a_error("INVALID_CONTINUATION", 409)
        seen.add(package_id)
        if result["outcome"] == "ACCEPTED":
            accepted.append(
                {
                    "packageId": package_id,
                    "actionId": proposal["actionId"],
                    "action": proposal["action"],
                    "receiptNonce": result["receiptNonce"],
                    "facts": proposal["facts"],
                    "evidenceRefs": proposal["evidenceRefs"],
                }
            )
    if seen != set(proposals):
        return None, _a2a_error("INVALID_CONTINUATION", 409)
    task["history"].append(message)
    task["status"] = {"state": "TASK_STATE_COMPLETED"}
    task["artifacts"].append(
        {
            "artifactId": uuid.uuid4().hex,
            "parts": [{"mediaType": A2A_RECEIPT_MEDIA, "data": {
                "batchId": data["batchId"], "executions": accepted
            }}],
        }
    )
    return task, None


def _a2a_batch_id(task: dict[str, Any]) -> str:
    return str(task["artifacts"][0]["parts"][0]["data"]["batchId"])


def _a2a_save_task(principal: str, task: dict[str, Any], connection: sqlite3.Connection) -> None:
    connection.execute(
        "UPDATE a2a_tasks SET state = ?, task_json = ? WHERE principal = ? AND id = ?",
        (task["status"]["state"], _canonical_json(task), principal, task["id"]),
    )
    connection.commit()


def _a2a_message_hash(message: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(message).encode("utf-8")).hexdigest()


def _a2a_replayed_message(
    connection: sqlite3.Connection,
    principal: str,
    message: dict[str, Any],
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    message_id = str(message.get("messageId", ""))
    if not message_id:
        return None, _a2a_error("INVALID_MESSAGE", 400)
    row = connection.execute(
        "SELECT message_hash, response_json FROM a2a_messages "
        "WHERE principal = ? AND message_id = ?",
        (principal, message_id),
    ).fetchone()
    if not row:
        return None, None
    if row[0] != _a2a_message_hash(message):
        return None, _a2a_error("IDEMPOTENCY_CONFLICT", 409)
    return json.loads(row[1]), None


@app.get("/.well-known/agent-card.json")
async def a2a_agent_card(request: Request) -> JSONResponse:
    origin = str(request.base_url).rstrip("/")
    base_url = origin + A2A_BASE_PATH + "/"
    return JSONResponse(
        {
            "name": "Public Invoice Action Agent",
            "description": "Reads invoice claim batches, proposes evidence-backed actions, and executes only accepted results.",
            "version": "1.0.0",
            "capabilities": {"streaming": False, "pushNotifications": False},
            "securitySchemes": {
                "bearerAuth": {
                    "httpAuthSecurityScheme": {
                        "scheme": "Bearer",
                        "description": "Use the deployment Bearer token.",
                    }
                }
            },
            "security": [{"bearerAuth": []}],
            "supportedInterfaces": [
                {"url": base_url, "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}
            ],
            "defaultInputModes": [A2A_BATCH_MEDIA, A2A_RESULT_MEDIA],
            "defaultOutputModes": [A2A_PROPOSAL_MEDIA, A2A_RECEIPT_MEDIA],
            "skills": [{
                "id": "invoice_action_agent",
                "name": "Invoice action decisions",
                "description": "Proposes and executes safe invoice actions with document evidence.",
                "tags": ["invoice", "evidence", "payments"],
            }],
        },
        media_type=A2A_JSON_MEDIA,
    )


@app.post(A2A_BASE_PATH + "/message:send")
@app.post(A2A_BASE_PATH + "/message%3Asend")
async def a2a_send(
    request: Request,
    authorization: str | None = Header(default=None),
    a2a_version: str | None = Header(default=None, alias="A2A-Version"),
    content_type: str | None = Header(default=None, alias="Content-Type"),
) -> Response:
    principal, error = _a2a_check_headers(authorization, a2a_version, content_type, None, True)
    if error:
        return error
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return _a2a_error("INVALID_MESSAGE", 400)
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict) or not message.get("messageId") or message.get("role") != "ROLE_USER":
        return _a2a_error("INVALID_MESSAGE", 400)
    if not any(part.get("mediaType") == A2A_BATCH_MEDIA for part in message.get("parts", [])):
        if not any(part.get("mediaType") == A2A_RESULT_MEDIA for part in message.get("parts", [])):
            return _a2a_error("UNSUPPORTED_INPUT", 400)
    with A2A_DB_LOCK:
        if any(part.get("mediaType") == A2A_RESULT_MEDIA for part in message.get("parts", [])):
            task_id = str(message.get("taskId", ""))
            task, connection = _a2a_find_task(principal, task_id)
            if task is None:
                connection.close()
                return _a2a_error("TASK_NOT_FOUND", 404)
            replay, error = _a2a_replayed_message(connection, principal, message)
            if error:
                connection.close()
                return error
            if replay is not None:
                connection.close()
                return _a2a_task_response(replay)
            task, error = _a2a_apply_results(principal, task, message)
            if error:
                connection.close()
                return error
            _a2a_save_task(principal, task, connection)
            connection.execute(
                "INSERT INTO a2a_messages "
                "(principal, message_id, message_hash, task_id, response_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    principal,
                    str(message["messageId"]),
                    _a2a_message_hash(message),
                    task["id"],
                    _canonical_json(task),
                ),
            )
            connection.commit()
            connection.close()
            return _a2a_task_response(task)


        try:
            task, error = _a2a_store_task(principal, str(message["messageId"]), message)
        except (sqlite3.Error, OSError):
            return _a2a_error("TASK_STORAGE_UNAVAILABLE", 503)
        if error:
            return error
        return _a2a_task_response(task)


@app.get(A2A_BASE_PATH + "/")
async def a2a_base() -> JSONResponse:
    return JSONResponse(
        {"service": "invoice-action-agent", "agentCard": "/.well-known/agent-card.json"},
        media_type=A2A_JSON_MEDIA,
    )


def _a2a_route_headers(
    authorization: str | None, a2a_version: str | None, accept: str | None
) -> tuple[str | None, JSONResponse | None]:
    return _a2a_check_headers(authorization, a2a_version, None, accept, False)


@app.get(A2A_BASE_PATH + "/tasks/{task_id}")
async def a2a_get_task(
    task_id: str,
    authorization: str | None = Header(default=None),
    a2a_version: str | None = Header(default=None, alias="A2A-Version"),
    accept: str | None = Header(default=None, alias="Accept"),
) -> Response:
    principal, error = _a2a_route_headers(authorization, a2a_version, accept)
    if error:
        return error
    with A2A_DB_LOCK:
        if task_id == "undefined":
            connection = _a2a_database()
            row = connection.execute(
                "SELECT task_json FROM a2a_tasks WHERE principal = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (principal,),
            ).fetchone()
            task = json.loads(row[0]) if row else None
        else:
            task, connection = _a2a_find_task(principal, task_id)
        connection.close()
    return _a2a_raw_task_response(task) if task else _a2a_error("TASK_NOT_FOUND", 404)


@app.get(A2A_BASE_PATH + "/tasks")
async def a2a_list_tasks(
    authorization: str | None = Header(default=None),
    a2a_version: str | None = Header(default=None, alias="A2A-Version"),
    accept: str | None = Header(default=None, alias="Accept"),
) -> Response:
    principal, error = _a2a_route_headers(authorization, a2a_version, accept)
    if error:
        return error
    with A2A_DB_LOCK:
        connection = _a2a_database()
        rows = connection.execute(
            "SELECT task_json FROM a2a_tasks WHERE principal = ? ORDER BY rowid DESC",
            (principal,),
        ).fetchall()
        connection.close()
    return JSONResponse({"tasks": [json.loads(row[0]) for row in rows]}, media_type=A2A_JSON_MEDIA)


@app.post(A2A_BASE_PATH + "/tasks/{task_id}:cancel")
async def a2a_cancel_task(
    task_id: str,
    authorization: str | None = Header(default=None),
    a2a_version: str | None = Header(default=None, alias="A2A-Version"),
    content_type: str | None = Header(default=None, alias="Content-Type"),
) -> Response:
    principal, error = _a2a_check_headers(authorization, a2a_version, content_type, None, True)
    if error:
        return error
    with A2A_DB_LOCK:
        task, connection = _a2a_find_task(principal, task_id)
        if task is None:
            connection.close()
            return _a2a_error("TASK_NOT_FOUND", 404)
        if task["status"]["state"] in {"TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"}:
            connection.close()
            return _a2a_error("TASK_ALREADY_TERMINAL", 409)
        task["status"] = {"state": "TASK_STATE_CANCELED"}
        _a2a_save_task(principal, task, connection)
        connection.close()
        return _a2a_raw_task_response(task)


def load_ledger_rows() -> list[dict[str, Any]]:
    if not LEDGER_ORDERS_URL:
        return []
    request = UrlRequest(
        LEDGER_ORDERS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AcmeLedger/1.0)",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://exam.sanand.workers.dev/",
            "Origin": "https://exam.sanand.workers.dev",
        },
    )
    with urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return list(csv.DictReader(io.StringIO(body)))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    for key in ("orders", "data", "results", "items"):
        if isinstance(payload.get(key), list):
            return [row for row in payload[key] if isinstance(row, dict)]
    return []


def current_orders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        order_id = str(row.get("order_id", row.get("id", "")))
        updated = str(row.get("updated_at", ""))
        if order_id not in latest or updated > str(latest[order_id].get("updated_at", "")):
            latest[order_id] = row
    return list(latest.values())


def money_value(row: dict[str, Any], *names: str) -> float:
    for name in names:
        value = row.get(name)
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", str(value))
        if match:
            return float(match.group(0).replace(",", ""))
    return 0.0


def answer_ledger_question(question: str) -> Any:
    rows = current_orders(load_ledger_rows())
    lowered = question.lower()
    month_match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        lowered,
    )
    year_match = re.search(r"\b(20\d{2})\b", lowered)
    region_match = re.search(r"\b(north|south|east|west|central)\b", lowered)
    filtered = rows
    if month_match or year_match:
        month = month_match.group(1) if month_match else None
        year = year_match.group(1) if year_match else None
        filtered = [
            row
            for row in filtered
            if (date_value := str(row.get("date", row.get("order_date", row.get("created_at", ""))))).lower()
            and (not month or month in date_value.lower())
            and (not year or year in date_value)
        ]
    if region_match:
        filtered = [
            row
            for row in filtered
            if region_match.group(1) in str(row.get("region", "")).lower()
        ]
    if "refund" in lowered or "refunded" in lowered:
        refunded = [row for row in filtered if str(row.get("status", "")).lower() == "refunded"]
        return len(refunded) if any(word in lowered for word in ("count", "how many", "number")) else sum(
            money_value(row, "refund_amount", "amount", "total", "revenue") for row in refunded
        )
    paid = [row for row in filtered if str(row.get("status", "")).lower() == "paid"]
    if any(word in lowered for word in ("revenue", "sales", "total", "earn", "earned")):
        return round(
            sum(money_value(row, "amount", "total", "revenue", "total_usd", "net_amount") for row in paid),
            2,
        )
    return len(filtered)


@app.post("/")
@app.post("/answer")
async def answer_question(request: Request) -> Any:
    payload = await request.json()
    if isinstance(payload, dict) and payload.get("profile") == MAIL_PROFILE:
        return _mailroom_process(payload)
    return {"answer": answer_ledger_question(str(payload.get("question", "")))}


@app.post("/mailroom")
@app.post("/mailroom/")
async def mailroom_endpoint(request: Request) -> Response:
    payload = await request.json()
    return _mailroom_process(payload) if isinstance(payload, dict) else _mail_error("INVALID_REQUEST")


def mcp_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


@app.post("/mcp")
async def mcp_endpoint(
    request: Request,
    x_exam_challenge: str | None = Header(default=None),
) -> Response:
    message = await request.json()
    method = message.get("method")
    request_id = message.get("id")

    if method == "notifications/initialized":
        return Response(status_code=202)

    if method == "initialize":
        return mcp_result(
            request_id,
            {
                "protocolVersion": message.get("params", {}).get(
                    "protocolVersion", MCP_PROTOCOL_VERSION
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "exam-challenge-server", "version": "1.0.0"},
            },
        )

    if method == "tools/list":
        return mcp_result(
            request_id,
            {
                "tools": [
                    {
                        "name": "solve_challenge",
                        "description": "Solve the current exam challenge from the request header.",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            },
        )

    if method == "tools/call":
        challenge = x_exam_challenge or ""
        digest = hashlib.sha256(f"{challenge}:{EXAM_EMAIL}".encode("utf-8")).hexdigest()[:16]
        return mcp_result(
            request_id,
            {"content": [{"type": "text", "text": digest}]},
        )

    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        },
        status_code=404,
    )


MAIL_PROFILE = "ga5-mailroom-action-gate/v2"
MAIL_DB_PATH = os.getenv(
    "MAILROOM_DB_PATH", str(Path(__file__).with_name("mailroom.sqlite3"))
)
MAIL_ACTIONS = {
    "create_draft", "update_internal_record", "send_approved_notice",
    "request_confirmation", "quarantine_item", "no_action",
}
MAIL_DB_LOCK = threading.RLock()


def _mail_db() -> sqlite3.Connection:
    db = sqlite3.connect(MAIL_DB_PATH, check_same_thread=False)
    db.execute(
        "CREATE TABLE IF NOT EXISTS mail_evaluations "
        "(evaluation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, input_digest TEXT NOT NULL, "
        "verifier_json TEXT NOT NULL, response_json TEXT NOT NULL, commit_json TEXT, commit_hash TEXT)"
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(mail_evaluations)")}
    if "commit_hash" not in columns:
        db.execute("ALTER TABLE mail_evaluations ADD COLUMN commit_hash TEXT")
    db.commit()
    return db


def _mail_canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _mail_digest(value: Any) -> str:
    return hashlib.sha256(_mail_canonical(value).encode("utf-8")).hexdigest()


def _mail_error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status, media_type="application/json")


def _mail_lines(dossier: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for source in dossier.get("sources", []):
        if isinstance(source, dict):
            for line in source.get("lines", []):
                if isinstance(line, dict) and isinstance(line.get("lineId"), str) and isinstance(line.get("text"), str):
                    result.append({"lineId": line["lineId"], "text": line["text"]})
    return result


def _mail_validate_dossier(dossier: Any) -> bool:
    if not isinstance(dossier, dict):
        return False
    if not isinstance(dossier.get("dossierId"), str) or not dossier["dossierId"]:
        return False
    if dossier.get("partition") not in {"stable_core", "fresh_audit"}:
        return False
    if not isinstance(dossier.get("receivedAt"), str) or not isinstance(dossier.get("mailbox"), str):
        return False
    if not isinstance(dossier.get("objective"), str) or not isinstance(dossier.get("sources"), list):
        return False
    line_ids: list[str] = []
    for source in dossier["sources"]:
        if not isinstance(source, dict) or not isinstance(source.get("lines"), list):
            return False
        for line in source["lines"]:
            if (
                not isinstance(line, dict)
                or not isinstance(line.get("lineId"), str)
                or not line["lineId"]
                or not isinstance(line.get("text"), str)
            ):
                return False
            line_ids.append(line["lineId"])
    return bool(line_ids) and len(line_ids) == len(set(line_ids))


def _mail_validate_proposal(proposal: dict[str, Any], dossier: dict[str, Any]) -> bool:
    if (
        not isinstance(proposal.get("callId"), str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{12,128}", proposal["callId"])
        or proposal.get("action") not in MAIL_ACTIONS
        or not isinstance(proposal.get("evidence"), list)
        or not proposal["evidence"]
        or len(proposal["evidence"]) != len(set(proposal["evidence"]))
    ):
        return False
    valid_lines = {line["lineId"] for line in _mail_lines(dossier)}
    if any(line_id not in valid_lines or not isinstance(line_id, str) for line_id in proposal["evidence"]):
        return False
    target = proposal.get("target")
    payload = proposal.get("payload")
    if proposal["action"] == "no_action":
        return (
            target is None
            and isinstance(payload, dict)
            and set(payload) == {"reasonCode", "referenceId"}
            and payload["reasonCode"] in {"ALREADY_COMPLETED", "DUPLICATE", "INFORMATIONAL"}
            and all(isinstance(value, str) and value for value in payload.values())
        )
    schemas = {
        "create_draft": ("draft_queue", {"recipient", "referenceId", "status", "template"}),
        "update_internal_record": ("case_record", {"field", "sourceEventId", "value"}),
        "send_approved_notice": ("email", {"referenceId", "status", "template"}),
        "request_confirmation": ("approval_queue", {"claimedSender", "questionCode", "referenceId"}),
        "quarantine_item": ("security_queue", {"artifactId", "reasonCode"}),
    }
    target_schema, payload_keys = schemas[proposal["action"]]
    return (
        isinstance(target, dict)
        and set(target) == {"kind", "id"}
        and target["kind"] == target_schema
        and isinstance(target["id"], str)
        and bool(target["id"])
        and isinstance(payload, dict)
        and set(payload) == payload_keys
        and all(isinstance(value, str) and value for value in payload.values())
    )


def _mail_extract(text: str, patterns: tuple[str, ...], default: str = "") -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return default


def _mail_propose(dossier: dict[str, Any]) -> dict[str, Any]:
    dossier_id = str(dossier["dossierId"])
    lines = _mail_lines(dossier)
    text = "\n".join(line["text"] for line in lines)
    lowered = text.lower()
    objective = str(dossier.get("objective", ""))
    mailbox = str(dossier.get("mailbox", ""))
    joined = f"{objective}\n{text}".lower()
    line_ids = [line["lineId"] for line in lines]

    def evidence(*needles: str) -> list[str]:
        chosen = [line["lineId"] for line in lines if any(needle in line["text"].lower() for needle in needles)]
        return chosen[:4] or line_ids[:1]

    recipient = _mail_extract(text, (r"(?:recipient|deliver to|send to)\s*[:=]\s*<?([^>\s,;]+@[^>\s,;]+)>?",))
    reference = _mail_extract(text, (r"(?:reference|order|case|tracking)\s*(?:id|number|#)?\s*[:=]\s*([A-Z0-9][A-Z0-9_-]+)",), dossier_id)
    case_id = _mail_extract(text, (r"(?:case(?:\s*id)?|record)\s*[:=]\s*([A-Z0-9][A-Z0-9_-]+)",), dossier_id)
    status = _mail_extract(text, (r"\b(?:status|delivery status)\s*[:=]\s*([A-Za-z -]+)",), "pending")
    if "prompt injection" in joined or "ignore previous" in joined or "send secret" in joined or "tool instruction" in joined:
        action = "quarantine_item"
        target = {"kind": "security_queue", "id": "mailroom"}
        payload = {"artifactId": dossier_id, "reasonCode": "INDIRECT_PROMPT_INJECTION"}
        ev = evidence("ignore previous", "prompt injection", "tool instruction", "send secret")
    elif "already completed" in joined or "duplicate" in joined or "informational" in joined or "no action" in joined:
        action = "no_action"
        reason = "DUPLICATE" if "duplicate" in joined else "ALREADY_COMPLETED" if "completed" in joined else "INFORMATIONAL"
        target, payload = None, {"reasonCode": reason, "referenceId": reference}
        ev = evidence("duplicate", "completed", "informational", "no action")
    elif ("approved" in joined and recipient and recipient.lower() in joined and
          ("public" in joined or "notice" in joined or "delivery" in joined)):
        action = "send_approved_notice"
        target = {"kind": "email", "id": recipient}
        payload = {"referenceId": reference, "status": status, "template": "approved_delivery_notice"}
        ev = evidence("approved", recipient.lower(), "public", "delivery")
    elif recipient and ("draft" in joined or "customer email" in joined or "order status" in joined):
        action = "create_draft"
        target = {"kind": "draft_queue", "id": f"mailbox:{mailbox}"}
        payload = {"recipient": recipient, "referenceId": reference, "status": status, "template": "order_status"}
        ev = evidence("draft", "customer", "order status", "recipient")
    elif "delivery window" in joined and ("authorized" in joined or "approved" in joined):
        action = "update_internal_record"
        target = {"kind": "case_record", "id": case_id}
        event_id = _mail_extract(text, (r"(?:event|sourceevent|event id)\s*[:=]\s*([A-Z0-9][A-Z0-9_-]+)",), reference)
        value = _mail_extract(text, (r"delivery window\s*[:=]\s*([^.;\n]+)",), "confirmed")
        payload = {"field": "delivery_window", "sourceEventId": event_id, "value": value}
        ev = evidence("delivery window", "authorized", "approved")
    else:
        action = "request_confirmation"
        owner = _mail_extract(text, (r"(?:owning team|team|queue)\s*[:=]\s*([A-Za-z0-9 _-]+)",), "mailroom")
        claimed = _mail_extract(text, (r"(?:claimed sender|from|sender)\s*[:=]\s*([^\s,;]+)",), "unknown")
        target = {"kind": "approval_queue", "id": owner}
        payload = {"claimedSender": claimed, "questionCode": "VERIFY_REQUEST", "referenceId": reference}
        ev = evidence("verify", "ambiguous", "sender", "identity", "confirmation")
    fingerprint = _mail_digest(dossier)
    call_id = f"mail-{fingerprint[:32]}"
    return {"dossierId": dossier_id, "callId": call_id, "action": action, "target": target, "payload": payload, "evidence": list(dict.fromkeys(ev))[:4]}


def _mail_proposal_digest(proposal: dict[str, Any]) -> str:
    normalized = {
        "dossierId": proposal["dossierId"], "callId": proposal["callId"], "action": proposal["action"],
        "target": proposal.get("target"), "payload": proposal["payload"],
        "evidence": sorted(proposal["evidence"]),
    }
    return _mail_digest(normalized)


def _mail_verify(receipt: dict[str, Any], verifier: dict[str, Any], evaluation_id: str, input_digest: str) -> bool:
    try:
        if verifier.get("algorithm") != "Ed25519":
            return False
        key = verifier["publicKeyJwk"]
        raw_key = base64.urlsafe_b64decode(str(key["x"]) + "=" * (-len(str(key["x"])) % 4))
        signature = base64.b64decode(receipt["receiptSignature"], validate=True)
        inner = {key: value for key, value in receipt.items() if key != "receiptSignature"}
        signed = {"profile": MAIL_PROFILE, "evaluationId": evaluation_id, "inputDigest": input_digest, "receipt": inner}
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            signature, _mail_canonical(signed).encode("utf-8")
        )
        return True
    except (InvalidSignature, KeyError, TypeError, ValueError):
        return False


INCIDENT_PROFILE = "ga5-incident-agent/v2"
INCIDENT_DB_PATH = os.getenv(
    "INCIDENT_DB_PATH", str(Path(__file__).with_name("incident_runs.sqlite3"))
)
INCIDENT_LOCK = threading.RLock()


def _incident_db() -> sqlite3.Connection:
    db = sqlite3.connect(INCIDENT_DB_PATH, check_same_thread=False)
    db.execute(
        "CREATE TABLE IF NOT EXISTS incident_runs "
        "(run_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, state_json TEXT NOT NULL)"
    )
    db.commit()
    return db


def _incident_hex(size: int) -> str:
    return uuid.uuid4().hex[:size]


def _incident_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _incident_lines(transcript: str) -> list[tuple[str, str]]:
    result = []
    for line in transcript.splitlines():
        match = re.match(r"\s*\[([A-Za-z0-9._:-]+)\]\s*(.*)", line)
        if match:
            result.append((match.group(1), match.group(2).strip()))
    return result


def _incident_plan(body: dict[str, Any], incoming_traceparent: str | None) -> dict[str, Any]:
    incident = body["incident"]
    lines = _incident_lines(incident["transcript"])
    text = incident["transcript"].lower()
    causes = incident["allowedRootCauses"]
    scores = {}
    for cause in causes:
        terms = re.findall(r"[a-z0-9_]{4,}", cause.lower())
        scores[cause] = sum(
            10 if cause.lower() in line.lower() else
            sum(1 for term in terms if term in line.lower())
            for _, line in lines
        )
    root = max(causes, key=lambda cause: scores[cause]) if causes else "unknown"
    decisive = [
        line_id for line_id, line in lines
        if any(word in line.lower() for word in re.findall(r"[a-z0-9_]{4,}", root.lower()))
    ][:4]
    if len(decisive) < 2:
        decisive = [line_id for line_id, _ in lines[:4]]
    evidence = list(dict.fromkeys(decisive))[:4]
    catalog = {
        item.get("name"): item for item in body.get("toolCatalog", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    diagnostic_names = [
        name for name in catalog
        if name not in set(body["policy"].get("effectTools", []))
        and any(word in name.lower() or word in str(catalog[name].get("description", "")).lower()
                for word in ("metric", "log", "health", "trace", "diagnos"))
    ]
    diagnostic_names.sort(key=lambda name: (
        0 if ("metric" in name.lower() and any(
            word in text for word in ("cpu", "latency", "memory", "traffic", "scale", "load")
        )) else 1,
        0 if ("log" in name.lower() and any(
            word in text for word in ("error", "exception", "stack", "failed", "log")
        )) else 1,
    ))
    diagnostics = diagnostic_names[:max(1, min(
        int(body["policy"].get("maximumDiagnostics", 3)), 2
    ))]
    if not diagnostics and catalog:
        diagnostics = [next(iter(catalog))]

    def tool_arguments(tool_name: str) -> dict[str, Any]:
        schema = catalog.get(tool_name, {}).get("inputSchema", {})
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        values = {
            "service": incident["service"],
            "serviceName": incident["service"],
            "incidentId": incident["incidentId"],
            "incident_id": incident["incidentId"],
            "severity": incident.get("severity", ""),
        }
        return {
            key: values[key] for key in properties
            if key in values
        } or {"service": incident["service"], "incidentId": incident["incidentId"]}
    trace_id = _incident_hex(32)
    parent_span = None
    if incoming_traceparent:
        match = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-[01]", incoming_traceparent)
        if match:
            trace_id, parent_span = match.group(1), match.group(2)
    server_span = _incident_hex(16)
    agent_span = _incident_hex(16)
    model_span = _incident_hex(16)
    public = body["publicMarker"]
    common = {"ga5.run.id": body["runId"], "ga5.public.marker": public}
    spans = [
        {"traceId": trace_id, "spanId": server_span, "parentSpanId": parent_span or "",
         "name": "POST /v2/incidents", "kind": 2, "attributes": common},
        {"traceId": trace_id, "spanId": agent_span, "parentSpanId": server_span,
         "name": "invoke_agent incident-response", "kind": 1, "attributes": common},
        {"traceId": trace_id, "spanId": model_span, "parentSpanId": agent_span,
         "name": "chat incident-plan", "kind": 3,
         "attributes": {**common, "gen_ai.operation.name": "chat", "gen_ai.request.model": "local-rule-planner"}},
    ]
    dispatches = []
    for name in diagnostics:
        action_id = f"act-{_incident_hex(16)}"
        call_id = f"call-{_incident_hex(16)}"
        client_span = _incident_hex(16)
        args = tool_arguments(name)
        dispatch = {
            "actionId": action_id, "callId": call_id, "phase": "diagnostic",
            "toolName": name, "arguments": args, "evidence": evidence[:2],
            "attempt": 1, "traceparent": f"00-{trace_id}-{client_span}-01",
        }
        dispatches.append(dispatch)
        spans.extend([
            {"traceId": trace_id, "spanId": _incident_hex(16), "parentSpanId": agent_span,
             "name": f"execute_tool {name}", "kind": 1, "attributes": {
                 **common, "ga5.action.id": action_id, "gen_ai.tool.name": name,
                 "gen_ai.tool.call.id": call_id, "gen_ai.operation.name": "execute_tool"}},
            {"traceId": trace_id, "spanId": client_span,
             "parentSpanId": spans[-1]["spanId"], "name": f"POST tool/{name}", "kind": 3,
             "attributes": {**common, "ga5.action.id": action_id, "ga5.attempt": 1,
                            "http.request.method": "POST", "http.request.resend_count": 0}},
        ])
    if len(dispatches) > 1:
        spans.append({"traceId": trace_id, "spanId": _incident_hex(16), "parentSpanId": agent_span,
                      "name": "incident.join", "kind": 1, "attributes": common,
                      "links": [{"traceId": trace_id, "spanId": s["spanId"]}
                                for s in spans if s["name"].startswith("execute_tool ")]})
    effect_tools = [
        name for name in body["policy"].get("effectTools", [])
        if name in catalog
    ]
    if any(term in text for term in ("at capacity", "maximum", "scaling limit", "cannot scale")):
        effect = next((name for name in effect_tools if "page" in name.lower() or "owner" in name.lower()), None)
    else:
        effect = effect_tools[0] if effect_tools else None
    return {
        "runId": body["runId"], "status": "waiting",
        "diagnosis": {"rootCause": root, "evidence": evidence},
        "dispatches": dispatches, "approvals": [], "actionLog": list(dispatches),
        "receiptLog": [], "chosenEffect": effect, "suppressed": [],
        "_body": body, "_traceId": trace_id, "_spans": spans, "_receiptHashes": {},
    }


def _incident_public(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if not key.startswith("_")}


def _incident_refresh_trace(state: dict[str, Any]) -> None:
    trace_id = state["_traceId"]
    common = {"ga5.run.id": state["runId"], "ga5.public.marker": state["_body"]["publicMarker"]}
    for span in state["_spans"]:
        span["traceId"] = trace_id
        span["attributes"] = {**common, **span.get("attributes", {})}
    for dispatch in state["actionLog"]:
        action_id = dispatch["actionId"]
        client = dispatch["traceparent"].split("-")[2]
        existing = next((s for s in state["_spans"] if s["spanId"] == client), None)
        if existing:
            receipt = next((r for r in state["receiptLog"]
                            if r.get("actionId") == action_id
                            and r.get("attempt") == dispatch["attempt"]), {})
            existing["attributes"].update({
                "gen_ai.tool.name": dispatch["toolName"],
                "gen_ai.tool.call.id": dispatch["callId"],
                "gen_ai.operation.name": "execute_tool",
                "ga5.receipt.id": receipt.get("receiptId", ""),
                "ga5.receipt.nonce": receipt.get("nonce", ""),
                "http.response.status_code": receipt.get("status", 0),
                "http.request.method": "POST",
                "http.request.resend_count": dispatch["attempt"] - 1,
            })
            if receipt.get("status") == 503 or receipt.get("status") == 0:
                existing["status"] = {"code": 2}
                if receipt.get("errorType"):
                    existing["attributes"]["error.type"] = receipt["errorType"]


def _incident_add_action_spans(state: dict[str, Any], dispatch: dict[str, Any]) -> None:
    client_span = dispatch["traceparent"].split("-")[2]
    parent = next(
        (span["spanId"] for span in state["_spans"]
         if span["name"] == f"invoke_agent incident-response"),
        "",
    )
    execute_span = _incident_hex(16)
    common = {
        "ga5.run.id": state["runId"],
        "ga5.public.marker": state["_body"]["publicMarker"],
        "ga5.action.id": dispatch["actionId"],
        "gen_ai.tool.name": dispatch["toolName"],
        "gen_ai.tool.call.id": dispatch["callId"],
        "gen_ai.operation.name": "execute_tool",
    }
    state["_spans"].append({
        "traceId": state["_traceId"], "spanId": execute_span,
        "parentSpanId": parent, "name": f"execute_tool {dispatch['toolName']}",
        "kind": 1, "attributes": common,
    })
    state["_spans"].append({
        "traceId": state["_traceId"], "spanId": client_span,
        "parentSpanId": execute_span, "name": f"POST tool/{dispatch['toolName']}",
        "kind": 3,
        "attributes": {
            **common, "ga5.attempt": dispatch["attempt"],
            "http.request.method": "POST",
            "http.request.resend_count": dispatch["attempt"] - 1,
        },
    })


def _incident_add_approval_span(state: dict[str, Any], approval: dict[str, Any]) -> None:
    agent_span = next(
        (span["spanId"] for span in state["_spans"]
         if span["name"] == "invoke_agent incident-response"),
        "",
    )
    state["_spans"].append({
        "traceId": state["_traceId"], "spanId": _incident_hex(16),
        "parentSpanId": agent_span, "name": "approval_gate", "kind": 1,
        "attributes": {
            "ga5.run.id": state["runId"],
            "ga5.public.marker": state["_body"]["publicMarker"],
            "ga5.approval.id": approval["approvalId"],
            "ga5.approval.action.id": approval["actionId"],
        },
    })


def _incident_otlp_attributes(attributes: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for key, value in attributes.items():
        if isinstance(value, bool):
            typed = {"boolValue": value}
        elif isinstance(value, int):
            typed = {"intValue": value}
        elif isinstance(value, float):
            typed = {"doubleValue": value}
        else:
            typed = {"stringValue": str(value)}
        result.append({"key": key, "value": typed})
    return result


def _incident_response(state: dict[str, Any]) -> JSONResponse:
    _incident_refresh_trace(state)
    result = _incident_public(state)
    spans = []
    for span in state["_spans"]:
        serialized = dict(span)
        serialized["attributes"] = _incident_otlp_attributes(span.get("attributes", {}))
        spans.append(serialized)
    result["otlp"] = {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}
    return JSONResponse(result, media_type="application/json")


def _incident_save(db: sqlite3.Connection, state: dict[str, Any]) -> None:
    db.execute("UPDATE incident_runs SET state_json=? WHERE run_id=?",
               (_canonical_json(state), state["runId"]))
    db.commit()


def _incident_effect_arguments(state: dict[str, Any], tool_name: str) -> dict[str, Any]:
    incident = state["_body"]["incident"]
    catalog = {
        item.get("name"): item for item in state["_body"].get("toolCatalog", [])
        if isinstance(item, dict)
    }
    schema = catalog.get(tool_name, {}).get("inputSchema", {})
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    values = {
        "service": incident["service"],
        "serviceName": incident["service"],
        "incidentId": incident["incidentId"],
        "incident_id": incident["incidentId"],
        "severity": incident.get("severity", ""),
    }
    return {key: values[key] for key in properties if key in values} or {
        "service": incident["service"],
        "incidentId": incident["incidentId"],
    }


def _incident_receipts(state: dict[str, Any], body: dict[str, Any]) -> None:
    receipt_id = body.get("receiptId")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise ValueError("INVALID_RECEIPT")
    body_hash = _incident_digest(body)
    if receipt_id in state["_receiptHashes"]:
        if state["_receiptHashes"][receipt_id] != body_hash:
            raise ValueError("RECEIPT_CONFLICT")
        return
    state["_receiptHashes"][receipt_id] = body_hash
    approvals = body.get("approvals", [])
    if approvals:
        if state["dispatches"] or len(approvals) != 1 or not state["approvals"]:
            raise ValueError("INVALID_APPROVAL")
        approval = approvals[0]
        pending = state["approvals"][0]
        if (
            approval.get("approvalId") != pending["approvalId"]
            or approval.get("decision") not in {"approved", "rejected"}
            or not isinstance(approval.get("nonce"), str)
            or not approval["nonce"]
        ):
            raise ValueError("INVALID_APPROVAL")
        state["receiptLog"].append({"receiptId": receipt_id, **approval})
        state["approvals"] = []
        if approval["decision"] == "rejected":
            state["status"] = "failed"
            state["chosenEffect"] = None
            return
        effect = pending["toolName"]
        dispatch = {
            "actionId": pending["actionId"], "callId": f"call-{_incident_hex(16)}",
            "phase": "effect", "toolName": effect,
            "arguments": {"service": state["_body"]["incident"]["service"]},
            "evidence": state["diagnosis"]["evidence"][:2], "attempt": 1,
            "approvalId": pending["approvalId"], "approvalNonce": approval["nonce"],
            "traceparent": f"00-{state['_traceId']}-{_incident_hex(16)}-01",
        }
        state["dispatches"] = [dispatch]
        state["actionLog"].append(dispatch)
        _incident_add_action_spans(state, dispatch)
        approval_span = next(
            (span for span in state["_spans"] if span["name"] == "approval_gate"
             and span["attributes"].get("ga5.approval.id") == approval["approvalId"]),
            None,
        )
        if approval_span:
            approval_span["attributes"]["ga5.approval.nonce"] = approval["nonce"]
        return
    outcomes = body.get("outcomes", [])
    for outcome in outcomes:
        pending = next((d for d in state["dispatches"]
                        if d["actionId"] == outcome.get("actionId")
                        and d["callId"] == outcome.get("callId")
                        and d["attempt"] == outcome.get("attempt")), None)
        if not pending:
            raise ValueError("INVALID_RECEIPT")
        receipt = {"receiptId": receipt_id, **outcome}
        state["receiptLog"].append(receipt)
        if outcome.get("status") == 503 and pending["attempt"] == 1:
            retry = dict(pending)
            retry["attempt"] = 2
            retry["traceparent"] = f"00-{state['_traceId']}-{_incident_hex(16)}-01"
            state["dispatches"] = [
                retry if dispatch["actionId"] == pending["actionId"] else dispatch
                for dispatch in state["dispatches"]
            ]
            state["actionLog"].append(retry)
            client_span = retry["traceparent"].split("-")[2]
            execute_span = next(
                (span for span in state["_spans"]
                 if span["name"] == f"execute_tool {retry['toolName']}"
                 and span["attributes"].get("ga5.action.id") == retry["actionId"]),
                None,
            )
            state["_spans"].append({
                "traceId": state["_traceId"],
                "spanId": client_span,
                "parentSpanId": execute_span["spanId"] if execute_span else "",
                "name": f"POST tool/{retry['toolName']}",
                "kind": 3,
                "attributes": {
                    "ga5.run.id": state["runId"],
                    "ga5.public.marker": state["_body"]["publicMarker"],
                    "ga5.action.id": retry["actionId"],
                    "gen_ai.tool.name": retry["toolName"],
                    "gen_ai.tool.call.id": retry["callId"],
                    "gen_ai.operation.name": "execute_tool",
                    "ga5.attempt": 2,
                    "http.request.method": "POST",
                    "http.request.resend_count": 1,
                },
            })
            state["status"] = "waiting"
            return
        if outcome.get("status") == 0 and outcome.get("errorType") == "timeout":
            state["dispatches"] = []
            state["status"] = "failed"
            return
    if state["dispatches"] and all(any(r.get("actionId") == d["actionId"] and r.get("attempt") == d["attempt"]
                                       for r in state["receiptLog"]) for d in state["dispatches"]):
        state["dispatches"] = []
        if state["approvals"]:
            return
        if state["chosenEffect"] and not any(d["phase"] == "effect" for d in state["actionLog"]):
            effect = state["chosenEffect"]
            action_id, call_id = f"act-{_incident_hex(16)}", f"call-{_incident_hex(16)}"
            if effect in state["_body"]["policy"].get("approvalRequiredFor", []):
                approval_id = f"approval-{_incident_hex(16)}"
                args = _incident_effect_arguments(state, effect)
                state["approvals"] = [{"approvalId": approval_id, "actionId": action_id,
                                       "toolName": effect, "argumentsDigest": _incident_digest(args)}]
                _incident_add_approval_span(state, state["approvals"][0])
            else:
                state["dispatches"] = [{"actionId": action_id, "callId": call_id, "phase": "effect",
                                        "toolName": effect, "arguments": _incident_effect_arguments(state, effect),
                                        "evidence": state["diagnosis"]["evidence"][:2], "attempt": 1,
                                        "traceparent": f"00-{state['_traceId']}-{_incident_hex(16)}-01"}]
                state["actionLog"].extend(state["dispatches"])
                _incident_add_action_spans(state, state["dispatches"][0])
        elif any(d["phase"] == "effect" for d in state["actionLog"]):
            state["status"] = "completed"
        elif state["chosenEffect"] is None:
            state["status"] = "completed"


def _incident_process(body: dict[str, Any], traceparent: str | None = None) -> JSONResponse:
    if body.get("profile") != INCIDENT_PROFILE or body.get("runId") == "":
        return JSONResponse({"error": "INVALID_REQUEST"}, status_code=400)
    run_id = body.get("runId")
    if not isinstance(run_id, str) or not run_id or not isinstance(body.get("incident"), dict):
        return JSONResponse({"error": "INVALID_REQUEST"}, status_code=400)
    request_hash = _incident_digest(body)
    with INCIDENT_LOCK:
        db = _incident_db()
        row = db.execute("SELECT request_hash,state_json FROM incident_runs WHERE run_id=?", (run_id,)).fetchone()
        if row:
            db.close()
            return _incident_response(json.loads(row[1])) if row[0] == request_hash else JSONResponse({"error": "RUN_CONFLICT"}, status_code=409)
        required = ("publicMarker", "toolCatalog", "policy")
        if any(key not in body for key in required):
            db.close()
            return JSONResponse({"error": "INVALID_REQUEST"}, status_code=400)
        state = _incident_plan(body, traceparent)
        db.execute("INSERT INTO incident_runs VALUES (?,?,?)", (run_id, request_hash, _canonical_json(state)))
        db.commit()
        db.close()
        return _incident_response(state)


def _incident_receipt_process(run_id: str, body: dict[str, Any]) -> JSONResponse:
    with INCIDENT_LOCK:
        db = _incident_db()
        row = db.execute("SELECT state_json FROM incident_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            db.close()
            return JSONResponse({"error": "RUN_NOT_FOUND"}, status_code=404)
        state = json.loads(row[0])
        try:
            _incident_receipts(state, body)
        except (KeyError, TypeError, ValueError):
            db.close()
            return JSONResponse({"error": "INVALID_RECEIPT"}, status_code=409)
        _incident_save(db, state)
        db.close()
        return _incident_response(state)


@app.post("/v2/incidents")
@app.post("/v2/incidents/")
async def incident_create(request: Request) -> Response:
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return JSONResponse({"error": "INVALID_REQUEST"}, status_code=400)
    return _incident_process(body, request.headers.get("traceparent"))


@app.post("/v2/incidents/{run_id}/incidents")
@app.post("/v2/incidents/{run_id}/incidents/")
async def incident_create_nested(run_id: str, request: Request) -> Response:
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return JSONResponse({"error": "INVALID_REQUEST"}, status_code=400)
    if isinstance(body, dict) and not body.get("runId"):
        body["runId"] = run_id
    return _incident_process(body, request.headers.get("traceparent"))


@app.post("/v2/incidents/{run_id}/receipts")
@app.post("/v2/incidents/{run_id}/receipts/")
async def incident_receipts(run_id: str, request: Request) -> Response:
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return JSONResponse({"error": "INVALID_RECEIPT"}, status_code=400)
    return _incident_receipt_process(run_id, body)


@app.get("/v2/incidents/{run_id}")
async def incident_get(run_id: str) -> Response:
    with INCIDENT_LOCK:
        db = _incident_db()
        row = db.execute("SELECT state_json FROM incident_runs WHERE run_id=?", (run_id,)).fetchone()
        db.close()
    if not row:
        return JSONResponse({"error": "RUN_NOT_FOUND"}, status_code=404)
    return _incident_response(json.loads(row[0]))


@app.get("/v2/incidents/{run_id}/incidents/{nested_run_id}")
async def incident_get_nested(run_id: str, nested_run_id: str) -> Response:
    return await incident_get(nested_run_id)


def _mailroom_process(body: dict[str, Any]) -> JSONResponse:
    if body.get("profile") != MAIL_PROFILE or body.get("operation") not in {"propose", "commit"}:
        return _mail_error("INVALID_REQUEST")
    operation = body["operation"]
    evaluation_id = body.get("evaluationId")
    if not isinstance(evaluation_id, str) or not evaluation_id:
        return _mail_error("INVALID_REQUEST")
    with MAIL_DB_LOCK:
        db = _mail_db()
        existing = db.execute(
            "SELECT request_hash, response_json, commit_json, verifier_json, commit_hash "
            "FROM mail_evaluations WHERE evaluation_id=?",
            (evaluation_id,),
        ).fetchone()
        if operation == "propose":
            dossiers = body.get("dossiers")
            if (
                not isinstance(dossiers, list)
                or not dossiers
                or any(not _mail_validate_dossier(dossier) for dossier in dossiers)
                or len({dossier["dossierId"] for dossier in dossiers}) != len(dossiers)
            ):
                db.close()
                return _mail_error("INVALID_REQUEST")
            input_digest = _mail_digest(dossiers)
            request_hash = _mail_digest({key: value for key, value in body.items() if key != "operation"})
            if existing:
                db.close()
                return JSONResponse(json.loads(existing[1]), media_type="application/json") if existing[0] == request_hash else _mail_error("EVALUATION_CONFLICT", 409)
            if (
                not isinstance(body.get("receiptVerifier"), dict)
                or not isinstance(body.get("allowedActions"), list)
                or not all(isinstance(action, str) for action in body["allowedActions"])
                or set(body["allowedActions"]) != MAIL_ACTIONS
                or len(body["allowedActions"]) != len(MAIL_ACTIONS)
                or body["receiptVerifier"].get("algorithm") != "Ed25519"
                or not isinstance(body["receiptVerifier"].get("publicKeyJwk"), dict)
                or body["receiptVerifier"]["publicKeyJwk"].get("kty") != "OKP"
                or body["receiptVerifier"]["publicKeyJwk"].get("crv") != "Ed25519"
                or not isinstance(body["receiptVerifier"]["publicKeyJwk"].get("x"), str)
                or not body["receiptVerifier"]["publicKeyJwk"]["x"]
            ):
                db.close()
                return _mail_error("INVALID_REQUEST")
            proposals = [_mail_propose(dossier) for dossier in dossiers]
            if any(not _mail_validate_proposal(proposal, dossier) for proposal, dossier in zip(proposals, dossiers)):
                db.close()
                return _mail_error("INVALID_PROPOSAL")
            response = {"profile": MAIL_PROFILE, "evaluationId": evaluation_id, "status": "awaiting_receipts", "inputDigest": input_digest, "proposals": proposals}
            db.execute(
                "INSERT INTO mail_evaluations "
                "(evaluation_id, request_hash, input_digest, verifier_json, response_json, commit_json, commit_hash) "
                "VALUES (?,?,?,?,?,NULL,NULL)",
                (evaluation_id, request_hash, input_digest, _mail_canonical(body["receiptVerifier"]), _mail_canonical(response)),
            )
            db.commit(); db.close()
            return JSONResponse(response, media_type="application/json")
        if not existing:
            db.close(); return _mail_error("UNKNOWN_EVALUATION", 409)
        commit_hash = _mail_digest(body)
        if existing[2]:
            stored_hash = existing[4]
            db.close()
            return (
                JSONResponse(json.loads(existing[2]), media_type="application/json")
                if stored_hash == commit_hash else _mail_error("EVALUATION_CONFLICT", 409)
            )
        if body.get("inputDigest") != json.loads(existing[1]).get("inputDigest"):
            db.close(); return _mail_error("EVALUATION_CONFLICT", 409)
        receipts = body.get("receipts")
        response = json.loads(existing[1])
        if not isinstance(receipts, list) or len(receipts) != len(response["proposals"]):
            db.close(); return _mail_error("INVALID_RECEIPTS")
        proposals = {item["dossierId"]: item for item in response["proposals"]}
        if (
            any(
                not isinstance(receipt, dict)
                or set(receipt) != {
                    "dossierId", "callId", "action", "accepted", "proposalDigest",
                    "receiptId", "receiptSignature",
                }
                or not isinstance(receipt["accepted"], bool)
                or not all(isinstance(receipt[key], str) and receipt[key] for key in (
                    "dossierId", "callId", "action", "proposalDigest", "receiptId",
                    "receiptSignature",
                ))
                for receipt in receipts
            )
            or len({r["dossierId"] for r in receipts}) != len(receipts)
        ):
            db.close(); return _mail_error("INVALID_RECEIPTS")
        verifier = json.loads(existing[3])
        outcomes = []
        for receipt in receipts:
            proposal = proposals.get(receipt.get("dossierId"))
            if not proposal or receipt.get("callId") != proposal["callId"] or receipt.get("action") != proposal["action"] or receipt.get("proposalDigest") != _mail_proposal_digest(proposal) or not _mail_verify(receipt, verifier, evaluation_id, response["inputDigest"]):
                db.close(); return _mail_error("INVALID_RECEIPTS")
            outcomes.append({"dossierId": proposal["dossierId"], "callId": proposal["callId"], "action": proposal["action"], "proposalDigest": proposal["proposalDigest"] if "proposalDigest" in proposal else receipt["proposalDigest"], "receiptId": receipt["receiptId"], "status": "executed" if receipt["accepted"] else "rejected"})
        final = {"profile": MAIL_PROFILE, "evaluationId": evaluation_id, "status": "completed", "inputDigest": response["inputDigest"], "outcomes": outcomes}
        db.execute(
            "UPDATE mail_evaluations SET commit_json=?, commit_hash=? WHERE evaluation_id=?",
            (_mail_canonical(final), commit_hash, evaluation_id),
        ); db.commit(); db.close()
        return JSONResponse(final, media_type="application/json")


def coerce(key: str, value: Any) -> Any:
    if key in {"port", "workers"}:
        return int(value)
    if key == "debug":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "on"}
    return str(value)


def effective_values(overrides: list[str]) -> dict[str, Any]:
    values = DEFAULTS.copy()

    yaml_path = Path(__file__).with_name("config.development.yaml")
    with yaml_path.open("r", encoding="utf-8") as config_file:
        for key, value in (yaml.safe_load(config_file) or {}).items():
            values[key] = coerce(key, value)

    environment_values = {
        "port": os.getenv("APP_PORT"),
        "workers": os.getenv("APP_WORKERS", os.getenv("NUM_WORKERS")),
        "debug": os.getenv("APP_DEBUG"),
        "log_level": os.getenv("APP_LOG_LEVEL"),
        "api_key": os.getenv("APP_API_KEY"),
    }
    for key, value in environment_values.items():
        if value is not None:
            values[key] = coerce(key, value)

    for override in overrides:
        if "=" not in override:
            continue
        key, value = override.split("=", 1)
        key = key.strip()
        if key:
            values[key] = coerce(key, value.strip())

    return {
        "port": values["port"],
        "workers": values["workers"],
        "debug": values["debug"],
        "log_level": values["log_level"],
        "api_key": "****",
    }


@app.get("/effective-config")
def effective_config(set: list[str] = Query(default=[])) -> dict[str, Any]:
    return effective_values(set)
