#!/usr/bin/env python3
"""MCP server for the robot (stdio, no dependencies beyond the Python standard library).

Lets any MCP client - Claude Code (works with a Claude subscription), Claude Desktop, Hermes Agent, etc. -
see and act through the robot. It forwards to robotd's HTTP API.

Config (environment):
  ROBOT_URL    default http://pip.local:8080 (home Wi-Fi); from anywhere else use your robot's public address
               (config web.public_url, e.g. a Cloudflare Tunnel: https://robot.example.com)
  ROBOT_TOKEN  the robot's API token; if unset, read from ~/.config/ai-multi-bot/robot_token

Claude Code:  claude mcp add robot -- python3 /path/to/robot_mcp.py
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("ROBOT_URL", "http://pip.local:8080").rstrip("/")
_TOKEN_FILE = Path.home() / ".config/ai-multi-bot/robot_token"
TOKEN = os.environ.get("ROBOT_TOKEN") or (_TOKEN_FILE.read_text().strip() if _TOKEN_FILE.exists() else "")
PROTOCOL = "2025-06-18"


def http(method, path, body=None, raw=False, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method,
                                 headers={"X-Robot-Token": TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = r.read()
    return payload if raw else json.loads(payload or b"null")


def obj(props=None, required=None):
    return {"type": "object", "properties": props or {}, "required": required or []}


TOOLS = {
    "robot_status": ("Robot health: CPU temp, RAM, disk, network, RP2040, AI model in use.", obj(), lambda a: http("GET", "/api/status")),
    "robot_devices": ("Every part of the robot body and whether it is working (ok/warn/missing/planned).", obj(),
                      lambda a: http("GET", "/api/devices")),
    "robot_imu": ("Current orientation (pitch/roll/yaw), acceleration and gyro from the IMU.", obj(), lambda a: http("GET", "/api/imu")),
    "robot_look": ("Take a photo with the robot's camera and return it.", obj({"width": {"type": "integer"}, "height": {"type": "integer"}}),
                   lambda a: ("image", http("GET", f"/api/snapshot.jpg?w={a.get('width', 1280)}&h={a.get('height', 720)}", raw=True))),
    "robot_say": ("Make the robot speak a sentence out loud (and show it on its face).",
                  obj({"text": {"type": "string"}, "mood": {"type": "string"}}, ["text"]), lambda a: http("POST", "/api/say", a)),
    "robot_voice": ("Choose where the robot's voice comes out: output 'pi' (Orange Pi speaker), 'cyd' (screen earpiece) or 'auto' "
                    "(USB/Bluetooth speaker if connected); pi_volume 0..31. With no arguments, just reports the current setting.",
                    obj({"output": {"type": "string", "enum": ["pi", "cyd", "auto"]}, "pi_volume": {"type": "integer"}, "test": {"type": "boolean"}}),
                    lambda a: http("POST", "/api/voice", a) if a else http("GET", "/api/voice")),
    "robot_manual": ("Read the robot's manual (written for AI helpers). Start with page README; it lists the other pages "
                     "(body, software, api, screen, roboos, senses, brain, social, changing_things, motors, troubleshooting, owner).",
                     obj({"page": {"type": "string"}}), lambda a: http("GET", "/api/manual?page=" + a.get("page", "README"))),
    "robot_face": ("Set the robot's facial expression: idle, happy, curious, thinking, warning, sleepy, surprised, sad, love.",
                   obj({"mood": {"type": "string"}, "say": {"type": "string"}}, ["mood"]), lambda a: http("POST", "/api/face", a)),
    "robot_chat": ("Talk to the robot's own on-board AI brain (it has its own personality, memory and tools). Returns its reply.",
                   obj({"text": {"type": "string"}, "speak": {"type": "boolean"}}, ["text"]), lambda a: http("POST", "/api/chat", a)),
    "robot_tests": ("List the robot's self-tests with their last results.", obj(), lambda a: http("GET", "/api/tests")),
    "robot_run_test": ("Run one self-test by id (see robot_tests).", obj({"test_id": {"type": "string"}}, ["test_id"]),
                       lambda a: http("POST", f"/api/test/{a['test_id']}", {})),
    "robot_feed": ("Recent things the robot heard, said and felt (touches, bumps).", obj({"since": {"type": "integer"}}),
                   lambda a: http("GET", f"/api/feed?since={a.get('since', 0)}")),
    "robot_memories": ("The robot's long-term memories.", obj(), lambda a: http("GET", "/api/memories")),
    "robot_remember": ("Store a fact in the robot's long-term memory.", obj({"fact": {"type": "string"}}, ["fact"]),
                       lambda a: http("POST", "/api/memories", a)),
    "robot_ask_owner": ("Show a question with buttons on the robot's touchscreen and wait for the owner's answer. Use before risky actions.",
                        obj({"title": {"type": "string"}, "q": {"type": "string"}, "opts": {"type": "array", "items": {"type": "string"}},
                             "timeout": {"type": "number"}}, ["q"]), lambda a: http("POST", "/api/ask", a, timeout=float(a.get("timeout", 120)) + 10)),
    "robot_brain": ("Which AI models the robot can use, which is active, and per-model statistics.", obj(), lambda a: http("GET", "/api/brain")),
    "robot_select_brain": ("Hot-swap the robot's on-board AI model by key (see robot_brain).", obj({"key": {"type": "string"}}, ["key"]),
                           lambda a: http("POST", "/api/brain/select", a)),
    "robot_dev_send": ("Send a message to RoboDev, the robot's on-board coding agent (it edits the robot's code and firmware, runs "
                       "commands, restarts with auto-rollback). It works in the background in the shared RoboDev conversation (also "
                       "shown on the robot's RoboOS app and the web Dev tab); poll robot_dev_status for progress and replies.",
                       obj({"text": {"type": "string"}}, ["text"]), lambda a: http("POST", "/api/agent/send", {"text": a["text"], "source": "mcp"})),
    "robot_dev_status": ("RoboDev's state (idle/running/waiting for approval), permission mode, model, spend, and the conversation "
                         "items after `since` (use the returned status.seq as the next since).",
                         obj({"since": {"type": "integer"}}), lambda a: http("GET", f"/api/agent?since={int(a.get('since', 0))}")),
    "robot_dev_approve": ("Answer RoboDev's pending Allow/Deny question (status.pending.id). Only do this when the owner told you to.",
                          obj({"id": {"type": "string"}, "allow": {"type": "boolean"}}, ["id", "allow"]),
                          lambda a: http("POST", "/api/agent/approve", a)),
    "robot_dev_stop": ("Stop RoboDev's current job.", obj(), lambda a: http("POST", "/api/agent/stop", {})),
    "robot_log": ("The robot supervisor's recent log lines.", obj(), lambda a: http("GET", "/api/log")),
    "robot_ideas": ("The robot's own suggestions for its next improvements.", obj(), lambda a: http("GET", "/api/ideas")),
}


def handle(msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}},
                "serverInfo": {"name": "robot", "version": "0.1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
        try:
            out = TOOLS[name][2](args)
            if isinstance(out, tuple) and out[0] == "image":
                return {"content": [{"type": "image", "data": base64.b64encode(out[1]).decode(), "mimeType": "image/jpeg"}]}
            return {"content": [{"type": "text", "text": json.dumps(out, indent=1)}]}
        except urllib.error.HTTPError as e:
            return {"content": [{"type": "text", "text": f"robot returned HTTP {e.code}: {e.read()[:300]!r}"}], "isError": True}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"cannot reach robot at {URL}: {e}"}], "isError": True}
    raise KeyError(method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:          # notification (e.g. notifications/initialized)
            continue
        try:
            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except KeyError:
            resp = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"method not found: {msg.get('method')}"}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
