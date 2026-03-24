from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx

app = FastAPI(title="Mining Monitor Proxy", version="2.6")

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
    if not val:
        return ""
    s = str(val)
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    return ""


def to_btc(val):
    try:
        f = float(val)
        if f > 100:
            return f / 100_000_000
        return f
    except:
        return 0.0


async def send_telegram(token: str, chat_id: str, message: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
    except:
        pass


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
        # fallback v1
        p2 = {"fromDate": str(from_date), "toDate": str(to_date), "page": 0, "size": 200}
        if subaccount:
            p2["subaccountNames"] = subaccount
        async with httpx.AsyncClient(timeout=15) as c2:
            resp = await c2.get(f"{BASE}/api/external/v1/earnings", params=p2, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data    = resp.json()
    content = data.get("content", data if isinstance(data, list) else [])

    result = []
    for e in content:
        # earningsFor = "2022-03-23T00:00:00.000+00:00"
        raw_date = (e.get("earningsFor") or e.get("paidOn") or
                    e.get("date") or e.get("earningDate") or "")
        date_str = parse_date(raw_date)

        # netOwed already in BTC
        amount = to_btc(e.get("netOwed") or e.get("amount") or e.get("totalEarnings") or 0)
        fee    = to_btc(e.get("fee") or e.get("feesPaid") or e.get("poolFee") or 0)

        result.append({
            "date":       date_str,
            "amount":     f"{amount:.8f}",
            "fee":        f"{fee:.8f}",
            "status":     e.get("state") or e.get("status") or "CONFIRMED",
            "subaccount": e.get("subaccountName") or subaccount,
            "coin":       e.get("coin", "BTC"),
        })
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


@app.get("/estimated")
async def get_estimated(request: Request, subaccount: str = ""):
    headers = auth_headers(request)
    params  = {}
    if subaccount:
        params["subaccountNames"] = subaccount

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE}/api/external/v1/revenue", params=params, headers=headers)

    if resp.status_code != 200:
        return {"today_estimated": 0.0, "yesterday_estimated": 0.0}

    data      = resp.json()
    estimated = data.get("estimatedRevenues", {})
    dates     = sorted(estimated.keys(), reverse=True)

    today_amt = 0.0
    yest_amt  = 0.0

    for i, date_key in enumerate(dates[:2]):
        entries = estimated[date_key]
        total   = sum(to_btc(e.get("amount") or 0) for e in entries)
        if i == 0:
            today_amt = total
        else:
            yest_amt  = total

    return {"today_estimated": round(today_amt, 8), "yesterday_estimated": round(yest_amt, 8)}


@app.get("/payments")
async def get_payments(request: Request, subaccount: str = "", days: int = 90):
    headers   = auth_headers(request)
    to_date   = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days)

    params = {"startDate": str(from_date), "endDate": str(to_date), "page": 0, "size": 100}
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
        raw_date = (p.get("paidOn") or p.get("date") or p.get("paymentDate") or "")
        date_str = parse_date(raw_date)
        amount   = to_btc(p.get("amount") or p.get("totalAmount") or 0)

        result.append({
            "date":    date_str,
            "amount":  f"{amount:.8f}",
            "txId":    p.get("txId") or p.get("transactionId") or "—",
            "address": p.get("address") or p.get("payoutAddress") or "—",
            "status":  p.get("state") or p.get("status") or "CONFIRMED",
            "coin":    p.get("coin", "BTC"),
        })
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


@app.post("/alert")
async def send_alert(request: Request):
    """Called by frontend to send Telegram alert"""
    body = await request.json()
    token   = body.get("token", "")
    chat_id = body.get("chatId", "")
    message = body.get("message", "")

    if not token or not chat_id or not message:
        raise HTTPException(status_code=400, detail="Missing token, chatId or message")

    await send_telegram(token, chat_id, message)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}
