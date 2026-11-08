# Serial protocol v3

UTF-8, LF-terminated frames; CRLF accepted. `/ui json` negotiates structured events; hello returns `version: 3`. Text terminal commands remain available (`/sessions`, `/use N`, `/new`, `/history 1..20`, `/status`, `/help`). A v2 bridge can list/history but must be upgraded for new actions.

Both directions use `@OCBT1 ` followed by one JSON object. Newline characters inside JSON strings are escaped. Maximum input frame: 32 KiB; overflow discards through the next LF. Invalid UTF-8 and JSON are errors, never prompt text.

```json
{"requestId":"unique-client-uuid","action":"prompt","sessionId":"ses_123","text":"line one\nline two","agent":"build","model":{"providerID":"anthropic","modelID":"model-id"}}
```

The response is `{"type":"result","requestId":"...","ok":true,"data":null}` or `ok:false,error:"..."`. The bridge caches the last 128 outcomes; clients must use a new ID for each distinct request and must not replay mutations after a timeout. Caching includes failures because HTTP timeouts may occur after acceptance. It is not durable exactly-once execution.

Actions: `ping`, `catalog`, `select`, `prompt`, `abort`, `rename` (`title`), `delete`, `permission` (`id`, `reply`: `once|always|reject`), `question` (`id`, `answers`: string[][]), `reject_question` (`id`). All except ping/catalog require `sessionId`. Permission/question IDs must still be pending in that session.

Events: `hello`, `sessions`, `selected`, `history_begin`, `message`, `message_delta`, `history_end`, `status`, `pending`, `notice`, `error`, `result`. Message/history frames include `sessionId`; ignore frames belonging to a different selected session. `message` replaces text by message ID; `message_delta` appends text. A history refresh resets the baseline. Pending snapshots replace the displayed approvals/questions.

The page waits 20 seconds for acknowledgements, preserves failed prompts, and does not resend automatically. Serial writes are ordered and tied to the connection that queued them. The bridge alone owns its serial port and API state.
