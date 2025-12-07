# server.py
import os
import math
import json
import time
from typing import Dict, Tuple
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# NEW IMPORTS FOR SERVING HTML
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

PORT = int(os.getenv("PORT", "8080"))
DEFAULT_RADIUS_KM = float(os.getenv("DEFAULT_RADIUS_KM", "0.2"))
PRESENCE_TTL = int(os.getenv("PRESENCE_TTL", "120"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]

app = FastAPI(title="Standalone Proximity Flag")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS if CORS_ORIGINS != ["*"] else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# In-memory presence: user_id -> (lat, lon, last_seen_ts)
presence: Dict[str, Tuple[float, float, float]] = {}

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

def prune_stale(ttl_seconds: int = PRESENCE_TTL):
    now = time.time()
    stale = [uid for uid, (_, _, ts) in presence.items() if now - ts > ttl_seconds]
    for uid in stale:
        presence.pop(uid, None)

@app.get("/health")
def health():
    prune_stale()
    return {"ok": True, "active": len(presence)}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    me_id = None
    radius_km = DEFAULT_RADIUS_KM
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "identify":
                me_id = str(msg.get("user_id", "")).strip()
                if not me_id:
                    await ws.send_text(json.dumps({"type": "error", "error": "missing_user_id"}))
                    continue
                if "radius_km" in msg:
                    try:
                        radius_km = max(0.01, float(msg["radius_km"]))
                    except Exception:
                        pass
                await ws.send_text(json.dumps({"type": "ack", "ok": True, "radius_km": radius_km}))
                continue

            if mtype == "location":
                if not me_id:
                    await ws.send_text(json.dumps({"type": "error", "error": "identify_first"}))
                    continue
                try:
                    lat = float(msg["latitude"])
                    lon = float(msg["longitude"])
                except Exception:
                    await ws.send_text(json.dumps({"type": "error", "error": "invalid_coordinates"}))
                    continue
                
                print("UPDATED:", me_id, lat, lon)

                presence[me_id] = (lat, lon, time.time())
                prune_stale()

                found = False
                count = 0
                samples = []

                for uid, (olat, olon, _) in presence.items():
                    if uid == me_id:
                        continue
                    d = haversine_km(lat, lon, olat, olon)
                    if d <= radius_km:
                        found = True
                        count += 1
                        if len(samples) < 5:
                            samples.append({"user_id": uid, "distance_km": round(d, 3)})

                await ws.send_text(json.dumps({
                    "type": "proximity",
                    "found_nearby": found,
                    "count_nearby": count,
                    "sample": samples
                }))
                continue

            if mtype == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        # Just log; let prune_stale() remove them after PRESENCE_TTL seconds
        print("WebSocket disconnected for", me_id)
        pass



# ============================================
# NEW: Serve client.html on root
# ============================================

# Serve any static files (HTML, JS, CSS)
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
def serve_client():
    return FileResponse("client.html")


# Run locally if needed
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=True)
