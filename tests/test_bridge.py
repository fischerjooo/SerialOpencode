import importlib.util
import json
import pathlib
import sys
import types
import unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
stub = types.ModuleType("serial")
stub.SerialException = RuntimeError
sys.modules["serial"] = stub
spec = importlib.util.spec_from_file_location("opencode_bt_bridge", ROOT / "bridge" / "opencode_bt_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

class FakePort:
    def __init__(self): self.writes = []
    def write(self, data): self.writes.append(data)
    def events(self):
        lines=b"".join(self.writes).decode("utf-8").splitlines()
        self.writes.clear()
        return [json.loads(line[7:]) for line in lines if line.startswith("@OCBT1 ")]

class FakeAPI:
    def __init__(self): self.posts=[]
    def request(self, method, path, payload=None):
        if method=="POST" and path=="/session": return {"id":"s2","title":"New session","time":{"created":2}}
        if method=="GET" and path=="/session": return [{"id":"s1","title":"Crypto test","time":{"updated":1}}]
        if method=="GET" and path.endswith("/message?limit=50"):
            if "/s2/" in path: return []
            return [{"info":{"id":"m1","role":"assistant"},"parts":[{"type":"text","text":"Test reply"}]}]
        if method=="GET" and path=="/session/status": return {"s1":{"type":"idle"}}
        if method=="POST" and path.endswith("/prompt_async"):
            self.posts.append(payload)
            return None
        raise AssertionError((method,path,payload))

class BridgeProtocolTests(unittest.TestCase):
    def test_chat_protocol_without_bridge_token(self):
        port=FakePort()
        api=FakeAPI()
        b=bridge.BluetoothBridge(port,api,2)
        self.assertFalse(hasattr(b,"authenticated"))
        b.command("/ui json")
        self.assertEqual(port.events(),[{"type":"hello","version":2,"transport":"Bluetooth RFCOMM","auth":False}])
        b.command("/sessions")
        self.assertEqual(port.events()[0]["items"][0]["title"],"Crypto test")
        b.command("/use 1")
        self.assertEqual([e["type"] for e in port.events()],["selected","history_begin","message","history_end"])
        b.command("/status")
        self.assertEqual(port.events()[0]["status"],"idle")
        b.command("Hello world")
        self.assertEqual(port.events()[0]["type"],"prompt_accepted")
        self.assertEqual(api.posts,[{"parts":[{"type":"text","text":"Hello world"}]}])
        b.poll()
        self.assertEqual(port.events(),[])

    def test_new_session_visible_and_selected_while_api_list_is_stale(self):
        port=FakePort()
        api=FakeAPI()
        b=bridge.BluetoothBridge(port,api,2)
        b.command("/ui json")
        port.events()
        b.command("/sessions")
        port.events()
        b.command("/new")
        events=port.events()
        self.assertEqual([e["type"] for e in events],["selected","sessions","history_begin","history_end"])
        self.assertEqual(events[0]["id"],"s2")
        self.assertEqual(events[1]["items"][0],{"index":1,"title":"New session","id":"s2","active":True})
        self.assertEqual(events[1]["items"][1]["id"],"s1")
        self.assertEqual(b.session_id,"s2")
        # Server still returns only the old session. Refresh must retain the new one.
        b.command("/sessions")
        items=port.events()[0]["items"]
        self.assertEqual([i["id"] for i in items],["s2","s1"])
        b.command("/use 1")
        self.assertEqual(port.events()[0]["id"],"s2")
        b.command("hello new session")
        self.assertEqual(port.events()[0]["type"],"prompt_accepted")
        self.assertEqual(api.posts[-1]["parts"][0]["text"],"hello new session")

    def test_plain_mode_new_session_is_selected_and_listed(self):
        port=FakePort()
        api=FakeAPI()
        b=bridge.BluetoothBridge(port,api,2)
        b.command("/new")
        output=b"".join(port.writes).decode("utf-8")
        self.assertIn("Selected: New session",output)
        self.assertIn("1. New session *",output)
