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
        if method=="GET" and path=="/session": return [{"id":"s1","title":"Crypto test","time":{"updated":1}}]
        if method=="GET" and path.endswith("/message?limit=50"): return [{"info":{"id":"m1","role":"assistant"},"parts":[{"type":"text","text":"Test reply"}]}]
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
