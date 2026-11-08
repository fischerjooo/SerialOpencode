import importlib.util
import json
import pathlib
import sys
import types
import unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
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
        self.assertEqual(port.events(),[{"type":"hello","version":3,"transport":"Bluetooth RFCOMM","auth":False}])
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

class RobustnessTests(unittest.TestCase):
    def setUp(self):
        self.port=FakePort(); self.api=FakeAPI()
        self.b=bridge.BluetoothBridge(self.port,self.api,.5)
        self.b.session_id='s1'; self.b.ui_json=True
    def request(self, **fields):
        self.b.command('@OCBT1 '+json.dumps({'requestId':'r1','sessionId':'s1',**fields}))
        return self.port.events()[-1]
    def test_multiline_unicode_slash_prompt_and_duplicate_delivery(self):
        text='/this is prompt text\nこんにちは 👋'
        self.assertTrue(self.request(action='prompt',text=text)['ok'])
        self.assertTrue(self.request(action='prompt',text=text)['ok'])
        self.assertEqual(len(self.api.posts),1)
        self.assertEqual(self.api.posts[0]['parts'][0]['text'],text)
    def test_oversized_frame_tail_never_executes(self):
        self.b.feed(b'x'*(bridge.MAX_INPUT_BYTES+1))
        self.b.feed(b'dangerous prompt\nhello\n')
        self.assertEqual([p['parts'][0]['text'] for p in self.api.posts],['hello'])
    def test_fragmented_utf8_and_multiple_frames(self):
        data=('héllo\nworld\n').encode()
        for byte in data: self.b.feed(bytes([byte]))
        self.assertEqual(len(self.api.posts),2)
        self.assertEqual(self.api.posts[0]['parts'][0]['text'],'héllo')
    def test_invalid_frame_does_not_kill_following_command(self):
        self.b.feed(b'@OCBT1 null\n\xff\nhello\n')
        self.assertEqual(len(self.api.posts),1)
        self.assertEqual(len([e for e in self.port.events() if e['type']=='error']),2)
    def test_invalid_prompt_is_rejected(self):
        self.assertFalse(self.request(action='prompt',text='x'*4001)['ok'])
        self.assertEqual(self.api.posts,[])
    def test_partial_serial_writes(self):
        output=bytearray()
        def write(data): output.extend(data[:3]); return min(3,len(data))
        self.port.write=write
        self.b.send_event('notice',{'text':'こんにちは'})
        self.assertEqual(json.loads(output.decode()[7:])['text'],'こんにちは')
    def test_zero_write_fails(self):
        self.port.write=lambda data: 0
        with self.assertRaises(OSError): self.b.send_event('hello')
    def test_optional_auth(self):
        self.assertIsNone(bridge.OpenCodeAPI(4096).authorization)
        self.assertTrue(bridge.OpenCodeAPI(4096,'secret').authorization.startswith('Basic '))
    def test_append_only_message_sends_delta(self):
        self.b.seen={'m1':'Test'}
        self.b.poll()
        events=self.port.events()
        self.assertEqual(events[0]['type'],'message_delta')
        self.assertEqual(events[0]['text'],' reply')
        self.assertEqual(events[0]['sessionId'],'s1')
    def test_permission_checks_session(self):
        original=self.api.request
        self.api.request=lambda method,path,payload=None: [{'id':'p1','sessionID':'other'}] if path=='/permission' else original(method,path,payload)
        self.assertFalse(self.request(action='permission',id='p1',reply='once')['ok'])
    def test_failure_is_cached_not_reexecuted(self):
        calls=[]
        def fail(*args): calls.append(args); raise bridge.APIError('timeout; outcome unknown')
        self.api.request=fail
        self.assertFalse(self.request(action='prompt',text='hello')['ok'])
        self.assertFalse(self.request(action='prompt',text='hello')['ok'])
        self.assertEqual(len(calls),1)
