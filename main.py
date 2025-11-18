import os
import json
import random
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import requests

from database import db, create_document, get_documents

app = FastAPI(title="VoidSpark.world API", version="0.1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# Models (request/response)
# -----------------------------
class WalletLoginRequest(BaseModel):
    address: str

class CreateListingRequest(BaseModel):
    seller: str
    item_id: str
    price: float

class TradeActionRequest(BaseModel):
    wallet: str
    listing_id: str

class ItemCreate(BaseModel):
    owner: str
    name: str
    rarity: str = Field(default="common")
    stats: dict = Field(default_factory=dict)

class BuySellRequest(BaseModel):
    wallet: str
    item_id: str
    price: float

class MintItemRequest(BaseModel):
    wallet: str
    name: str
    attributes: dict = Field(default_factory=dict)


# -----------------------------
# Utility: simple AI generators
# -----------------------------
def generate_quest(seed: Optional[int] = None) -> dict:
    rnd = random.Random(seed or random.randint(1, 10_000_000))
    targets = ["Sentinel", "Wraith", "Marauder", "Crawler", "Revenant"]
    zones = ["Obsidian Flats", "Neon Mire", "Fracture Ridge", "Echo Dunes"]
    rewards = [
        {"type": "token", "amount": rnd.randint(5, 25)},
        {"type": "item", "name": rnd.choice(["Ion Blade", "Aether Core", "Flux Capacitor"])},
    ]
    quest = {
        "title": f"Cull the {rnd.choice(targets)}",
        "zone": rnd.choice(zones),
        "objective": "Eliminate hostiles and recover components",
        "target_count": rnd.randint(3, 12),
        "reward": rnd.choice(rewards),
        "expires_at": datetime.now(timezone.utc).isoformat(),
    }
    return quest


def generate_zone(seed: Optional[int] = None) -> dict:
    rnd = random.Random(seed or random.randint(1, 10_000_000))
    weather = rnd.choice(["ion-storm", "clear", "acid-rain", "solar-flare", "dust"])
    density = rnd.uniform(0.1, 1.0)
    resources = [
        {"type": "ferrocrete", "richness": round(rnd.uniform(0, 1), 2)},
        {"type": "aether", "richness": round(rnd.uniform(0, 1), 2)},
        {"type": "plasma", "richness": round(rnd.uniform(0, 1), 2)},
    ]
    return {
        "name": f"Zone-{rnd.randint(100,999)}",
        "weather": weather,
        "enemy_density": round(density, 2),
        "resources": resources,
    }


# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def root():
    return {"service": "VoidSpark.world Backend", "status": "ok"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = os.getenv("DATABASE_NAME") or "❌ Not Set"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
                response["connection_status"] = "Connected"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# Wallet auth (challenge-less demo: link address)
@app.post("/api/player/login-wallet")
def login_wallet(payload: WalletLoginRequest):
    if not payload.address or len(payload.address) < 20:
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    player_doc = {
        "address": payload.address,
        "last_login": datetime.now(timezone.utc),
        "stats": {"level": 1, "xp": 0, "hp": 100},
    }
    create_document("player", player_doc)
    return {"ok": True, "player": {"address": payload.address}}


# AI endpoints
@app.get("/api/ai/quest")
def ai_quest(seed: Optional[int] = None):
    quest = generate_quest(seed)
    create_document("quest", {**quest, "seed": seed or None})
    return quest


@app.get("/api/ai/zone")
def ai_zone(seed: Optional[int] = None):
    zone = generate_zone(seed)
    create_document("zone", {**zone, "seed": seed or None})
    return zone


# Items & inventory
@app.post("/api/item/create")
def create_item(item: ItemCreate):
    doc = item.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    doc["updated_at"] = datetime.now(timezone.utc)
    _id = create_document("item", doc)
    return {"ok": True, "id": _id}


@app.get("/api/inventory/{wallet}")
def get_inventory(wallet: str):
    items = get_documents("item", {"owner": wallet})
    # convert ObjectIds to strings if needed
    def normalize(doc):
        d = dict(doc)
        if "_id" in d:
            d["_id"] = str(d["_id"])
        return d
    return {"items": [normalize(i) for i in items]}


# Marketplace
@app.get("/api/marketplace/listings")
def listings():
    docs = get_documents("listing", {})
    def n(d):
        d = dict(d)
        d["_id"] = str(d.get("_id"))
        return d
    return {"listings": [n(x) for x in docs]}


@app.post("/api/marketplace/create")
def create_listing(payload: CreateListingRequest):
    listing = payload.model_dump()
    listing["created_at"] = datetime.now(timezone.utc)
    listing["status"] = "open"
    _id = create_document("listing", listing)
    return {"ok": True, "id": _id}


@app.post("/api/marketplace/buy")
def buy_listing(payload: TradeActionRequest):
    doc = {
        "action": "buy",
        "wallet": payload.wallet,
        "listing_id": payload.listing_id,
        "time": datetime.now(timezone.utc),
    }
    create_document("trade", doc)
    return {"ok": True}


# SPL-token related endpoints (lightweight, free RPC)
SOLANA_RPC = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")

@app.get("/getBalance")
def get_balance(address: str = Query(..., description="Solana wallet address")):
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [address],
        }
        r = requests.post(SOLANA_RPC, json=payload, timeout=8)
        lamports = r.json().get("result", {}).get("value", 0) if r.ok else 0
        return {"address": address, "lamports": lamports, "sol": lamports / 1e9}
    except Exception as e:
        return {"address": address, "lamports": 0, "sol": 0, "error": str(e)[:120]}


@app.post("/buyItem")
def buy_item(req: BuySellRequest):
    doc = req.model_dump()
    doc["action"] = "buy"
    doc["time"] = datetime.now(timezone.utc)
    create_document("token_trades", doc)
    return {"ok": True}


@app.post("/sellItem")
def sell_item(req: BuySellRequest):
    doc = req.model_dump()
    doc["action"] = "sell"
    doc["time"] = datetime.now(timezone.utc)
    create_document("token_trades", doc)
    return {"ok": True}


@app.post("/mintItemNFT")
def mint_item_nft(req: MintItemRequest):
    # Placeholder: In production, this should be done client-side with wallet signature
    # and an Anchor program. Here we just log intent for automation pipeline.
    record = {
        "wallet": req.wallet,
        "name": req.name,
        "attributes": req.attributes,
        "intent": "mint_nft",
        "time": datetime.now(timezone.utc),
    }
    create_document("nft_mint_intents", record)
    return {"ok": True, "note": "Recorded mint intent. Use deployment scripts to mint on-chain."}


# WebSocket: simple pub-sub for world events
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, message: dict):
        data = json.dumps(message)
        for connection in list(self.active):
            try:
                await connection.send_text(data)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    await manager.broadcast({"type": "system", "message": "Player joined"})
    try:
        while True:
            text = await websocket.receive_text()
            await manager.broadcast({"type": "chat", "text": text, "ts": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast({"type": "system", "message": "Player left"})


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
