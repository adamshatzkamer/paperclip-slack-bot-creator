import copy
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import botctl
import remote
import io
import runpy
from unittest.mock import Mock

CFG = json.loads((Path(__file__).resolve().parents[1] / 'settings.example.json').read_text())
with patch.object(botctl, 'settings', return_value=CFG):
    import server
AGENT = "11111111-1111-4111-8111-111111111111"
APP = "A1234567890"
FORM = {"name": "Example GM", "agent_id": AGENT, "channel": "C1234567890", "private": False}
TOKENS = {"app_id": APP, "bot_token": "xoxb-fake-for-unit-tests-only",
          "app_token": "xapp-1-A1234567890-fake-for-unit-tests-only", "confirm_deploy": True}


class BuilderTests(unittest.TestCase):
    def setUp(self):
        mock_settings = patch.object(botctl, 'settings', return_value=CFG)
        mock_settings.start()
        self.addCleanup(mock_settings.stop)

    def test_manifest_has_thread_events_and_no_slash_collision(self):
        value = botctl.manifest("Example GM")
        self.assertIn("message.channels", value["settings"]["event_subscriptions"]["bot_events"])
        self.assertNotIn("slash_commands", value["features"])
        self.assertNotIn("admin", " ".join(botctl.SCOPES))

    def test_channel_link(self):
        self.assertEqual(botctl.channel_id("https://your-workspace.slack.com/archives/C1234567890", CFG), FORM["channel"])

    def test_channel_injection_rejected(self):
        for value in ["C123;echo pwned", "https://evil.test/archives/C1234567890", "$(touch /tmp/pwned)", "https://your-workspace.slack.com/archives/C1234567890/p123"]:
            with self.assertRaises(botctl.SafeError):
                botctl.channel_id(value, CFG)

    def test_create_requires_confirmation(self):
        with self.assertRaises(botctl.SafeError):
            botctl.run("create", FORM, slack_call=lambda *_: self.fail("Called Slack"))

    def test_creation_drops_all_credentials(self):
        result = botctl.run("create", dict(FORM, confirm_create=True, config_token="fake-config-token"),
                            slack_call=lambda *_: {"ok": True, "app_id": APP, "credentials": {"client_secret": "DO-NOT-PRINT"}})
        self.assertEqual(result, {"ok": True, "app_id": APP})

    def test_wrong_workspace_stops_before_mutation(self):
        with self.assertRaisesRegex(botctl.SafeError, "different Slack workspace"):
            botctl.run("deploy", dict(FORM, **TOKENS), slack_call=lambda *_: {"ok": True, "team_id": "WRONG"},
                       remote_call=lambda *_: self.fail("Remote called"))

    def test_wrong_app_token_stops_before_network(self):
        with self.assertRaises(botctl.SafeError):
            botctl.run("deploy", dict(FORM, **dict(TOKENS, app_token="xapp-1-A9999999999-fake-token")),
                       slack_call=lambda *_: self.fail("Slack called"))

    def test_deploy_order(self):
        events = []
        def slack(method, _token, _body=None):
            events.append(method)
            return {"ok": True, "team_id": CFG["workspace_id"], "bot_id": "B1234567890",
                    "bot": {"app_id": APP}, "channel": {"id": FORM["channel"], "is_member": True}}
        def vps(action, _data, _cfg):
            events.append(action)
            return {"ok": True, "agents": [{"id": AGENT}], "bots": [], "healthy": True}
        result = botctl.run("deploy", dict(FORM, **TOKENS), slack_call=slack, remote_call=vps)
        self.assertTrue(result["joined"])
        self.assertEqual(events, ["auth.test", "bots.info", "apps.connections.open", "inspect", "conversations.join", "deploy"])

    def test_private_channel_never_joins(self):
        def slack(method, *_):
            self.assertNotEqual(method, "conversations.join")
            return {"ok": True, "team_id": CFG["workspace_id"], "bot_id": "B1234567890", "bot": {"app_id": APP},
                    "channel": {"id": FORM["channel"], "is_member": False}}
        with self.assertRaisesRegex(botctl.SafeError, "Invite"):
            botctl.run("deploy", dict(FORM, **TOKENS, private=True), slack_call=slack,
                       remote_call=lambda *_: {"ok": True, "agents": [{"id": AGENT}], "bots": []})


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.config = {"companyId": CFG["company_id"], "additionalBots": [{"key": "old", "agentId": "old-agent"}],
                       "defaultAgentId": "primary-agent", "continueMentionedThreads": True,
                       "allowedSlackUserIds": ["U1234567890"], "custom_setting": {"preserve": True}}
        self.before = copy.deepcopy(self.config)
        self.writes = []
        self.secret_count = 0

    def api(self, path, body=None):
        if body is not None:
            self.writes.append((path, copy.deepcopy(body)))
            if path.endswith("/secrets"):
                self.secret_count += 1
                return {"id": f"00000000-0000-4000-8000-{self.secret_count:012d}"}
            if path.endswith("/config"):
                self.config = copy.deepcopy(body["configJson"])
                return {"ok": True}
        if "/config?" in path:
            return {"configJson": copy.deepcopy(self.config)}
        if path.endswith("/agents"):
            return [{"id": AGENT, "name": "Example", "status": "idle"}]
        if path.endswith("/secrets"):
            return []
        if path.endswith("/health"):
            return {"healthy": True}
        return {"name": CFG["company_name"]}

    def payload(self, action="deploy"):
        return dict(CFG, action=action, agent_id=AGENT, **TOKENS)

    def test_inspect_has_no_writes(self):
        result = remote.run(self.payload("inspect"), self.api)
        self.assertTrue(result["ok"])
        self.assertEqual(self.writes, [])

    def test_append_preserves_every_existing_setting(self):
        result = remote.run(self.payload(), self.api)
        self.assertTrue(result["ok"])
        added = self.config["additionalBots"].pop()
        self.assertEqual(self.config, self.before)
        self.assertEqual(added["agentId"], AGENT)
        self.assertEqual(added["allowedSlackUserIds"], self.before["allowedSlackUserIds"])
        self.assertEqual([p.rsplit("/", 1)[1] for p, _ in self.writes], ["secrets", "secrets", "config"])

    def test_duplicate_is_read_only(self):
        remote.run(self.payload(), self.api)
        self.writes.clear()
        result = remote.run(self.payload(), self.api)
        self.assertTrue(result["already_configured"])
        self.assertEqual(self.writes, [])

    def test_no_empty_allowlist(self):
        self.config["allowedSlackUserIds"] = []
        with self.assertRaisesRegex(ValueError, "unrestricted"):
            remote.run(self.payload(), self.api)
        self.assertEqual(self.writes, [])

    def test_concurrent_change_not_overwritten(self):
        def changed(path, body=None):
            if "/config?" in path and self.secret_count == 2:
                self.config["changed_by_someone_else"] = True
            return self.api(path, body)
        result = remote.run(self.payload(), changed)
        self.assertFalse(result["ok"])
        self.assertTrue(result["partial"])
        self.assertFalse(any(p.endswith("/config") for p, _ in self.writes))

    def test_partial_secret_failure_no_delete_or_config_write(self):
        def failed(path, body=None):
            if body and path.endswith("/secrets") and self.secret_count == 1:
                raise ValueError("Secret creation failed safely")
            return self.api(path, body)
        result = remote.run(self.payload(), failed)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["secret_ids"]), 1)
        self.assertEqual(self.config, self.before)


class ServerTests(unittest.TestCase):
    def test_preview_starts_without_private_settings_or_network(self):
        fake_server = Mock()
        fake_server.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(sys, 'argv', ['server.py', '--demo']), \
             patch.object(botctl, 'settings', side_effect=AssertionError('No private settings in demo')), \
             patch('http.server.ThreadingHTTPServer', return_value=fake_server) as factory, \
             patch('builtins.print'):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'server.py'), run_name='__main__')
        self.assertEqual(factory.call_args.args[0], ('127.0.0.1', CFG['port']))
        fake_server.server_close.assert_called_once()

    def test_session_exposes_configured_workspace(self):
        h = self.handler(path='/session')
        h.do_GET()
        payload = h.send.call_args.args[1]
        self.assertEqual(payload['workspace_host'], CFG['workspace_host'])
        self.assertEqual(payload['company'], CFG['company_name'])

    def handler(self, headers=None, payload=None, path="/api/inspect"):
        data = json.dumps(payload or {}).encode()
        h = server.Handler.__new__(server.Handler)
        h.headers = {"Host": "127.0.0.1:" + str(server.PORT), "Origin": server.ORIGIN,
                     "X-Builder-Token": server.NONCE, "Content-Type": "application/json", "Content-Length": str(len(data))}
        h.headers.update(headers or {})
        h.path = path
        h.rfile = io.BytesIO(data)
        h.connection = Mock()
        h.send = Mock()
        return h

    def test_csrf_and_rebinding_rejected_without_helper(self):
        for headers in ({"Origin": "https://evil.test"}, {"Host": "evil.test"}, {"X-Builder-Token": "wrong"}):
            h = self.handler(headers)
            with patch.object(server.subprocess, "run", side_effect=AssertionError("No helper allowed")):
                h.do_POST()
            self.assertEqual(h.send.call_args.args[0], 403)

    def test_demo_never_invokes_helper(self):
        for path in ("/api/inspect", "/api/create", "/api/deploy"):
            h = self.handler(path=path)
            with patch.object(server, "DEMO", True), patch.object(server.subprocess, "run", side_effect=AssertionError("No external calls in preview")):
                h.do_POST()
            self.assertEqual(h.send.call_args.args[0], 200)

    def test_existing_app_cannot_be_deployed_by_gui(self):
        h = self.handler(payload=dict(FORM, **TOKENS), path="/api/deploy")
        with patch.object(server, "DEMO", False), patch.object(server.subprocess, "run", side_effect=AssertionError("No helper")):
            h.do_POST()
        self.assertEqual(h.send.call_args.args[0], 409)

    def test_repeated_create_is_blocked(self):
        h = self.handler(payload=dict(FORM, confirm_create=True), path="/api/create")
        with patch.object(server, "DEMO", False), patch.object(server, "CREATE_ATTEMPTED", True), patch.object(server.subprocess, "run", side_effect=AssertionError("No helper")):
            h.do_POST()
        self.assertEqual(h.send.call_args.args[0], 409)


if __name__ == "__main__":
    unittest.main()
