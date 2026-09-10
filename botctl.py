#!/usr/bin/env python3
"""Bundled CLI: JSON on stdin, sanitized JSON on stdout; no credentials on disk."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
SCOPES = ["app_mentions:read", "chat:write", "channels:history", "channels:read",
          "channels:join", "groups:history", "groups:read", "im:history", "im:read",
          "im:write", "reactions:read", "users:read"]


class SafeError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise SafeError("Unexpected redirect refused; credentials were not forwarded.")


def need(value, message):
    if not value:
        raise SafeError(message)


def settings():
    cfg = json.loads((ROOT / "settings.json").read_text())
    need(bool(re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9.-]+", cfg["ssh_target"])), "Invalid SSH destination in settings.json.")
    return cfg


def channel_id(value, cfg):
    value = str(value).strip()
    if value.startswith("https://"):
        url = urllib.parse.urlparse(value)
        need(url.hostname == cfg["workspace_host"] and url.port in (None, 443), "Use a channel link.")
        match = re.fullmatch(r"/archives/([CG][A-Z0-9]{8,20})/?", url.path)
        need(match, "Paste a channel link, not a message link.")
        value = match.group(1)
    need(bool(re.fullmatch(r"[CG][A-Z0-9]{8,20}", value)), "Enter a valid Slack channel ID or channel link.")
    return value


def details(data, cfg):
    name = str(data.get("name", "")).strip()
    need(1 <= len(name) <= 80 and all(ord(c) >= 32 for c in name), "Enter a bot name of 1–80 characters.")
    agent = str(data.get("agent_id", ""))
    need(bool(re.fullmatch(r"[a-f0-9-]{36}", agent)), "Choose a Paperclip agent.")
    return {"name": name, "agent_id": agent, "channel": channel_id(data.get("channel", ""), cfg),
            "private": data.get("private") is True}


def manifest(name):
    # No slash command: a shared /paperclip command would collide with existing apps.
    return {"display_information": {"name": name},
            "features": {"bot_user": {"display_name": name, "always_online": False},
                         "app_home": {"home_tab_enabled": False, "messages_tab_enabled": True,
                                      "messages_tab_read_only_enabled": False}},
            "oauth_config": {"scopes": {"bot": SCOPES}},
            "settings": {"socket_mode_enabled": True,
                         "interactivity": {"is_enabled": True},
                         "event_subscriptions": {"bot_events": ["app_mention", "message.channels",
                                                                  "message.groups", "message.im", "reaction_added"]}}}


def slack(method, token, body=None):
    url = "https://slack.com/api/" + method
    headers = {"Authorization": "Bearer " + token}
    if method == "bots.info":
        # Use the documented GET form so Slack receives the bot lookup argument.
        url += "?" + urllib.parse.urlencode(body or {})
        req = urllib.request.Request(url, headers=headers, method="GET")
    else:
        headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=json.dumps(body or {}).encode(),
                                     headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            result = json.load(response)
    except Exception:
        raise SafeError("Slack request outcome is uncertain. Check Your Apps before retrying app creation; do not create a duplicate.") from None
    if not result.get("ok"):
        known = {"invalid_auth", "token_revoked", "missing_scope", "not_authed", "invalid_manifest",
                 "channel_not_found", "is_archived", "restricted_action", "not_allowed_token_type",
                 "ratelimited", "not_in_channel", "no_permission", "access_denied"}
        code = result.get("error")
        raise SafeError("Slack rejected the request: " + (code if code in known else "request_failed") +
                        ". Check permissions and existing app state before retrying.")
    return result


def remote(action, data, cfg):
    payload = {k: cfg[k] for k in ("company_id", "company_name", "plugin_id")}
    payload.update(data, action=action)
    # The only shell-interpreted text is our static, shipped source. User data goes on stdin.
    command = "python3 -c " + shlex.quote((ROOT / "remote.py").read_text())
    try:
        proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                               "-o", "ConnectTimeout=10", cfg["ssh_target"], command],
                              input=json.dumps(payload), capture_output=True, text=True, timeout=170)
        need(proc.returncode == 0, "SSH failed. Check your existing VPS login; host-key checks were not bypassed.")
        output = json.loads(proc.stdout)
        return output
    except subprocess.TimeoutExpired:
        raise SafeError("VPS operation timed out. Its outcome is uncertain; inspect before retrying.") from None
    except (OSError, json.JSONDecodeError):
        raise SafeError("Could not read a safe response from the VPS. Inspect status before retrying.") from None


def run(action, data, cfg=None, slack_call=slack, remote_call=remote):
    cfg = cfg or settings()
    if action == "inspect":
        return remote_call("inspect", {}, cfg)
    form = details(data, cfg)
    if action == "create":
        need(data.get("confirm_create") is True, "Confirm Slack app creation first.")
        token = str(data.get("config_token", "")).strip()
        need(10 <= len(token) <= 1000 and not any(c.isspace() for c in token), "Enter an app configuration access token.")
        result = slack_call("apps.manifest.create", token,
                            {"manifest": json.dumps(manifest(form["name"])), "team_id": cfg["workspace_id"]})
        app_id = result.get("app_id", "")
        need(bool(re.fullmatch(r"A[A-Z0-9]{8,20}", app_id)), "App may have been created, but no valid app ID returned. Check Your Apps.")
        # Deliberately discard signing/client secrets and OAuth URLs from this response.
        return {"ok": True, "app_id": app_id}
    need(action == "deploy", "Unknown action.")
    need(data.get("confirm_deploy") is True, "Confirm VPS deployment and brief Slack reconnection first.")
    app_id = str(data.get("app_id", "")).strip()
    need(bool(re.fullmatch(r"A[A-Z0-9]{8,20}", app_id)), "Enter the Slack app ID.")
    bot_token = str(data.get("bot_token", "")).strip()
    app_token = str(data.get("app_token", "")).strip()
    need(bool(re.fullmatch(r"xoxb-[A-Za-z0-9-]{10,500}", bot_token)), "Bot token must start with xoxb-.")
    need(bool(re.fullmatch(r"xapp-[A-Za-z0-9-]{10,500}", app_token)), "App token must start with xapp-.")
    need(app_id in app_token.split("-"), "The app-level token does not match this Slack app ID.")
    auth = slack_call("auth.test", bot_token)
    need(auth.get("team_id") == cfg["workspace_id"], "Bot token belongs to a different Slack workspace.")
    bot_id = auth.get("bot_id")
    need(isinstance(bot_id, str) and bool(re.fullmatch(r"B[A-Z0-9]+", bot_id)),
         "Slack did not return a bot ID for this token. Copy the Bot User OAuth Token from OAuth & Permissions and retry.")
    bot = slack_call("bots.info", bot_token, {"bot": bot_id}).get("bot")
    actual_app_id = bot.get("app_id") if isinstance(bot, dict) else None
    need(isinstance(actual_app_id, str) and bool(re.fullmatch(r"A[A-Z0-9]{8,20}", actual_app_id)),
         "Slack did not return an app ID for this bot, so its app could not be verified. Retry with the Bot User OAuth Token from this app's OAuth & Permissions page.")
    need(actual_app_id == app_id,
         f"Bot token belongs to Slack app {actual_app_id}, but this setup expects {app_id}. Copy the Bot User OAuth Token from the expected app's OAuth & Permissions page.")
    slack_call("apps.connections.open", app_token)  # Validate connections:write; never emit the socket URL.
    snapshot = remote_call("inspect", {}, cfg)
    need(snapshot.get("ok"), snapshot.get("error", "VPS inspection failed."))
    need(any(a["id"] == form["agent_id"] for a in snapshot["agents"]), "The chosen agent is unavailable.")
    for mapped in snapshot.get("bots", []):
        need(mapped["agentId"] != form["agent_id"] or mapped["key"] == "slack-" + app_id.lower(),
             "This agent already has another Slack bot; nothing was changed.")
    if form["private"]:
        channel = slack_call("conversations.info", bot_token, {"channel": form["channel"]}).get("channel", {})
        need(channel.get("is_member") is True, "Invite the new bot to the private channel in Slack, then retry.")
    else:
        channel = slack_call("conversations.join", bot_token, {"channel": form["channel"]}).get("channel", {})
        need(channel.get("is_member") is True, "Slack did not confirm channel membership.")
    need(channel.get("id") == form["channel"] and not channel.get("is_archived"), "Channel is unavailable.")
    result = remote_call("deploy", {"app_id": app_id, "agent_id": form["agent_id"],
                                    "bot_token": bot_token, "app_token": app_token}, cfg)
    result.update(app_id=app_id, channel=form["channel"], joined=True)
    return result


if __name__ == "__main__":
    try:
        result = run(sys.argv[1], json.load(sys.stdin))
    except SafeError as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception:
        result = {"ok": False, "error": "Operation failed. No credential details were printed; inspect status before retrying."}
    print(json.dumps(result))
