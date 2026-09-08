"""Executed through SSH; requests arrive on stdin, never in shell arguments.

Only the fixed existing Paperclip API is reachable. No service or agent edits.
Errors intentionally omit response bodies, which may contain secret values.
"""
import copy
import json
import re
import sys
import urllib.request


def require(condition, message):
    if not condition:
        raise ValueError(message)


def request(path, body=None):
    payload = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:3100" + path, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as response:
            return json.load(response)
    except Exception:
        raise ValueError("Paperclip request failed; inspect status before retrying. No response body was exposed.") from None


def context(data, api=request):
    for field in ("company_id", "plugin_id"):
        require(bool(re.fullmatch(r"[a-f0-9-]{36}", data[field])), "Invalid target ID.")
    company = data["company_id"]
    path = "/api/plugins/" + data["plugin_id"]
    record = api("/api/companies/" + company)
    require(record.get("name") == data["company_name"], "Company identity changed; deployment stopped.")
    config = api(path + "/config?companyId=" + company)["configJson"]
    require(config.get("companyId") == company, "Plugin belongs to another company.")
    require(isinstance(config.get("additionalBots"), list), "Multi-bot plugin configuration is unavailable.")
    require(config.get("continueMentionedThreads") is True, "Thread continuation is not enabled; existing configuration was not changed.")
    allow = config.get("allowedSlackUserIds")
    require(isinstance(allow, list) and len(allow) > 0, "Existing user allowlist is empty; refusing unrestricted access.")
    agents = api("/api/companies/" + company + "/agents")
    require(isinstance(agents, list), "Unexpected agent list response.")
    return path, config, agents


def run(data, api=request):
    path, config, agents = context(data, api)
    eligible = [a for a in agents if a.get("status") not in ("terminated", "paused", "pending_approval")]
    if data["action"] == "inspect":
        return {"ok": True, "agents": [{"id": a["id"], "name": a["name"]} for a in eligible],
                "allowlist": config["allowedSlackUserIds"],
                "bots": [{"key": "primary", "agentId": config.get("defaultAgentId")}] +
                        [{"key": b["key"], "agentId": b["agentId"]} for b in config["additionalBots"]]}
    require(data["action"] == "deploy", "Unsupported action.")
    app_id = data["app_id"]
    require(bool(re.fullmatch(r"A[A-Z0-9]{8,20}", app_id)), "Invalid app ID.")
    require(any(a["id"] == data["agent_id"] for a in eligible), "Agent is unavailable in the selected company.")
    key = "slack-" + app_id.lower()
    existing = [b for b in config["additionalBots"] if b.get("key") == key]
    if existing:
        require(existing[0].get("agentId") == data["agent_id"], "This app is already mapped to another agent.")
        return {"ok": True, "already_configured": True, "healthy": api(path + "/health").get("healthy") is True}
    # Do not overwrite any prior bot or silently attach another bot to an agent.
    require(data["agent_id"] != config.get("defaultAgentId") and
            not any(b.get("agentId") == data["agent_id"] for b in config["additionalBots"]),
            "This agent already has a Slack bot. Existing routing was preserved.")
    require(bool(re.fullmatch(r"xoxb-[A-Za-z0-9-]{10,500}", data["bot_token"])), "Invalid bot token format.")
    require(bool(re.fullmatch(r"xapp-[A-Za-z0-9-]{10,500}", data["app_token"])), "Invalid app token format.")
    secret_path = "/api/companies/" + data["company_id"] + "/secrets"
    secrets = api(secret_path)
    require(isinstance(secrets, list), "Unexpected secret metadata response.")
    names = [key + "-bot-token", key + "-app-token"]
    require(not any(s.get("key") in names for s in secrets),
            "A previous attempt left credentials for this app. Inspect those secrets before retrying; nothing was overwritten.")
    created = []
    try:
        for suffix, token in (("bot", data["bot_token"]), ("app", data["app_token"])):
            item = api(secret_path, {"name": key + "-" + suffix + "-token",
                                     "key": key + "-" + suffix + "-token",
                                     "provider": "local_encrypted", "value": token,
                                     "description": "Paperclip Bot Builder / " + app_id})
            require(bool(re.fullmatch(r"[a-f0-9-]{36}", item.get("id", ""))), "Secret creation returned an invalid identifier.")
            created.append(item["id"])
        latest = api(path + "/config?companyId=" + data["company_id"])["configJson"]
        require(latest == config, "Plugin settings changed during setup. No configuration was overwritten.")
        updated = copy.deepcopy(config)
        updated["additionalBots"].append({"key": key, "agentId": data["agent_id"],
                                         "slackBotTokenRef": {"type": "secret_ref", "secretId": created[0], "version": "latest"},
                                         "slackAppTokenRef": {"type": "secret_ref", "secretId": created[1], "version": "latest"},
                                         "allowedSlackUserIds": config["allowedSlackUserIds"][:]})
        # Saving binds NEW secret references before config/test can resolve them.
        api(path + "/config", {"companyId": data["company_id"], "configJson": updated})
        saved = api(path + "/config?companyId=" + data["company_id"])["configJson"]
        require(saved == updated, "Saved config changed; inspect before retrying. No rollback was attempted.")
        health = api(path + "/health")
        return {"ok": True, "healthy": health.get("healthy") is True,
                "key": key, "secret_ids": created}
    except Exception as exc:
        # Never auto-delete credentials or overwrite potentially concurrent edits.
        return {"ok": False, "error": str(exc), "secret_ids": created,
                "partial": True, "app_id": app_id}


if __name__ == "__main__":
    try:
        import fcntl
        import os
        # Single provisioning process at a time; external config writers must stay idle.
        lock_path = "/tmp/paperclip-bot-builder-" + str(os.getuid()) + ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output = run(json.load(sys.stdin))
    except Exception as exc:
        output = {"ok": False, "error": str(exc) if isinstance(exc, ValueError) else "Remote operation failed safely; inspect before retrying."}
    print(json.dumps(output))
