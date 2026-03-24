from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx

app = FastAPI(title="Mining Monitor Proxy", version="2.5")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


def parse_date(val):
    """Extract YYYY-MM-DD from ISO date string"""
    if not val:
        return ""
    s = str(val)
    # Format: 2022-03-23T00:00:00.000+00:00
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    return ""


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
        # hashrates array [10m, 1h, 1d] in MH/s → TH/s
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

    # API uses startDate/endDate (v2 endpoint)
    params = {
        "startDate": str(from_date),
        "endDate":   str(to_date),
        "page":      0,
        "size":      200,
    }
    if subaccount:
        params["vSubaccounts"] = subaccount

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/api/external/v2/earnings", params=params, headers=headers)

    if resp.status_code != 200:
        # fallback to v1
        params2 = {"fromDate": str(from_date), "toDate": str(to_date), "page": 0, "size": 200}
        if subaccount:
            params2["subaccountNames"] = subaccount
        async with httpx.AsyncClient(timeout=15) as client2:
            resp = await client2.get(f"{BASE}/api/external/v1/earnings", params=params2, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data    = resp.json()
    content = data.get("content", data if isinstance(data, list) else [])

    result = []
    for e in content:
        # earningsFor = "2022-03-23T00:00:00.000+00:00"
        raw_date = (e.get("earningsFor") or e.get("paidOn") or
                    e.get("date") or e.get("earningDate") or e.get("createdAt") or "")
        date_str = parse_date(raw_date)

        # netOwed is already in BTC (whole coin)
        amount = float(e.get("netOwed") or e.get("amount") or e.get("totalEarnings") or 0)
        fee    = float(e.get("fee") or e.get("poolFee") or e.get("feesPaid") or 0)

        # If values seem to be in satoshis (very large), convert
        if amount > 100:
            amount = amount / 100_000_000
        if fee > 100:
            fee = fee / 100_000_000

        result.append({
            "date":       date_str,
            "amount":     f"{amount:.8f}",
            "fee":        f"{fee:.8f}",
            "status":     e.get("state") or e.get("status") or "CONFIRMED",
            "subaccount": e.get("subaccountName") or subaccount,
            "coin":       e.get("coin", "BTC"),
            "hashrate":   e.get("hashrate", 0),
            "scheme":     e.get("earningScheme", "FPPS"),
        })
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


@app.get("/estimated")
async def get_estimated(request: Request, subaccount: str = ""):
    """Today and yesterday estimated revenue"""
    headers = auth_headers(request)
    params  = {}
    if subaccount:
        params["subaccountNames"] = subaccount

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/api/external/v1/revenue", params=params, headers=headers)

    if resp.status_code != 200:
        return {"today_estimated": 0.0, "yesterday_estimated": 0.0}

    data = resp.json()
    # Response: {"estimatedRevenues": {"2022-01-20T00:...": [...], "2022-01-19T00:...": [...]}}
    estimated = data.get("estimatedRevenues", {})

    dates = sorted(estimated.keys(), reverse=True)
    today_amt = 0.0
    yest_amt  = 0.0

    for i, date_key in enumerate(dates[:2]):
        entries = estimated[date_key]
        total = 0.0
        for e in entries:
            amt = float(e.get("amount") or 0)
            if amt > 100:
                amt = amt / 100_000_000
            total += amt
        if i == 0:
            today_amt = total
        else:
            yest_amt = total

    return {
        "today_estimated":     round(today_amt, 8),
        "yesterday_estimated": round(yest_amt, 8),
    }


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
    if subaccount:
        params["vSubaccounts"] = subaccount

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/api/external/v1/payouts", params=params, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data    = resp.json()
    content = data.get("content", data if isinstance(data, list) else [])

    result = []
    for p in content:
        # paidOn = "2022-03-23T20:30:04.107+00:00"
        raw_date = (p.get("paidOn") or p.get("date") or p.get("paymentDate") or
                    p.get("createdAt") or "")
        date_str = parse_date(raw_date)

        amount = float(p.get("amount") or p.get("totalAmount") or 0)
        if amount > 100:
            amount = amount / 100_000_000

        result.append({
            "date":    date_str,
            "amount":  f"{amount:.8f}",
            "txId":    p.get("txId") or p.get("transactionId") or p.get("txHash") or "—",
            "address": p.get("address") or p.get("payoutAddress") or "—",
            "status":  p.get("state") or p.get("status") or "CONFIRMED",
            "coin":    p.get("coin", "BTC"),
        })
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}
