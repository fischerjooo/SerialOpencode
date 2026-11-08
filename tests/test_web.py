import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class SiteTests(unittest.TestCase):
    def test_standalone_html(self):
        html=(ROOT/"index.html").read_text()
        self.assertIn('<style id="embedded-styles">',html)
        self.assertIn('<script id="embedded-app">',html)
        self.assertIsNone(re.search(r'<script\\s+[^>]*src\\s*=',html,re.I))
        self.assertIsNone(re.search(r'<link\\s+[^>]*rel=["\\\']stylesheet',html,re.I))
        self.assertIn('navigator.serial.requestPort()',html)
        self.assertIn('autoConnectPreviouslyAllowed()',html)
        self.assertIn("navigator.serviceWorker.register('./sw.js')",html)
        self.assertIn('href="./manifest.webmanifest"',html)
        self.assertIn("worker-src 'self'",html)
        self.assertNotIn('One-time bridge token',html)
    def test_mobile_sidebar_closes_and_new_session_is_handled(self):
        html=(ROOT/"index.html").read_text()
        self.assertIn('id="sidebarScrim"',html)
        self.assertIn('id="closeSidebarBtn"',html)
        self.assertIn('aria-controls="sidebar"',html)
        self.assertIn("function setSidebarOpen(open)",html)
        self.assertIn("$('closeSidebarBtn').onclick=closeSidebar",html)
        self.assertIn("$('sidebarScrim').onclick=closeSidebar",html)
        self.assertIn("if(e.key==='Escape')closeSidebar()",html)
        self.assertIn("await send('/new');closeSidebar()",html)
        self.assertIn("renderSessions();closeSidebar();showEmpty",html)
        self.assertIn(".sidebar-scrim.open{display:block}",html)
        self.assertIn("serialopencode-pwa-v2",(ROOT/"sw.js").read_text())

    def test_manifest_and_assets(self):
        m=json.loads((ROOT/"manifest.webmanifest").read_text())
        self.assertEqual(m["start_url"],"./index.html")
        self.assertEqual(m["scope"],"./")
        self.assertEqual(m["display"],"fullscreen")
        sizes={i["sizes"] for i in m["icons"]}
        self.assertTrue({"192x192","any"} <= sizes)
        for icon in m["icons"]:
            path=ROOT/icon["src"]
            self.assertTrue(path.exists(),str(path))
            self.assertGreater(path.stat().st_size,100)
        sw=(ROOT/"sw.js").read_text()
        self.assertIn("'./index.html'",sw)
        self.assertNotIn("'./app.js'",sw)
    def test_embedded_javascript_syntax(self):
        node=shutil.which("node")
        if not node: self.skipTest("Node not installed")
        html=(ROOT/"index.html").read_text()
        scripts=re.findall(r'<script[^>]*>(.*?)</script>',html,flags=re.DOTALL|re.IGNORECASE)
        self.assertGreaterEqual(len(scripts),2)
        with tempfile.TemporaryDirectory() as d:
            for i,script in enumerate(scripts):
                f=pathlib.Path(d)/f"app_{i}.js"
                f.write_text(script)
                r=subprocess.run([node,"--check",str(f)],capture_output=True,text=True)
                self.assertEqual(r.returncode,0,r.stderr)
