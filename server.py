# this is just the beggining! 
# by Eng. Aymen Al-Tinah
"""
FastAPI Web Server — serves the frontend and manages WebSocket chat sessions.
config.json  → MCP server configurations ONLY (like Claude Desktop)
settings.json → Provider API keys, models, internal state
"""

import json
import os
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Cookie, Response, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from providers import PROVIDER_REGISTRY, create_provider
from mcp_client import MCPClient
import database

load_dotenv()

# ─── Config Persistence ────────────────────────────────────────────────────────
# config.json  → MCP server configurations ONLY (like Claude Desktop)
# settings.json → Provider API keys, models, selected provider

CONFIG_PATH = Path(__file__).parent / "config.json"
SETTINGS_PATH = Path(__file__).parent / "settings.json"

DEFAULT_MCP_CONFIG = {
    "mcpServers": {}
}

DEFAULT_SETTINGS = {
    "selected_provider": "anthropic",
    "providers": {
        pid: {
            "api_key": os.getenv(info["env_key"], ""),
            "selected_model": info["default_model"],
        }
        for pid, info in PROVIDER_REGISTRY.items()
    },
    "mcp_server_path": "",
}


def get_mcp_config(user_id: int) -> dict:
    """Load MCP server config from DB for a user."""
    config = json.loads(json.dumps(DEFAULT_MCP_CONFIG))
    saved = database.get_user_config(user_id)
    if saved and "mcpServers" in saved:
        config["mcpServers"] = saved["mcpServers"]
    return config


def save_mcp_config(user_id: int, config: dict):
    """Save MCP server config to DB for a user."""
    database.save_user_config(user_id, config)


def get_settings(user_id: int) -> dict:
    """Load provider settings from DB for a user."""
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    saved = database.get_user_settings(user_id)
    if saved:
        settings["selected_provider"] = saved.get("selected_provider", settings["selected_provider"])
        settings["mcp_server_path"] = saved.get("mcp_server_path", settings["mcp_server_path"])
        for pid in PROVIDER_REGISTRY:
            if pid in saved.get("providers", {}):
                saved_p = saved["providers"][pid]
                if saved_p.get("api_key"):
                    settings["providers"][pid]["api_key"] = saved_p["api_key"]
                if saved_p.get("selected_model"):
                    settings["providers"][pid]["selected_model"] = saved_p["selected_model"]
    return settings


def save_settings(user_id: int, settings: dict):
    """Save provider settings to DB for a user."""
    database.save_user_settings(user_id, settings)


def get_full_state(user_id: int) -> dict:
    """Load both config + settings merged for the frontend."""
    mcp_config = get_mcp_config(user_id)
    settings = get_settings(user_id)
    return {**settings, **mcp_config}


def get_current_user(session_token: str | None = Cookie(default=None)):
    """Dependency to get the current user from the session cookie."""
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = database.get_user_from_session(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return user


# ─── App Setup ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield

app = FastAPI(title="MCP Client", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── REST Endpoints ─────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ─── Auth Endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/auth/signup")
async def signup(data: dict):
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return JSONResponse({"status": "error", "message": "Username and password required."}, status_code=400)
    
    if database.create_user(username, password):
        user = database.authenticate_user(username, password)
        token = database.create_session(user["id"])
        resp = JSONResponse({"status": "success", "username": username})
        resp.set_cookie("session_token", token, httponly=True, max_age=30*24*60*60)
        return resp
    else:
        return JSONResponse({"status": "error", "message": "Username already exists."}, status_code=400)


@app.post("/api/auth/login")
async def login(data: dict):
    username = data.get("username", "").strip()
    password = data.get("password", "")
    user = database.authenticate_user(username, password)
    if user:
        token = database.create_session(user["id"])
        resp = JSONResponse({"status": "success", "username": user["username"]})
        resp.set_cookie("session_token", token, httponly=True, max_age=30*24*60*60)
        return resp
    return JSONResponse({"status": "error", "message": "Invalid username or password."}, status_code=401)


@app.post("/api/auth/logout")
async def logout(session_token: str | None = Cookie(default=None)):
    if session_token:
        database.destroy_session(session_token)
    resp = JSONResponse({"status": "success"})
    resp.delete_cookie("session_token")
    return resp


@app.get("/api/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    return JSONResponse({"username": user["username"]})


@app.get("/api/providers")
async def get_providers():
    """Return provider registry with available models."""
    return JSONResponse({
        pid: {
            "name": info["name"],
            "models": info["models"],
            "default_model": info["default_model"],
            "icon": info["icon"],
        }
        for pid, info in PROVIDER_REGISTRY.items()
    })


@app.get("/api/config")
async def get_config(user: dict = Depends(get_current_user)):
    """Return full state (settings + MCP config) for the frontend for the logged in user."""
    state = get_full_state(user["id"])
    # Mask API keys for frontend display
    masked = json.loads(json.dumps(state))
    for pid in masked.get("providers", {}):
        key = masked["providers"][pid].get("api_key", "")
        if key and len(key) > 8:
            masked["providers"][pid]["api_key_masked"] = key[:4] + "\u2022" * (len(key) - 8) + key[-4:]
            masked["providers"][pid]["has_key"] = True
        elif key:
            masked["providers"][pid]["api_key_masked"] = "\u2022" * len(key)
            masked["providers"][pid]["has_key"] = True
        else:
            masked["providers"][pid]["api_key_masked"] = ""
            masked["providers"][pid]["has_key"] = False
        masked["providers"][pid]["api_key"] = key
    return JSONResponse(masked)


@app.post("/api/config")
async def save_config_endpoint(data: dict, user: dict = Depends(get_current_user)):
    """Save settings (provider keys, models, selected provider) to DB for the user."""
    settings = get_settings(user["id"])
    if "selected_provider" in data:
        settings["selected_provider"] = data["selected_provider"]
    if "mcp_server_path" in data:
        settings["mcp_server_path"] = data["mcp_server_path"]
    if "providers" in data:
        for pid, pdata in data["providers"].items():
            if pid not in settings["providers"]:
                settings["providers"][pid] = {}
            if "api_key" in pdata and pdata["api_key"]:
                settings["providers"][pid]["api_key"] = pdata["api_key"]
            if "selected_model" in pdata:
                settings["providers"][pid]["selected_model"] = pdata["selected_model"]
    save_settings(user["id"], settings)
    return JSONResponse({"status": "saved"})


@app.get("/api/config/raw")
async def get_raw_config(user: dict = Depends(get_current_user)):
    """Return the raw config.json (MCP servers only) for the editor from DB."""
    mcp_config = get_mcp_config(user["id"])
    return JSONResponse({
        "content": json.dumps(mcp_config, indent=2),
        "path": "database.sqlite->user_config",
    })


@app.put("/api/config/raw")
async def save_raw_config(data: dict, user: dict = Depends(get_current_user)):
    """Save raw config.json (MCP servers only) to DB from the editor."""
    raw_content = data.get("content", "")
    try:
        parsed = json.loads(raw_content)
        # Ensure it only contains mcpServers
        clean = {"mcpServers": parsed.get("mcpServers", {})}
        save_mcp_config(user["id"], clean)
        return JSONResponse({"status": "saved"})
    except json.JSONDecodeError as e:
        return JSONResponse({"status": "error", "message": f"Invalid JSON: {str(e)}"}, status_code=400)


# ─── WebSocket Chat ─────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    session_token = websocket.cookies.get("session_token")
    if not session_token:
        await websocket.close(code=1008)
        return
    user = database.get_user_from_session(session_token)
    if not user:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    client = MCPClient()
    conversation_history: list[dict] = []

    async def send_event(event: dict):
        try:
            await websocket.send_json(event)
        except Exception:
            pass

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            # ── Connect to MCP Server ──
            if msg_type == "connect":
                server_path = data.get("server_path", "").strip()
                if not server_path:
                    await send_event({"type": "error", "message": "Server path is required."})
                    continue

                try:
                    # Disconnect existing if any
                    if client.is_connected:
                        await client.disconnect()
                        client = MCPClient()

                    await send_event({"type": "status", "message": f"Connecting to {server_path}..."})
                    tools = await client.connect_to_server(server_path)

                    # Save the server path to user settings
                    settings = get_settings(user["id"])
                    settings["mcp_server_path"] = server_path
                    save_settings(user["id"], settings)

                    await send_event({
                        "type": "connected",
                        "tools": tools,
                        "server_path": server_path,
                    })
                except Exception as e:
                    await send_event({"type": "error", "message": f"Failed to connect: {str(e)}"})

            # ── Connect via Config (command + args) ──
            elif msg_type == "connect_config":
                server_name = data.get("name", "")
                command = data.get("command", "").strip()
                args = data.get("args", [])
                env = data.get("env", None)

                if not command:
                    await send_event({"type": "error", "message": "Server command is required."})
                    continue

                try:
                    if client.is_connected:
                        await client.disconnect()
                        client = MCPClient()

                    await send_event({"type": "status", "message": f"Connecting to {server_name}..."})
                    tools = await client.connect_with_command(command, args, env)

                    await send_event({
                        "type": "connected",
                        "tools": tools,
                        "server_path": server_name,
                    })
                except Exception as e:
                    await send_event({"type": "error", "message": f"Failed to connect to {server_name}: {str(e)}"})

            # ── Disconnect ──
            elif msg_type == "disconnect":
                try:
                    await client.disconnect()
                    client = MCPClient()
                    conversation_history.clear()
                    await send_event({"type": "disconnected"})
                except Exception as e:
                    await send_event({"type": "error", "message": f"Disconnect error: {str(e)}"})

            # ── Set Provider ──
            elif msg_type == "set_provider":
                provider_name = data.get("provider", "")
                model = data.get("model", "")
                api_key = data.get("api_key", "")

                if not api_key:
                    await send_event({"type": "error", "message": f"API key required for {provider_name}."})
                    continue

                try:
                    provider = create_provider(provider_name, api_key)
                    client.set_provider(provider, model)
                    await send_event({
                        "type": "provider_set",
                        "provider": provider_name,
                        "model": model,
                    })
                except Exception as e:
                    await send_event({"type": "error", "message": f"Provider error: {str(e)}"})

            # ── Chat Message ──
            elif msg_type == "chat":
                message = data.get("message", "").strip()
                if not message:
                    continue

                if not client.provider:
                    await send_event({"type": "error", "message": "Please select a provider and enter an API key first."})
                    continue

                if not client.is_connected:
                    await send_event({"type": "error", "message": "Please connect to an MCP server first."})
                    continue

                try:
                    async for event in client.process_query(message, conversation_history):
                        if event["type"] == "response":
                            conversation_history = event.get("messages", conversation_history)
                        await send_event(event)
                except Exception as e:
                    await send_event({"type": "error", "message": f"Error processing query: {str(e)}"})

            # ── Clear History ──
            elif msg_type == "clear":
                conversation_history.clear()
                await send_event({"type": "cleared"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await send_event({"type": "error", "message": f"WebSocket error: {str(e)}"})
        except Exception:
            pass
    finally:
        await client.cleanup()


# ─── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("\n  >> MCP Client starting at http://localhost:4321\n")
    uvicorn.run(app, host="0.0.0.0", port=4321, log_level="info")

# just the end of the file ^_^

# by Eng. Aymen Al-Tinah