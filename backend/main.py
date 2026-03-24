"""
Mining Monitor - Backend Proxy
Deploy gratuito no Render.com

Endpoints:
  GET /workers   → lista workers com status/hashrate
  GET /earnings  → histórico de earnings dos últimos 30 dias
  GET /payments  → histórico de pagamentos
  GET /health    → healthcheck (usado pelo cron de keep-alive)

Deploy no Render.com:
  Runtime:       Python 3
  Build Command: pip install -r requirements.txt
  Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx

app = FastAPI(title="Mining Monitor Proxy", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Em produção: restringe ao teu domínio GitHub Pages
    allow_methods=["GET"],
    allow_headers=["*"],
)

BASE = "https://pool-api.sbicrypto.com"


def auth_headers(request: Request):
    key    = request.headers.get("x-api-key")
    secret = request.headers.get("x-api-secret")
    if not key or not secret:
        raise HTTPException(status_code=401, detail="Missing x-api-key or x-api-secret headers")
    return {"x-api-key": key, "x-api-secret": secret, "Accept": "application/json"}


# ── WORKERS ──────────────────────────────────────────────────────────────────
@app.get("/workers")
async def get_workers(request: Request, subaccount: str = ""):
    headers = auth_headers(request)
    params  = {"size": 200}
    if subaccount:
        params["subaccountNames"] = subaccount

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/api/external/v1/workers", params=params, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data    = resp.json()
    content = data.get("content", data if isinstance(data, list) else [])

    return [
        {
            "name":          w.get("name", "unknown"),
            "status":        w.get("status", "DEAD"),
            "hashrate":      float(w.get("hashrate1HrAvg") or w.get("hashrate") or 0),
            "lastShareTime": w.get("lastShareTime"),
            "subaccount":    w.get("subaccountName", subaccount),
        }
        for w in content
    ]


# ── EARNINGS ─────────────────────────────────────────────────────────────────
@app.get("/earnings")
async def get_earnings(request: Request, subaccount: str = "", days: int = 30):
    headers  = auth_headers(request)
    to_date  = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days)

    params = {
        "fromDate":       str(from_date),
        "toDate":         str(to_date),
        "page":           0,
        "size":           200,
    }
    if subaccount:
        params["subaccountNames"] = subaccount

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/api/external/v1/earnings", params=params, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data    = resp.json()
    content = data.get("content", data if isinstance(data, list) else [])

    result = []
    for e in content:
        # Normalise date field (may be string or datetime)
        raw_date = e.get("date") or e.get("earningDate") or e.get("createdAt") or ""
        if isinstance(raw_date, str) and "T" in raw_date:
            raw_date = raw_date.split("T")[0]

        result.append({
            "date":       raw_date,
            "amount":     str(e.get("amount") or e.get("totalEarnings") or 0),
            "fee":        str(e.get("fee") or e.get("poolFee") or 0),
            "status":     e.get("status", "CONFIRMED"),
            "subaccount": e.get("subaccountName", subaccount),
            "coin":       e.get("coin", "BTC"),
        })

    # Sort newest first
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


# ── PAYMENTS ─────────────────────────────────────────────────────────────────
@app.get("/payments")
async def get_payments(request: Request, subaccount: str = "", days: int = 90):
    headers   = auth_headers(request)
    to_date   = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days)

    params = {
        "startDate": str(from_date),
        "endDate":   str(to_date),
        "page":      0,
        "size":      100,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/api/external/v1/payouts", params=params, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data    = resp.json()
    content = data.get("content", data if isinstance(data, list) else [])

    result = []
    for p in content:
        raw_date = p.get("date") or p.get("paidDate") or p.get("createdAt") or ""
        if isinstance(raw_date, str) and "T" in raw_date:
            raw_date = raw_date.split("T")[0]

        result.append({
            "date":    raw_date,
            "amount":  str(p.get("amount") or p.get("totalAmount") or 0),
            "txId":    p.get("txId") or p.get("transactionId") or "—",
            "address": p.get("address") or p.get("payoutAddress") or "—",
            "status":  p.get("status", "CONFIRMED"),
            "coin":    p.get("coin", "BTC"),
        })

    result.sort(key=lambda x: x["date"], reverse=True)
    return result


# ── HEALTH ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}
