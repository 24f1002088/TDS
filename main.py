import os
import hashlib
import csv
import io
import json
import re
from datetime import datetime
from urllib.request import urlopen
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

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


def load_ledger_rows() -> list[dict[str, Any]]:
    if not LEDGER_ORDERS_URL:
        return []
    with urlopen(LEDGER_ORDERS_URL, timeout=10) as response:
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
            float(row.get("refund_amount", row.get("amount", row.get("total", 0))) or 0) for row in refunded
        )
    paid = [row for row in filtered if str(row.get("status", "")).lower() == "paid"]
    if "revenue" in lowered or "sales" in lowered or "total" in lowered:
        return round(
            sum(float(row.get("amount", row.get("total", row.get("revenue", 0))) or 0) for row in paid),
            2,
        )
    return len(filtered)


@app.post("/")
@app.post("/answer")
async def answer_question(request: Request) -> dict[str, Any]:
    payload = await request.json()
    return {"answer": answer_ledger_question(str(payload.get("question", "")))}


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
