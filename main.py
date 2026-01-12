import asyncio
import datetime
import json
import os
from collections import deque, defaultdict
from typing import List, Optional
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pytz
import uuid

app = FastAPI()

if not os.path.exists("static"): os.makedirs("static")
if not os.path.exists("templates"): os.makedirs("templates")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_FILE = "chats.json"
KST = pytz.timezone('Asia/Seoul')

class Message(BaseModel):
    msg_id: str
    room_name: str
    sender: str
    content: str
    timestamp: int
    profile: Optional[str] = ""

def format_time(ts_ms):
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000.0, KST)
    return dt.strftime("%p %I:%M")

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                db = defaultdict(list, data.get("chats", {}))
                unread = defaultdict(int, data.get("unread", {}))
                processed = set(data.get("processed_ids", []))
                profiles = defaultdict(str, data.get("profiles", {}))
                return db, unread, processed, profiles
        except:
            return defaultdict(list), defaultdict(int), set(), defaultdict(str)
    return defaultdict(list), defaultdict(int), set(), defaultdict(str)

def save_db():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "chats": chat_db, 
                "unread": unread_db,
                "processed_ids": list(processed_ids),
                "profiles": dict(profile_db)
            }, f, ensure_ascii=False, indent=2)
    except:
        pass

chat_db, unread_db, processed_ids, profile_db = load_db()
reply_queue = deque()
read_queue = deque()
new_msg_event = asyncio.Event()

@app.get("/", response_class=RedirectResponse)
async def root():
    return "/chats"

@app.get("/chats", response_class=HTMLResponse)
async def read_chats(request: Request):
    summary = []
    rooms = sorted(chat_db.items(), key=lambda x: x[1][-1]['timestamp'] if x[1] else 0, reverse=True)
    for room, msgs in rooms:
        if msgs:
            summary.append({
                "target": room, 
                "last": msgs[-1],
                "unread_count": unread_db.get(room, 0),
                "profile": profile_db.get(room, "")
            })
    return templates.TemplateResponse("chats.html", {"request": request, "chats": summary})

@app.get("/chat/{target}", response_class=HTMLResponse)
async def read_chat(request: Request, target: str):
    unread_db[target] = 0
    save_db()
    if target not in read_queue:
        read_queue.append(target)
    new_msg_event.set()
    new_msg_event.clear()
    return templates.TemplateResponse("chat.html", {"request": request, "target": target, "profile": profile_db.get(target, "")})

@app.post("/read/{target}")
async def mark_as_read(target: str):
    unread_db[target] = 0
    save_db()
    if target not in read_queue:
        read_queue.append(target)
    new_msg_event.set()
    new_msg_event.clear()
    return {"status": "ok"}

@app.post("/push")
async def receive_from_android(msg: Message):
    if msg.msg_id in processed_ids:
        return {"status": "duplicate"}

    processed_ids.add(msg.msg_id)
    
    if msg.profile:
        profile_db[msg.room_name] = msg.profile

    room = chat_db[msg.room_name]
    room.append({
        "msg_id": msg.msg_id,
        "is_me": False,
        "sender": msg.sender,
        "content": msg.content,
        "time": format_time(msg.timestamp),
        "timestamp": msg.timestamp
    })
    unread_db[msg.room_name] += 1
    
    if len(room) > 100: chat_db[msg.room_name] = room[-100:]
    if len(processed_ids) > 1000:
        ids_list = list(processed_ids)
        processed_ids.clear()
        processed_ids.update(ids_list[-500:])

    save_db()
    new_msg_event.set()
    new_msg_event.clear()
    return {"status": "ok"}

@app.post("/send")
async def send_from_bb(sender: str = Form(...), content: str = Form(...)):
    msg_id = str(uuid.uuid4())
    ts = int(datetime.datetime.now().timestamp() * 1000)
    chat_db[sender].append({
        "msg_id": msg_id,
        "is_me": True,
        "sender": "Me",
        "content": content,
        "time": format_time(ts),
        "timestamp": ts
    })
    unread_db[sender] = 0
    save_db()
    reply_queue.append({"target": sender, "content": content})
    new_msg_event.set()
    new_msg_event.clear()
    return {"status": "ok"}

@app.get("/messages/{target}")
async def get_messages(target: str, after: int = 0):
    unread_db[target] = 0
    save_db()
    if target not in read_queue:
        read_queue.append(target)
    new_msg_event.set()
    new_msg_event.clear()
    return sorted(chat_db[target], key=lambda x: x['timestamp'])[after:]

@app.get("/subscribe")
async def subscribe():
    try:
        await asyncio.wait_for(new_msg_event.wait(), timeout=20.0)
    except asyncio.TimeoutError:
        pass
    return {"status": "updated"}

@app.get("/get_reply")
async def get_reply():
    response_data = {}
    if reply_queue: 
        response_data["reply"] = reply_queue.popleft()
    if read_queue: 
        response_data["read"] = read_queue.popleft()
    return response_data