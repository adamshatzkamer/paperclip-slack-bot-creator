# Paperclip Slack Bot Creator

A local web GUI and bundled Python command-line helper for creating a Slack app
and connecting it to an existing Paperclip agent on your own server.

**Early implementation, not a production-verified release.** Automated tests use
mocks. Live end-to-end deployment and complete visual verification are still pending.
This project is independent of Slack and Paperclip.

## Requirements

- Python 3.9+ on your computer; SSH and Python 3 on the target Linux server.
- An existing trusted SSH connection; strict host-key verification is retained.
- Paperclip accessible at `http://127.0.0.1:3100` on the target server, with API
  access available to the SSH user. Other ports or authenticated API deployments
  need adaptation; this is not a universal installer.
- An installed [Slack Socket plugin](https://github.com/0xCVH/paperclip-plugin-slack-socket)
  supporting `additionalBots`, `continueMentionedThreads`, plugin configuration
  hot reload, encrypted secret references, and the plugin health endpoint.
  This tool was developed against a patched multi-bot installation. Do not assume
  an arbitrary upstream release has these capabilities. It does not install or
  upgrade Paperclip or its plugin.
- Permission to create and install Slack apps in your workspace.

No third-party Python packages or separate Slack CLI installation are needed.
The bundled CLI calls Slack's official HTTPS APIs and uses SSH for deployment.

## Quick start

For a disconnected preview, requiring no credentials or server configuration:

```sh
python3 server.py --demo --open
```

For real setup:

```sh
cp settings.example.json settings.json
# Edit settings.json with your own identifiers and SSH destination.
python3 server.py --open
```

On macOS, after configuring settings, double-click `Launch Bot Builder.command`.
Keep its Terminal window open. Visit `http://127.0.0.1:8765` (or your configured
port). **Do not open public/index.html directly:** it requires the local server.
Use Control-C to stop the server. Starting it does not deploy anything.

`settings.json` is ignored by Git. Populate its SSH destination, company name/ID,
plugin ID, workspace ID, and workspace hostname. The example contains only dummy
values. Never put Slack tokens in either settings file.

## Create and deploy

1. Load agents from your server, then select an agent without an existing bot.
   Enter a bot name and a Slack channel link or ID.
2. Obtain an **App Configuration Access Token** for your workspace from
   [Your Apps](https://api.slack.com/apps), under Your App Configuration Tokens.
   Paste it in the local form, review permissions, and confirm app creation.
3. Follow the new app's installation link and approve in Slack. Copy the **Bot
   User OAuth Token** (`xoxb-`) from OAuth & Permissions. Generate an **App-Level
   Token** (`xapp-`) with `connections:write` under Basic Information.
4. Enter both tokens locally. For a private channel, invite the bot in Slack first.
   Confirm **Submit & deploy bot**. The helper verifies the app and workspace,
   joins the public channel or checks private membership, stores encrypted tokens
   on your server, and appends one mapping to the plugin. Saving activates it.
5. Mention the bot in Slack to check a real reply. The tool sends no test messages.

Slack approval and token generation are manual steps; this is not unattended
one-click provisioning. Model settings are inherited from the selected agent.
The form permits one app creation attempt per server session. Keep the page and
server open during setup; no session state is persisted across restarts.

## Safety and limitations

- Only IPv4 loopback is exposed. Host, Origin, and a per-launch request token are
  checked. Do not expose this service through a tunnel or reverse proxy.
- Credentials are not written to local files or logs by this tool, and are not
  placed in command-line arguments. They travel through HTTPS and SSH stdin.
  Browser password managers, process memory, and OS swap are outside this guarantee.
- Existing bot mappings, company settings, models, agent files, and unrelated
  services are not edited. Agents with existing bot mappings are rejected.
- Saving configuration briefly reconnects **all** Slack connections hosted by
  the shared plugin. The Paperclip server itself is not restarted.
- Avoid editing plugin settings concurrently. A lock serializes this helper;
  a pre-save comparison detects changes, but there is no verified atomic
  compare-and-swap API. Independent writers can still race the save.
- Channel selection is a membership target, not an exclusive channel restriction.
  The bot can answer permitted users in DMs or other channels where it is invited.
  The existing nonempty Slack user allowlist is inherited.
- No automatic deletion or rollback occurs after failure. New apps, channel
  membership, or encrypted secrets may remain. Inspect before retrying; an SSH
  timeout can leave a remote operation still finishing. Leftover secrets block
  retries rather than being overwritten. App creation failures may be ambiguous.
- Plugin health does not prove that a model can reply. A real Slack conversation
  remains the final verification step.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests use example settings and in-memory fixtures. They do not contact Slack,
connect to SSH, or require a live settings file. The `--demo` mode disables all
external operations. Never use real credentials in tests or screenshots.

## Sharing and contributions

This copy contains no private repository history, deployment settings, credential
files, or organization-specific design assets. Keep those exclusions when sharing
changes. Review changes to permissions, deployment, and secret handling carefully.
Do not report vulnerabilities with live tokens or private logs in public issues;
coordinate privately with the repository owner first.

## References and license

- [Slack app manifest creation](https://docs.slack.dev/reference/methods/apps.manifest.create/)
- [Socket Mode setup](https://docs.slack.dev/apis/events-api/using-socket-mode/)
- [Channel joining](https://docs.slack.dev/reference/methods/conversations.join/)

MIT; see [LICENSE](LICENSE). Third-party services and dependencies retain their
own licenses and terms. No upstream plugin source is bundled here.
