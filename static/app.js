const $ = (selector) => document.querySelector(selector);
let snapshot = null, filter = 'open', search = '', request = { payload: '', key: '' };
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || (Array.isArray(body.detail) ? body.detail.map(e => e.msg).join('; ') : 'The request failed.'));
  return body;
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function notify(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(notify.timer); notify.timer = setTimeout(() => { $('#toast').hidden = true; }, 4500); }
function showError(message) { $('#error').textContent = message; $('#error').hidden = !message; }
function empty(container, title, description) {
  const node = element('div', 'empty'); node.append(element('span', 'empty-symbol', '✳'), element('h3', '', title), element('p', '', description)); container.append(node);
}
function render() {
  if (!snapshot) return;
  for (const key of ['open', 'critical', 'resolved', 'overdue']) $(`#metric-${key}`).textContent = String(snapshot.metrics[key]).padStart(2, '0');
  $('#queue-count').textContent = snapshot.metrics.open;
  $('#ticket-count').textContent = snapshot.tickets.length;
  $('#health-label').textContent = snapshot.fault === 'none' ? '● Systems operational' : '● Priority routing degraded';
  $('#health-label').classList.toggle('degraded', snapshot.fault !== 'none');
  const tickets = snapshot.tickets.filter(t => (filter === 'all' || t.status === filter) && `${t.subject} ${t.requester} ${t.email}`.toLowerCase().includes(search.toLowerCase()));
  const list = $('#tickets'); list.replaceChildren();
  if (!tickets.length) empty(list, search ? 'No matching conversations.' : filter === 'resolved' ? 'Your first resolution is on its way.' : 'A quiet queue. A good place to start.', search ? 'Try another name or subject.' : 'Create a ticket and give the conversation somewhere to begin.');
  for (const ticket of tickets) {
    const article = element('article', 'ticket-row');
    const avatar = element('span', 'ticket-avatar', ticket.requester.split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase());
    const body = element('div', 'ticket-body');
    const top = element('div', 'ticket-top'); top.append(element('span', 'ticket-id', `T-${ticket.id.slice(0, 5).toUpperCase()}`), element('span', `priority ${ticket.priority}`, ticket.priority), element('time', '', new Date(ticket.createdAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })));
    const detail = element('details', 'ticket-content'); const summary = element('summary', '', ticket.subject); detail.append(summary, element('p', 'ticket-description', ticket.description));
    const meta = element('div', 'ticket-meta'); meta.append(element('span', '', ticket.requester), element('span', '', ticket.email), element('span', '', ticket.status === 'resolved' ? 'Resolved' : `Target: ${new Date(ticket.dueAt).toLocaleString()}`));
    body.append(top, detail, meta); article.append(avatar, body);
    if (ticket.status === 'open') {
      const button = element('button', 'resolve-button', '✓ Resolve'); button.addEventListener('click', async () => { button.disabled = true; try { await api(`/api/tickets/${ticket.id}/resolve`, { method: 'POST', body: '{}' }); await refresh(); notify('Conversation resolved. One less thing on their mind.'); } catch (error) { showError(error.message); button.disabled = false; } }); article.append(button);
    } else article.append(element('span', 'resolved-label', '✓ Resolved'));
    list.append(article);
  }
  const events = $('#events'); events.replaceChildren();
  if (!snapshot.events.length) empty(events, 'A fresh page in the journal.', 'Your first ticket will leave a trace here.');
  for (const event of snapshot.events) { const row = element('div', 'event-row'); row.append(element('span', 'event-symbol', event.type === 'demo.fault' ? '⚑' : '↗')); const body = element('div'); body.append(element('strong', '', event.message), element('small', '', event.type)); row.append(body, element('time', '', new Date(event.createdAt).toLocaleString())); events.append(row); }
  const chart = $('#priority-chart'); chart.replaceChildren();
  for (const priority of ['critical', 'high', 'normal', 'low']) { const count = snapshot.tickets.filter(t => t.status === 'open' && t.priority === priority).length; const row = element('div', 'chart-row'); const name = element('span', '', priority); const track = element('div', 'chart-track'); const bar = element('div', `chart-bar ${priority}`); bar.style.width = `${Math.max(1, count / Math.max(1, snapshot.metrics.open) * 100)}%`; track.append(bar); row.append(name, track, element('strong', '', String(count))); chart.append(row); }
}
async function refresh() { try { snapshot = await api('/api/overview'); render(); showError(''); } catch (error) { showError(error.message); } }
function openDialog() { $('#form-error').hidden = true; $('#ticket-dialog').showModal(); }
$('#new-ticket').addEventListener('click', openDialog); $('#care-new-ticket').addEventListener('click', openDialog); $('#close-dialog').addEventListener('click', () => $('#ticket-dialog').close());
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => { const view = button.dataset.view; document.querySelectorAll('[data-view]').forEach(b => b.classList.toggle('active', b === button)); ['queue', 'analytics', 'activity'].forEach(name => { $(`#${name}-view`).hidden = name !== view; }); $('#breadcrumb').textContent = { queue: 'Support queue', analytics: 'At a glance', activity: 'Activity' }[view]; }));
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => { filter = button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('selected', b === button)); render(); }));
$('#search').addEventListener('input', event => { search = event.target.value; render(); });
$('#ticket-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = $('#submit-ticket'); button.disabled = true; button.textContent = 'Creating…'; $('#form-error').hidden = true;
  const payload = JSON.stringify(Object.fromEntries(new FormData(event.target)));
  if (request.payload !== payload) request = { payload, key: crypto.randomUUID() };
  try { await api('/api/tickets', { method: 'POST', body: payload, headers: { 'Idempotency-Key': request.key } }); event.target.reset(); request = { payload: '', key: '' }; $('#ticket-dialog').close(); await refresh(); notify('Ticket created. The conversation starts here.'); } catch (error) { $('#form-error').textContent = error.message; $('#form-error').hidden = false; } finally { button.disabled = false; button.textContent = 'Create ticket ↗'; }
});
void refresh(); setInterval(() => void refresh(), 8000);
