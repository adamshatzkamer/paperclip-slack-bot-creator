'use strict';
const $ = (id) => document.getElementById(id);
let session, formData, appId, busy = false;
function status(text, error = false) { $('status').textContent = text; $('status').classList.toggle('error', error); }
function step(n) { document.querySelectorAll('.steps li').forEach((li, i) => { if (i === n - 1) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current'); }); }
async function api(action, data) {
  if (busy) throw new Error('An operation is already running.');
  if (!session) throw new Error('Reload the local tool first.');
  busy = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    const response = await fetch('/api/' + action, {method:'POST', headers:{'Content-Type':'application/json','X-Builder-Token':session.nonce},body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok || !result.ok) {
      let detail = result.error || 'The operation failed. Check status before retrying.';
      if (result.joined) detail += '\nThe bot joined the selected channel, but VPS deployment needs attention.';
      if (result.secret_ids?.length) detail += '\nNew secret IDs for recovery: ' + result.secret_ids.join(', ');
      throw new Error(detail);
    }
    return result;
  } finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); }
}
fetch('/session').then(r => r.json()).then(s => { session=s; $('preview').hidden=!s.demo; $('target').textContent=s.target; $('company').textContent=s.company; }).catch(() => status('Could not start a local session. Reload this page.', true));
$('connect').addEventListener('click', async () => {
  status('Reading available agents from the VPS. No settings will change.');
  try {
    const result = await api('inspect', {});
    $('agent').replaceChildren(new Option('Choose an agent', ''));
    const mapped = new Set(result.bots.map(b => b.agentId));
    for (const agent of result.agents) {
      const option = new Option(agent.name + (mapped.has(agent.id) ? ' — already has a bot' : ''), agent.id);
      option.disabled = mapped.has(agent.id); $('agent').add(option);
    }
    status('Agents loaded. The existing allowlist has ' + result.allowlist.length + ' permitted Slack users.');
  } catch (e) { status(e.message, true); }
});
$('details').addEventListener('submit', async (event) => {
  event.preventDefault();
  formData={name:$('name').value.trim(),agent_id:$('agent').value,channel:$('channel').value.trim(),private:$('private').checked};
  status('Creating one Slack app. Keep this window open; do not submit again.');
  try {
    const result=await api('create', {...formData, config_token:$('config').value, confirm_create:$('create-confirm').checked});
    appId=result.app_id; $('app-id').textContent=appId;
    $('install').href='https://api.slack.com/apps/'+appId+'/install-on-team';
    $('general').href='https://api.slack.com/apps/'+appId+'/general';
    $('selection').textContent=formData.name+' · '+$('agent').selectedOptions[0].textContent+' · '+formData.channel;
    $('details').hidden=true; $('authorization').hidden=false; $('private-help').hidden=!formData.private;
    step(2); status('App created. Complete Slack authorization, then submit to deploy.');
  } catch(e) { status(e.message,true); }
  finally { $('config').value=''; }
});
$('authorization').addEventListener('submit', async event => {
  event.preventDefault(); status('Checking credentials and channel membership, then activating the VPS connection. This can take a few minutes.'); step(3);
  try {
    const result=await api('deploy',{...formData,app_id:appId,bot_token:$('bot-token').value,app_token:$('app-token').value,confirm_deploy:$('deploy-confirm').checked});
    $('authorization').hidden=true; $('result').hidden=false;
    $('result-title').textContent=result.healthy?'Bot connected':'Deployment saved — check connection';
    $('result-text').textContent=result.healthy?'The bot is in the selected channel and Paperclip reports a healthy plugin.':'The configuration was saved and the bot joined the channel, but a healthy connection was not confirmed. Check the Paperclip plugin before testing.';
    $('channel-link').href='https://'+session.workspace_host+'/archives/'+result.channel;
    status('');
  } catch(e) { status(e.message,true); }
  finally { $('bot-token').value=''; $('app-token').value=''; $('deploy-confirm').checked=false; }
});
