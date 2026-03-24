from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx

app = FastAPI(title="Mining Monitor Proxy", version="2.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

BASE = "https://pool-api.sbicrypto.com"
SATOSHI = 100_000_000


def auth_headers(request: Request):
    key    = request.headers.get("x-api-key")
    secret = request.headers.get("x-api-secret")
    if not key or not secret:
        raise HTTPException(status_code=401, detail="Missing x-api-key or x-api-secret headers")
    return {"x-api-key": key, "x-api-secret": secret, "Accept": "application/json"}


def parse_date(val):
    if not val:
        return ""
    s = str(val)
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    try:
        ts = int(float(s))
        if ts > 1e10:
            ts = ts // 1000
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    except:
        pass
    return s[:10] if len(s) >= 10 else s


def to_btc(val):
    try:
        f = float(val)
        if f > 1000:
            return f / SATOSHI
        return f
    except:
        return 0.0


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

    result = []
    for w in content:
        state = (w.get("state") or w.get("status") or "DEAD").upper()
        hashrates = w.get("hashrates", [])
        if isinstance(hashrates, list) and len(hashrates) >= 2:
            hr_1h = float(hashrates[1] or 0) / 1_000_000
            hr_1d = float(hashrates[2] or 0) / 1_000_000 if len(hashrates) > 2 else hr_1h
        else:
            hr_1h = float(w.get("hashrate") or 0)
            hr_1d = hr_1h

        result.append({
            "name":          w.get("name") or "unknown",
            "status":        state,
            "hashrate":      round(hr_1h, 4),
            "hashrateAvg":   round(hr_1d, 4),
            "lastShareTime": w.get("lastShareTime") or w.get("lastShare"),
            "subaccount":    w.get("subaccount") or w.get("subaccountName", subaccount),
        })
    return result


@app.get("/earnings")
async def get_earnings(request: Request, subaccount: str = "", days: int = 30):
    headers   = auth_headers(request)
    to_date   = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days)

    params = {"fromDate": str(from_date), "toDate": str(to_date), "page": 0, "size": 200}
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
        raw_date = (e.get("date") or e.get("earningDate") or e.get("paidDate") or
                    e.get("createdAt") or e.get("timestamp") or "")
        date_str = parse_date(raw_date)
        amount_btc = to_btc(e.get("amount") or e.get("totalEarnings") or e.get("earnedAmount") or 0)
        fee_btc    = to_btc(e.get("fee") or e.get("poolFee") or 0)
        result.append({
            "date":       date_str,
            "amount":     f"{amount_btc:.8f}",
            "fee":        f"{fee_btc:.8f}",
            "status":     e.get("status", "CONFIRMED"),
            "subaccount": e.get("subaccountName", subaccount),
            "coin":       e.get("coin", "BTC"),
        })
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


@app.get("/estimated")
async def get_estimated(request: Request, subaccount: str = ""):
    """Estimated revenue for today - increases throughout the day"""
    headers = auth_headers(request)
    params  = {}
    if subaccount:
        params["subaccountNames"] = subaccount

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/api/external/v1/estimatedrevenue", params=params, headers=headers)

    if resp.status_code != 200:
        # fallback - try subaccounts endpoint for estimated
        try:
            resp2 = await client.get(f"{BASE}/api/external/v1/subaccounts",
                params=params, headers=headers)
            if resp2.status_code == 200:
                data = resp2.json()
                subs = data if isinstance(data, list) else data.get("subaccounts", [])
                total_est = 0.0
                for s in subs:
                    est = s.get("estimatedRevenue") or s.get("estimatedEarnings") or 0
                    total_est += to_btc(est)
                return {"today_estimated": round(total_est, 8), "source": "subaccounts"}
        except:
            pass
        return {"today_estimated": 0.0, "source": "unavailable"}

    data = resp.json()
    content = data.get("content", data if isinstance(data, list) else [])
    total = 0.0
    for e in content:
        val = e.get("estimatedRevenue") or e.get("amount") or e.get("revenue") or 0
        total += to_btc(val)
    return {"today_estimated": round(total, 8), "source": "estimatedrevenue"}


@app.get("/payments")
async def get_payments(request: Request, subaccount: str = "", days: int = 90):
    headers   = auth_headers(request)
    to_date   = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days)

    params = {"startDate": str(from_date), "endDate": str(to_date), "page": 0, "size": 100}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/api/external/v1/payouts", params=params, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data    = resp.json()
    content = data.get("content", data if isinstance(data, list) else [])

    result = []
    for p in content:
        raw_date = (p.get("date") or p.get("paidDate") or p.get("paymentDate") or
                    p.get("createdAt") or p.get("timestamp") or "")
        date_str = parse_date(raw_date)
        amount_btc = to_btc(p.get("amount") or p.get("totalAmount") or p.get("paidAmount") or 0)
        result.append({
            "date":    date_str,
            "amount":  f"{amount_btc:.8f}",
            "txId":    p.get("txId") or p.get("transactionId") or p.get("txHash") or "—",
            "address": p.get("address") or p.get("payoutAddress") or p.get("toAddress") or "—",
            "status":  p.get("status", "CONFIRMED"),
            "coin":    p.get("coin", "BTC"),
        })
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}
