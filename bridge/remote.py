"""Version 3 remote commands. Serial ownership stays with the bridge loop."""
from collections import OrderedDict
import json
from urllib.parse import quote


class RemoteCommands:
    def __init__(self, bridge):
        self.bridge = bridge
        self.results = OrderedDict()
        self.pending = None
        self.unsupported = set()

    def handle(self, raw):
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError('Request must be an object')
        rid = request.get('requestId')
        if not isinstance(rid, str) or not 1 <= len(rid) <= 100:
            raise ValueError('requestId is required')
        if rid not in self.results:
            result = {'requestId': rid, 'ok': False}
            try:
                result['data'] = self.dispatch(request)
                result['ok'] = True
            except (RuntimeError, ValueError, TypeError) as exc:
                result['error'] = str(exc)
            self.results[rid] = result
            while len(self.results) > 128:
                self.results.popitem(last=False)
        self.bridge.send_event('result', self.results[rid])

    def dispatch(self, r):
        b = self.bridge
        api = b.api.request
        action = r.get('action')
        if action == 'ping':
            return {'version': 3}
        if action == 'catalog':
            providers = api('GET', '/provider')
            agents = api('GET', '/agent')
            return {
                'models': [{'providerID': p['id'], 'modelID': key, 'name': m.get('name', key)}
                           for p in providers.get('all', []) if p.get('id') in providers.get('connected', [])
                           for key, m in p.get('models', {}).items()][:300],
                'agents': [a['name'] for a in agents if not a.get('hidden') and a.get('mode') != 'subagent'],
            }
        ident = r.get('sessionId')
        if not isinstance(ident, str) or not 1 <= len(ident) <= 200:
            raise ValueError('A session ID is required')
        path = '/session/' + quote(ident, safe='')
        if action == 'select':
            session = api('GET', path)
            if not isinstance(session, dict) or session.get('id') != ident:
                raise ValueError('Unexpected session response')
            b.sessions = [session] + [s for s in b.sessions if s['id'] != ident]
            b.select_session('1')
        elif action == 'prompt':
            text = r.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                raise ValueError('Prompt must contain 1..4000 characters')
            payload = {'parts': [{'type': 'text', 'text': text}]}
            if r.get('agent'):
                if not isinstance(r['agent'], str):
                    raise ValueError('Invalid agent')
                payload['agent'] = r['agent']
            if r.get('model'):
                model = r['model']
                if not isinstance(model, dict) or not all(isinstance(model.get(k), str) for k in ('providerID', 'modelID')):
                    raise ValueError('Invalid model')
                payload['model'] = {k: model[k] for k in ('providerID', 'modelID')}
            api('POST', path + '/prompt_async', payload)
            b.busy = True
            b.last_poll_time = 0
        elif action == 'abort':
            return api('POST', path + '/abort')
        elif action == 'rename':
            title = r.get('title')
            if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
                raise ValueError('Title must contain 1..200 characters')
            api('PATCH', path, {'title': title.strip()})
            b.sessions_command()
        elif action == 'delete':
            api('DELETE', path)
            b.sessions = [s for s in b.sessions if s['id'] != ident]
            if b.session_id == ident:
                b.session_id = None
                b.seen.clear()
                b.send_event('selected', {'id': None, 'title': 'No session selected'})
            b.sessions_command()
        elif action in ('permission', 'question', 'reject_question'):
            kind = 'permission' if action == 'permission' else 'question'
            pending = api('GET', '/' + kind)
            request_id = r.get('id')
            if not any(x.get('id') == request_id and x.get('sessionID') == ident for x in pending):
                raise ValueError('Request is no longer pending in this session')
            endpoint = '/' + kind + '/' + quote(request_id, safe='')
            if action == 'permission':
                if r.get('reply') not in ('once', 'always', 'reject'):
                    raise ValueError('Invalid permission reply')
                api('POST', endpoint + '/reply', {'reply': r['reply']})
            elif action == 'reject_question':
                api('POST', endpoint + '/reject')
            else:
                answers = r.get('answers')
                if not isinstance(answers, list) or not all(isinstance(a, list) and all(isinstance(v, str) for v in a) for a in answers):
                    raise ValueError('Invalid answers')
                api('POST', endpoint + '/reply', {'answers': answers})
            self.pending = None
        else:
            raise ValueError('Unknown action')

    def poll(self):
        b = self.bridge
        b.status_command()
        if not b.ui_json:
            return
        snapshot = {'sessionId': b.session_id}
        for kind in ('permission', 'question'):
            if kind in self.unsupported:
                continue
            try:
                items = b.api.request('GET', '/' + kind)
                if not isinstance(items, list):
                    raise ValueError('Unexpected pending requests response')
                snapshot[kind] = [x for x in items if x.get('sessionID') == b.session_id]
            except RuntimeError as exc:
                if 'HTTP 404' not in str(exc):
                    raise
                self.unsupported.add(kind)
                b.send_event('notice', {'text': kind + ' API unavailable on this OpenCode version'})
        if snapshot != self.pending:
            b.send_event('pending', snapshot)
            self.pending = snapshot
