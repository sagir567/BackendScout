const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const desktop = window.matchMedia('(min-width: 1000px)');
const tabs = ['today', 'agent', 'input', 'applications', 'health'];
const inputStages = new Set(['needs_input', 'blocked', 'revision_requested', 'submission_unknown', 'uncertain', 'retryable_failure', 'permanent_failure']);
const finishedStages = new Set(['submitted', 'closed', 'rejected', 'assessment', 'interview', 'offer', 'withdrawn']);
const activeDeliveryStages = new Set(['enriching', 'drafting', 'queued', 'preparing', 'sending', 'delivering', 'applying']);
const stageNames = {
  discovered: 'Discovered', shortlisted: 'Shortlisted', enriching: 'Fetching job details', drafting: 'Drafting',
  review_ready: 'Ready for review', approved: 'Approved', preparing: 'Preparing',
  prepared: 'Prepared', sending: 'Sending', submitted: 'Submitted', needs_input: 'Needs your input',
  revision_requested: 'Revision requested', blocked: 'Blocked', submission_unknown: 'Delivery unconfirmed',
  closed: 'Closed', rejected: 'Rejected', assessment: 'Assessment', interview: 'Interview', offer: 'Offer',
};

const state = {
  session: null, pairingCSRF: null, epoch: 0, tab: 'today', overview: null, overviewLoading: false,
  overviewError: '', overviewRequest: 0, selectedId: null, detail: null,
  detailLoading: false, detailError: '', detailRequest: 0, mutation: null,
  refreshing: false, query: '', mode: 'all', sort: 'newest', notice: null,
  drafts: new Map(), deliveryHolds: new Map(), queued: new Map(),
};

class ApiError extends Error {
  constructor(status, payload, message) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

function present(value) {
  return value !== null && value !== undefined && String(value).trim() !== '';
}

function humanize(value) {
  return String(value ?? '').replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function valueText(value) {
  if (!present(value)) return 'Not reported';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.map(valueText).join('; ') || 'None';
  if (typeof value === 'object') {
    return Object.entries(value).map(([key, part]) => `${humanize(key)}: ${valueText(part)}`).join('; ');
  }
  return String(value);
}

function missing(value, fallback = 'Not listed') {
  return present(value) ? escapeHTML(valueText(value)) : `<span class="missing">${escapeHTML(fallback)}</span>`;
}

function normalizedStage(item) {
  return String(item?.status || 'unknown').toLowerCase().replace(/[ -]+/g, '_');
}

function stageTone(stage) {
  if (['submission_unknown', 'uncertain', 'permanent_failure', 'rejected'].includes(stage)) return 'danger';
  if (inputStages.has(stage)) return 'warning';
  if (['review_ready', 'approved', 'submitted', 'interview', 'offer', 'prepared'].includes(stage)) return 'positive';
  return '';
}

function stageBadge(item) {
  const stage = normalizedStage(item);
  return `<span class="stage ${stageTone(stage)}">${escapeHTML(stageNames[stage] || humanize(stage))}</span>`;
}

function workMode(item) {
  if (!present(item.work_mode)) return '';
  const mode = String(item.work_mode).trim().toLowerCase().replace(/[ _-]/g, '');
  return ({ onsite: 'On-site', hybrid: 'Hybrid', remote: 'Remote' })[mode] || valueText(item.work_mode);
}

function experience(item) {
  const value = item.years_experience;
  if (!present(value)) return '';
  return typeof value === 'number' || /^\d+(?:\.\d+)?$/.test(String(value).trim()) ? `${value} years` : valueText(value);
}

function salary(item) {
  return item.salary_text ?? item.salary ?? item.salary_range ?? '';
}

function scoreMarkup(item) {
  const score = present(item.match_score) ? Number(item.match_score) : NaN;
  return Number.isFinite(score)
    ? `<span class="score">${escapeHTML(Math.round(score))}%<small>match</small></span>`
    : '<span class="score unknown">Not scored<small>match</small></span>';
}

function dateText(value, withTime = false) {
  if (!present(value)) return 'Not reported';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Not reported';
  return new Intl.DateTimeFormat('en', {
    month: 'short', day: 'numeric', year: 'numeric',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  }).format(date);
}

function safeURL(value, { localOnly = false, email = false } = {}) {
  if (typeof value !== 'string' || !value.trim()) return '';
  try {
    let candidate = value.trim();
    if (email && !localOnly && /^[^\s@/?#]+@[^\s@/?#]+\.[^\s@/?#]+$/.test(candidate)) candidate = `mailto:${candidate}`;
    if (!localOnly && !/^(https?:\/\/|mailto:|\/)/i.test(candidate)) return '';
    const url = new URL(candidate, window.location.origin);
    if (url.username || url.password) return '';
    if (email && !localOnly && url.protocol === 'mailto:') return url.href;
    if (!['http:', 'https:'].includes(url.protocol)) return '';
    if (localOnly && url.origin !== window.location.origin) return '';
    return url.href;
  } catch {
    return '';
  }
}

function externalLink(value, label, options = { email: true }) {
  const url = safeURL(value, options);
  return url ? `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(label)} <span aria-hidden="true">&#8599;</span></a>` : '';
}

function trackerText(tracker) {
  if (!present(tracker)) return 'Private workspace';
  if (typeof tracker === 'object') return String(tracker.name || tracker.label || tracker.id || 'Private workspace');
  return `Tracker: ${tracker}`;
}

function errorMessage(payload, status) {
  const detail = payload?.detail;
  if (typeof detail === 'string') return detail;
  if (typeof payload?.message === 'string') return payload.message;
  if (typeof detail?.message === 'string') return detail.message;
  if (status === 429) return 'Too many attempts. Wait a moment before trying again.';
  if (status === 403) return 'This action was not authorized. Refresh the session before trying again.';
  return `The request could not be completed (HTTP ${status}).`;
}

async function api(path, { method = 'GET', body, pairing = false } = {}) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    const token = state.session?.csrf_token || (pairing ? state.pairingCSRF : null);
    if (token) headers['X-CSRF-Token'] = token;
    else if (!pairing) throw new ApiError(403, null, 'The session is missing its security token. Refresh the workspace.');
  }
  let response;
  let text;
  const controller = new AbortController();
  const requestTimeout = setTimeout(() => controller.abort(), 20000);
  try {
    response = await fetch(path, {
      method, headers, credentials: 'same-origin', cache: 'no-store',
      signal: controller.signal,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    text = await response.text();
  } catch {
    if (controller.signal.aborted) {
      throw new ApiError(0, null, 'The request timed out. Refresh to check the current state before repeating an action.');
    }
    throw new ApiError(0, null, 'The server could not be reached. Check your connection and refresh.');
  } finally {
    clearTimeout(requestTimeout);
  }
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); }
    catch {
      if (response.ok) throw new ApiError(response.status, null, 'The server returned an unreadable response. Refresh to check the current state.');
    }
  }
  if (!response.ok) {
    if (response.status === 401 && !pairing) showPairing('Your session has expired. Pair this browser again.');
    throw new ApiError(response.status, payload, errorMessage(payload, response.status));
  }
  return payload;
}

function announce(message) {
  $('#announcer').textContent = message;
}

function setNotice(message, tone = 'info', outcome = null) {
  state.notice = { message, tone, outcome };
  renderNotice();
}

function renderNotice() {
  const region = $('#notice-region');
  region.hidden = !state.notice;
  if (!state.notice) { region.replaceChildren(); return; }
  const { message, tone, outcome } = state.notice;
  const code = outcome?.reason_code || outcome?.detail?.reason_code;
  const correlation = outcome?.correlation_id || outcome?.detail?.correlation_id;
  region.innerHTML = `<div class="notice ${escapeHTML(tone)}" role="${tone === 'error' ? 'alert' : 'status'}">
    <div class="notice-copy"><p>${escapeHTML(message)}</p>${code || correlation ? `<p class="notice-meta">${code ? `Reason: ${escapeHTML(code)}` : ''}${code && correlation ? ' &middot; ' : ''}${correlation ? `Reference: ${escapeHTML(correlation)}` : ''}</p>` : ''}</div>
    <button class="icon-button small" id="dismiss-notice" type="button" aria-label="Dismiss notification" title="Dismiss notification">&times;</button>
  </div>`;
  $('#dismiss-notice').addEventListener('click', () => { state.notice = null; renderNotice(); });
}

function showPairing(message = '', csrfToken = null) {
  state.epoch += 1;
  state.session = null;
  state.pairingCSRF = csrfToken;
  state.overview = null;
  state.detail = null;
  state.selectedId = null;
  state.mutation = null;
  state.notice = null;
  state.overviewLoading = false;
  state.detailLoading = false;
  state.refreshing = false;
  state.drafts.clear();
  state.deliveryHolds.clear();
  state.queued.clear();
  $('#view-panel').replaceChildren();
  $('#notice-region').replaceChildren();
  $('#app').hidden = true;
  $('#boot').hidden = true;
  $('#pair-screen').hidden = false;
  $('.skip-link').href = '#pair-main';
  $('#pair-form').reset();
  $('#pair-error').textContent = message;
  $('#pair-error').hidden = !message;
  $('#pair-submit').disabled = false;
  $('#pair-submit').textContent = 'Pair browser';
  $('#pair-code').disabled = false;
  $('#pair-code').focus();
}

function showWorkspace(session) {
  state.session = session;
  state.pairingCSRF = null;
  $('#boot').hidden = true;
  $('#pair-screen').hidden = true;
  $('#app').hidden = false;
  $('.skip-link').href = '#main-content';
  $('#pair-form').reset();
  renderChrome();
  renderView();
  if (!session.csrf_token) setNotice('The session has no security token. Application actions are unavailable until the session is refreshed.', 'warning');
}

async function start() {
  $('#boot').hidden = false;
  $('#pair-screen').hidden = true;
  $('#app').hidden = true;
  try {
    const session = await api('/api/session');
    if (!session?.authenticated) { showPairing('', session?.csrf_token); return; }
    showWorkspace(session);
    await loadOverview();
    await openRequestedApplication();
  } catch (error) {
    if (error.status === 401) return;
    $('#boot').innerHTML = `<span class="brand-word">BackendScout</span><p class="muted">${escapeHTML(error.message)}</p><button id="retry-session" class="button" type="button">Retry connection</button>`;
    $('#retry-session').addEventListener('click', start);
  }
}

function items() {
  return Array.isArray(state.overview?.items) ? state.overview.items.filter((item) => item && present(item.id)) : [];
}

async function openRequestedApplication() {
  const id = new URLSearchParams(window.location.search).get('application');
  if (id && items().some((item) => String(item.id) === id)) await openDetail(id);
}

function requiresInput(item) {
  return inputStages.has(normalizedStage(item)) || present(item.needs_input_code) || present(item.needs_input_message);
}

function tabItems() {
  if (state.overview?.ready_to_apply) {
    const bucket = { today: 'ready', agent: 'manual', applications: 'history' }[state.tab];
    return bucket ? items().filter((item) => item.workspace_bucket === bucket) : [];
  }
  if (state.tab === 'agent') return items().filter((item) =>
    ['ashby', 'comeet', 'greenhouse', 'lever', 'employer-form'].includes(item.delivery_readiness?.adapter)
    && !['unavailable', 'manual_or_unsupported'].includes(item.delivery_readiness?.state));
  if (state.tab === 'input') return items().filter(requiresInput);
  if (state.tab === 'today') return items().filter((item) => !finishedStages.has(normalizedStage(item)));
  return items();
}

function filteredItems() {
  const query = state.query.trim().toLowerCase();
  const result = tabItems().filter((item) => {
    const text = [item.company, item.title, item.location, item.status, workMode(item)].filter(present).join(' ').toLowerCase();
    const mode = workMode(item).toLowerCase();
    return (!query || text.includes(query)) && (state.mode === 'all' || (state.mode === 'unknown' ? !mode : mode === state.mode));
  });
  return result.sort((a, b) => {
    if (state.sort === 'company') return String(a.company || '').localeCompare(String(b.company || ''));
    if (state.sort === 'score') return (Number(b.match_score ?? -1) || 0) - (Number(a.match_score ?? -1) || 0);
    return (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0);
  });
}

function serverCount(keys, fallback) {
  for (const key of keys) {
    const count = state.overview?.counts?.[key];
    if (typeof count === 'number' && Number.isFinite(count)) return count;
  }
  return fallback;
}

function renderChrome() {
  const tracker = trackerText(state.overview?.tracker ?? state.session?.tracker);
  $('#tracker-label').textContent = tracker;
  $('#footer-tracker').textContent = tracker;
  $('#updated-at').textContent = state.overview?.updated_at ? `Updated ${dateText(state.overview.updated_at, true)}` : 'Update time not reported';
  const streamlined = state.overview?.ready_to_apply === true;
  const names = streamlined
    ? { today: 'Ready to apply', agent: 'Manual apply', applications: 'History' }
    : { today: 'Today', agent: 'Agent Apply', applications: 'Applications' };
  $('#tab-input').hidden = streamlined;
  for (const [tab, name] of Object.entries(names)) {
    $(`#tab-${tab} > span`).textContent = name;
  }
  const counts = streamlined ? {
    today: items().filter((item) => item.workspace_bucket === 'ready').length,
    agent: items().filter((item) => item.workspace_bucket === 'manual').length,
    applications: items().filter((item) => item.workspace_bucket === 'history').length,
    input: 0,
  } : {
    today: serverCount(['today'], items().filter((item) => !finishedStages.has(normalizedStage(item))).length),
    input: serverCount(['needs_input', 'needs_my_input', 'input'], items().filter(requiresInput).length),
    applications: serverCount(['applications', 'total'], items().length),
    agent: serverCount(['agent_apply'], 0),
  };
  for (const tab of ['today', 'agent', 'input', 'applications']) {
    $(`#count-${tab}`).textContent = state.overview ? (counts[tab] > 99 ? '99+' : String(counts[tab])) : '\u2013';
    $(`#count-${tab}`).setAttribute('aria-label', state.overview ? `${counts[tab]} applications` : 'Not loaded');
  }
  $('#health-indicator').hidden = state.overview?.health?.healthy !== false;
  for (const button of $$('.tab')) {
    const selected = button.dataset.tab === state.tab;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  $('#view-panel').setAttribute('aria-labelledby', `tab-${state.tab}`);
  document.title = `${({ today: 'Today', agent: 'Agent Apply', input: 'Needs My Input', applications: 'Applications', health: 'Health' })[state.tab]} | BackendScout`;
  updateBusyUI();
}

function updateBusyUI() {
  const busy = Boolean(state.mutation || state.refreshing);
  $('#refresh-button').disabled = busy || state.overviewLoading;
  $('#refresh-button').setAttribute('aria-busy', String(state.refreshing));
  $('#logout-button').disabled = busy;
  for (const button of $$('[data-mutation]')) {
    button.disabled = busy || state.detailLoading || button.dataset.allowed !== 'true';
    const isCurrent = state.mutation?.action === button.dataset.mutation && state.mutation?.id === state.selectedId;
    button.setAttribute('aria-busy', String(isCurrent));
    button.textContent = isCurrent ? (button.dataset.busyLabel || 'Working...') : button.dataset.label;
  }
  for (const control of $$('[data-edit-control]')) control.disabled = busy || state.detailLoading || control.dataset.editAllowed === 'false';
}

async function loadOverview({ quiet = false } = {}) {
  if (!state.session) return;
  const epoch = state.epoch;
  const request = ++state.overviewRequest;
  const previous = state.overview;
  state.overviewLoading = true;
  state.overviewError = '';
  if (!quiet || !state.overview) renderView();
  renderChrome();
  try {
    const overview = await api('/api/overview');
    if (epoch !== state.epoch || request !== state.overviewRequest) return;
    if (!overview || !Array.isArray(overview.items)) throw new Error('The application list is incomplete. Refresh to try again.');
    state.overview = overview;
  } catch (error) {
    if (epoch !== state.epoch || request !== state.overviewRequest) return;
    state.overviewError = error.message;
    if (state.overview) setNotice(`The workspace could not be refreshed. Showing the last loaded data. ${error.message}`, 'warning', error.payload);
  } finally {
    if (epoch === state.epoch && request === state.overviewRequest) {
      state.overviewLoading = false;
      renderChrome();
      const keepDetail = quiet && previous && $('#queue-results')
        && previous.ready_to_apply === state.overview?.ready_to_apply;
      if (keepDetail) {
        if (JSON.stringify(previous.items) !== JSON.stringify(state.overview.items)) renderResults();
        const count = $('.queue-total');
        if (count) count.textContent = `${tabItems().length} roles`;
      } else {
        renderView();
      }
    }
  }
}

function loadingRows() {
  return `<div class="sr-only" role="status">Loading applications</div><div aria-hidden="true">${Array.from({ length: 3 }, () => '<div class="skeleton-row"><div class="skeleton-line"></div><div class="skeleton-line wide"></div><div class="skeleton-line short"></div></div>').join('')}</div>`;
}

function emptyMarkup(title, message = '', retry = false) {
  return `<div class="empty-state"><h2>${escapeHTML(title)}</h2>${message ? `<p>${escapeHTML(message)}</p>` : ''}${retry ? '<button class="button" type="button" data-retry="overview">Retry</button>' : ''}</div>`;
}

function renderView() {
  if (!state.session) return;
  if (state.tab === 'health') { renderHealth(); return; }
  const streamlined = state.overview?.ready_to_apply === true;
  const heading = $(`#tab-${state.tab} > span`).textContent;
  const subtitle = streamlined
    ? ({ today: 'Reviewed destination and tailored CV', agent: 'Tailored CV and official employer link', applications: 'Application outcomes' })[state.tab] || ''
    : ({ today: 'Active application queue', agent: 'Supported routes; readiness shown per role', input: 'Questions, revisions and blocked applications', applications: 'All stages' })[state.tab];
  const panel = $('#view-panel');
  panel.innerHTML = `<div class="workspace ${state.selectedId ? 'has-detail' : ''}">
    <section class="queue-pane" aria-labelledby="queue-title">
      <header class="queue-heading"><div><h1 id="queue-title">${heading}</h1><p>${subtitle}</p></div><span class="queue-total">${state.overview ? `${tabItems().length} roles` : ''}</span></header>
      <div class="queue-toolbar" role="search" aria-label="Filter applications">
        <div class="search-field"><label for="job-search" class="sr-only">Search applications</label><input id="job-search" type="search" placeholder="Search company, role or location" value="${escapeHTML(state.query)}" autocomplete="off"></div>
        <div><label for="mode-filter" class="sr-only">Work mode</label><select id="mode-filter"><option value="all">All modes</option><option value="remote">Remote</option><option value="hybrid">Hybrid</option><option value="on-site">On-site</option><option value="unknown">Not listed</option></select></div>
        <div><label for="sort-order" class="sr-only">Sort applications</label><select id="sort-order"><option value="newest">Newest first</option><option value="score">Highest match</option><option value="company">Company A-Z</option></select></div>
      </div>
      <div id="queue-results"></div>
    </section>
    <section id="detail-pane" class="detail-pane" aria-label="Application detail"></section>
  </div>`;
  $('#mode-filter').value = state.mode;
  $('#sort-order').value = state.sort;
  $('#job-search').addEventListener('input', (event) => { state.query = event.target.value; renderResults(); });
  $('#mode-filter').addEventListener('change', (event) => { state.mode = event.target.value; renderResults(); });
  $('#sort-order').addEventListener('change', (event) => { state.sort = event.target.value; renderResults(); });
  renderResults();
  renderDetail();
}

function renderResults() {
  const root = $('#queue-results');
  if (!root) return;
  if (state.overviewLoading && !state.overview) { root.innerHTML = loadingRows(); return; }
  if (state.overviewError && !state.overview) {
    root.innerHTML = emptyMarkup('Applications unavailable', state.overviewError, true);
    $('[data-retry]', root)?.addEventListener('click', () => loadOverview());
    return;
  }
  if (!state.overview) { root.innerHTML = loadingRows(); return; }
  const list = filteredItems();
  const filtered = Boolean(state.query || state.mode !== 'all');
  root.innerHTML = `<div class="results-bar"><span>${list.length} ${list.length === 1 ? 'application' : 'applications'}${filtered ? ' found' : ''}</span><span>${state.overviewLoading ? 'Refreshing...' : state.sort === 'score' ? 'By match' : state.sort === 'company' ? 'By company' : 'Newest first'}</span></div>`;
  if (!list.length) {
    const title = filtered ? 'No matching applications' : state.tab === 'input' ? 'Nothing needs your input' : state.tab === 'today' ? 'Your queue is clear' : 'No applications yet';
    root.insertAdjacentHTML('beforeend', emptyMarkup(title, filtered ? 'No results for these filters.' : ''));
    if (filtered) {
      const button = document.createElement('button');
      button.className = 'button button-small';
      button.textContent = 'Clear filters';
      button.addEventListener('click', () => { state.query = ''; state.mode = 'all'; renderView(); $('#job-search')?.focus(); });
      $('.empty-state', root).append(button);
    }
    return;
  }
  root.insertAdjacentHTML('beforeend', `<ul class="job-list">${list.map((item) => {
    const warning = item.needs_input_message || (Array.isArray(item.concerns) && item.concerns.length ? `${item.concerns.length} ${item.concerns.length === 1 ? 'concern' : 'concerns'} to review` : '');
    const id = String(item.id);
    return `<li><button type="button" class="job-row" data-job-id="${escapeHTML(id)}" aria-current="${id === state.selectedId}" aria-label="${escapeHTML(`${item.company || 'Company not listed'}: ${item.title || 'Role not listed'}. ${stageNames[normalizedStage(item)] || humanize(normalizedStage(item))}`)}">
      <span class="job-topline"><span><span class="job-company">${missing(item.company, 'Company not listed')}</span><span class="job-title">${missing(item.title, 'Role not listed')}</span></span>${scoreMarkup(item)}</span>
      <span class="job-location">${missing(item.location, 'Location not listed')}</span>
      <span class="job-facts"><span><span class="muted">Experience</span><span>${missing(experience(item))}</span></span><span><span class="muted">Work mode</span><span>${missing(workMode(item))}</span></span><span><span class="muted">Salary</span><span>${missing(salary(item))}</span></span></span>
      <span class="job-bottomline">${stageBadge(item)}<span class="row-date">${present(item.created_at) ? `Added ${escapeHTML(dateText(item.created_at))}` : 'Date not reported'}</span></span>
      ${state.tab === 'agent' ? `<span class="row-concern">${escapeHTML(item.delivery_readiness?.label || 'Not checked')}</span>` : ''}
      ${warning ? `<span class="row-concern">${escapeHTML(valueText(warning))}</span>` : ''}
    </button></li>`;
  }).join('')}</ul>`);
  for (const button of $$('[data-job-id]', root)) button.addEventListener('click', () => openDetail(button.dataset.jobId));
}

async function openDetail(id) {
  state.selectedId = String(id);
  state.detail = null;
  state.detailError = '';
  renderView();
  await loadDetail();
  if (!desktop.matches && state.selectedId === String(id)) {
    $('#detail-title')?.focus();
    $('#main-content').scrollIntoView({ block: 'start' });
  }
}

async function loadDetail({ quiet = false } = {}) {
  const id = state.selectedId;
  if (!id || !state.session) return;
  const epoch = state.epoch;
  const request = ++state.detailRequest;
  state.detailLoading = true;
  state.detailError = '';
  const previous = state.detail;
  if (!quiet) renderDetail();
  try {
    const detail = await api(`/api/applications/${encodeURIComponent(id)}`);
    if (epoch !== state.epoch || request !== state.detailRequest || state.selectedId !== id) return;
    if (quiet && detailEditorFocused()) return;
    if (!detail?.item || String(detail.item.id) !== id) throw new Error('The server returned an incomplete application. Refresh the review.');
    state.detail = detail;
    const stage = normalizedStage(detail.item);
    if (finishedStages.has(stage)) {
      state.deliveryHolds.delete(id);
      state.queued.delete(id);
    }
    if (inputStages.has(stage) || detail.can_resume === true) state.queued.delete(id);
    if (state.queued.has(id) && state.queued.get(id) !== detail.package?.fingerprint) state.queued.delete(id);
  } catch (error) {
    if (epoch !== state.epoch || request !== state.detailRequest || state.selectedId !== id) return;
    if (!quiet || !previous) {
      state.detailError = error.message;
      state.detail = null;
    }
  } finally {
    if (epoch === state.epoch && request === state.detailRequest && state.selectedId === id) {
      state.detailLoading = false;
      if (!quiet || JSON.stringify(previous) !== JSON.stringify(state.detail)) {
        renderDetail();
        renderResults();
      }
    }
  }
}

function closeDetail() {
  const id = state.selectedId;
  state.selectedId = null;
  state.detail = null;
  state.detailRequest += 1;
  state.detailLoading = false;
  renderView();
  const button = $$('[data-job-id]').find((element) => element.dataset.jobId === id);
  button?.focus();
}

function questionsFor(detail) {
  const questions = detail.pending_questions ?? detail.questions;
  return (Array.isArray(questions) ? questions : []).filter((question) => typeof question === 'string' || (question && typeof question === 'object')).map((question, index) => {
    if (typeof question === 'string') return { key: question, label: question, required: true, options: [] };
    return {
      key: String(question.key ?? question.id ?? question.label ?? index),
      label: String(question.label || question.question || question.prompt || question.text || question.key || question.id || `Question ${index + 1}`),
      required: question.required !== false,
      options: Array.isArray(question.options) ? question.options : question.type === 'boolean' ? ['Yes', 'No'] : [],
    };
  });
}

function draftFor(id) {
  if (!state.drafts.has(id)) state.drafts.set(id, { feedback: '', answers: Object.create(null), details: null });
  return state.drafts.get(id);
}

function jobDetailsMissing(detail) {
  return !present(detail.item.description) || !present(detail.item.official_url)
    || [detail.package_blocker?.reason_code, detail.item.needs_input_code, detail.needs_input_code]
      .some((code) => ['full_posting_required', 'official_route_required'].includes(code));
}

function detailsUnavailable(detail) {
  const codes = [detail.package_blocker?.reason_code, detail.item.needs_input_code, detail.needs_input_code];
  return codes.some((code) => (code || '').startsWith('source_') || /source|remote|fetch|posting|enrich|job_details|job_page/i.test(code || '')
    && /blocked|unavailable|fail|error|unsupported|inaccessible|forbidden|denied|timeout/i.test(code || ''));
}

function reviewGuards(detail) {
  const item = detail.item;
  const pkg = detail.package;
  const stage = normalizedStage(item);
  const fingerprint = typeof pkg?.fingerprint === 'string' ? pkg.fingerprint.trim() : '';
  const cv = safeURL(pkg?.cv_url, { localOnly: true });
  const archived = finishedStages.has(stage) || detail.package_blocker?.reason_code === 'historical';
  const uncertain = ['submission_unknown', 'uncertain'].includes(stage) || state.deliveryHolds.has(String(item.id));
  const inFlight = activeDeliveryStages.has(stage) || detail.package_blocker?.reason_code === 'enriching'
    || state.queued.get(String(item.id)) === fingerprint;
  const enabled = detail.delivery_enabled !== false && pkg?.delivery_enabled !== false && item.delivery_enabled !== false;
  const awaitingInput = questionsFor(detail).length > 0 || stage === 'needs_input'
    || present(item.needs_input_code) || present(item.needs_input_message)
    || present(detail.needs_input_code) || present(detail.needs_input_message)
    || detail.outcome?.status === 'needs_input' || detail.outcome === 'needs_input';
  const routeComplete = present(pkg?.delivery_route) && present(pkg?.official_destination);
  const evidenceComplete = Boolean(fingerprint && cv && routeComplete
    && present(item.description) && Array.isArray(pkg?.answers));
  const blocked = Boolean(detail.package_blocker);
  const routeBlocked = ['posting_unavailable', 'posting_identity_mismatch', 'posting_url_invalid', 'posting_redirect_unverified'].includes(detail.package_blocker?.reason_code);
  const complete = evidenceComplete && enabled && !awaitingInput && !blocked;
  const authenticated = Boolean(state.session?.csrf_token);
  const editable = authenticated && !archived && !uncertain && !inFlight;
  return {
    fingerprint, cv, archived, uncertain, inFlight, enabled, awaitingInput, routeComplete, routeBlocked,
    approve: editable && complete && pkg?.approved !== true,
    prepare: editable && complete && pkg?.approved === true,
    deliver: editable && evidenceComplete && enabled && !blocked && pkg?.approved === true && detail.can_resume === true,
    answers: editable && questionsFor(detail).length > 0,
    revise: editable && !routeBlocked,
    tailor: editable && !routeBlocked,
    details: editable, 'refresh-details': editable,
    'inspect-form': editable && (item.delivery_readiness?.adapter === 'greenhouse' || detail.delivery_mode === 'hybrid') && !detail.form_check_pending,
  };
}

function actionButton(action, label, allowed, className = '', busyLabel = 'Working...', describedBy = '') {
  const fingerprint = state.detail?.package?.fingerprint || '';
  return `<button class="button ${className}" type="${['answers', 'revise', 'details'].includes(action) ? 'submit' : 'button'}" data-mutation="${action}" data-allowed="${allowed}" data-label="${escapeHTML(label)}" data-busy-label="${escapeHTML(busyLabel)}" data-fingerprint="${escapeHTML(fingerprint)}" ${describedBy ? `aria-describedby="${escapeHTML(describedBy)}"` : ''} ${allowed && !state.mutation && !state.refreshing && !state.detailLoading ? '' : 'disabled'}>${escapeHTML(label)}</button>`;
}

function detailsEditor(detail, guards) {
  const item = detail.item;
  const draft = draftFor(String(item.id));
  const values = draft.details || item;
  return `<details id="details-editor" class="details-editor" ${draft.detailsOpen && guards.details ? 'open' : ''}>
    <summary>Edit job details manually</summary>
    <form id="details-form"><fieldset data-edit-control data-edit-allowed="${guards.details}" aria-label="Manual job details" ${guards.details ? '' : 'disabled'}>
      <div class="form-field"><label for="job-description">Job description <span class="required-note">Required</span></label><textarea id="job-description" name="description" rows="6" required data-edit-control>${escapeHTML(values.description || '')}</textarea></div>
      <div class="form-field"><label for="official-url">Official application URL <span class="required-note">Required</span></label><input id="official-url" name="official_url" type="url" required value="${escapeHTML(values.official_url || '')}" placeholder="https://" data-edit-control></div>
      <div class="form-field"><label for="years-experience">Required years of experience</label><input id="years-experience" name="years_experience" type="text" value="${escapeHTML(values.years_experience ?? '')}" placeholder="Not listed" data-edit-control></div>
      <p class="action-context">Saving changes invalidates the previous package approval.</p>
      ${actionButton('details', 'Save details', guards.details, '', 'Saving...')}
    </fieldset></form>
  </details>`;
}

function pdfMarkup(url, item, exact = true, previewURL = '') {
  const safe = safeURL(url, { localOnly: true });
  if (!safe) return '<p class="inline-warning">Private PDF unavailable.</p>';
  const preview = safeURL(previewURL, { localOnly: true });
  const label = exact ? 'Exact package PDF' : finishedStages.has(normalizedStage(item)) ? 'Archived CV' : 'Existing CV';
  const media = preview
    ? `<figure class="pdf-preview-surface"><img class="pdf-preview" src="${escapeHTML(preview)}" alt="${escapeHTML(`${item.company || 'Application'} CV, page 1 of the ${label.toLowerCase()}`)}" width="840" height="1188" loading="lazy" decoding="async" referrerpolicy="same-origin"><p class="pdf-preview-error" role="status" hidden>Page preview unavailable. PDF download available.</p></figure>`
    : `<iframe class="pdf-frame" src="${escapeHTML(safe)}" title="${escapeHTML(`${item.company || 'Application'} ${label.toLowerCase()}`)}" referrerpolicy="same-origin" loading="lazy"></iframe>`;
  return `<div id="cv-review" class="pdf-heading"><h3>${label}</h3><div class="pdf-actions"><a class="button button-small" href="${escapeHTML(safe)}" download>Download PDF <span aria-hidden="true">&#8595;</span></a></div></div>
    ${media}
    <p class="pdf-fallback">${exact ? preview ? 'Page 1 of the exact package PDF.' : 'Exact package PDF.' : 'No approvable package for this CV.'}</p>`;
}

function reviewActionReason(detail, guards, resume = false) {
  if (!state.session?.csrf_token) return 'Refresh the session to restore authorization.';
  if (guards.archived) return 'This application is no longer in the delivery queue.';
  if (guards.uncertain) return 'Delivery is unconfirmed. Reconciliation is required before another attempt.';
  if (guards.inFlight) {
    const stage = normalizedStage(detail.item);
    return stage === 'enriching' || detail.package_blocker?.reason_code === 'enriching' ? 'Fetching job details.'
      : stage === 'drafting' ? 'Creating CV.' : 'Application queued or in progress.';
  }
  if (detail.package_blocker) return detail.package_blocker.message || humanize(detail.package_blocker.reason_code) || 'Review package unavailable.';
  if (detailsUnavailable(detail)) return detail.item.needs_input_message || detail.needs_input_message || 'Job details unavailable from source.';
  if (!present(detail.item.description)) return 'Job description unavailable.';
  if (!present(detail.item.official_url)) return 'Official application URL unavailable.';
  if (!detail.package) return detail.item.has_cv || detail.item.cv_url ? 'CV available. Review package unavailable.' : 'No CV created.';
  if (!guards.enabled) return detail.item.needs_input_message || detail.needs_input_message || detail.outcome?.message || 'Delivery is disabled for this application.';
  if (!guards.routeComplete) return 'The official destination or delivery route is missing.';
  if (!guards.cv) return 'An exact private PDF is required before applying.';
  if (!guards.fingerprint) return 'A current package fingerprint is required before applying.';
  if (!Array.isArray(detail.package?.answers)) return 'The reviewed answers are unavailable. Refresh the review.';
  if (resume && detail.can_resume !== true) return 'Complete the pending input before delivery can resume.';
  if (!resume && guards.awaitingInput) return detail.item.needs_input_message || detail.needs_input_message || detail.outcome?.message || 'Answers required before applying.';
  return 'Refresh the review to check the current package state.';
}

function workflowMarkup(detail, guards) {
  const { item, package: pkg } = detail;
  // An existing CV remains viewable, but never substitutes for the exact package PDF.
  const cv = guards.cv || safeURL(item.cv_url, { localOnly: true });
  const hasCV = Boolean(cv || item.has_cv);
  const approved = pkg?.approved === true;
  const resume = approved && (detail.can_resume === true || normalizedStage(item) === 'needs_input');
  const action = !approved ? 'approve' : resume ? 'deliver' : '';
  const enriching = normalizedStage(item) === 'enriching' || detail.package_blocker?.reason_code === 'enriching';
  const fetchDetails = !guards.archived && !guards.routeBlocked && (jobDetailsMissing(detail) || detailsUnavailable(detail) || enriching);
  const blocked = !action || !guards[action];
  const reason = blocked ? approved && !action && !guards.archived && !guards.uncertain && !guards.inFlight && !detail.package_blocker
    ? 'Exact package approved.' : reviewActionReason(detail, guards, resume) : '';
  const status = cv ? 'CV available' : hasCV ? 'CV unavailable' : 'No CV yet';
  return `<section class="workflow-section" aria-labelledby="workflow-heading">
    <div class="section-heading"><h2 id="workflow-heading">CV &amp; application</h2><span class="stage ${cv ? 'positive' : ''}">${status}</span></div>
    ${fetchDetails ? `<div class="details-recovery">${actionButton('refresh-details', 'Fetch job details', guards['refresh-details'], 'button-primary', 'Fetching...')}</div>` : ''}
    <div class="primary-actions">
      ${actionButton('tailor', 'Create CV', guards.tailor, !hasCV && !fetchDetails ? 'button-primary' : '', 'Creating...', 'review-action-context')}
      ${cv ? `<a class="button" id="view-cv" href="${escapeHTML(cv)}" target="_blank" rel="noopener noreferrer">View CV <span aria-hidden="true">&#8599;</span></a>` : '<button class="button" id="view-cv" type="button" disabled>View CV</button>'}
      ${action ? actionButton(action, resume ? 'Resume delivery' : 'Approve & apply', guards[action], 'button-primary approve-action', resume ? 'Resuming...' : 'Approving...', 'review-action-context') : ''}
      ${guards.archived ? '' : `<button id="request-changes" class="button" type="button" aria-controls="revision-panel" aria-expanded="${Boolean(draftFor(String(item.id)).revisionOpen)}" data-edit-control data-edit-allowed="${guards.revise}" ${guards.revise ? '' : 'disabled'}>Request changes</button>`}
    </div>
    ${reason ? `<p id="review-action-context" class="workflow-status ${guards.uncertain || detailsUnavailable(detail) ? 'inline-warning' : 'action-context'}" role="status">${detailsUnavailable(detail) && !enriching ? '<strong>Job details unavailable.</strong> ' : ''}${escapeHTML(reason)}</p>` : '<span id="review-action-context" class="sr-only">Exact current PDF, destination and reviewed answers.</span>'}
    ${guards.routeBlocked ? `<button id="edit-application-link" class="button" type="button" aria-controls="details-editor" ${guards.details ? '' : 'disabled'}>Update application link</button>` : ''}
    ${revisionMarkup(detail, guards)}
  </section>`;
}

function deliveryReadinessMarkup(detail, guards) {
  const readiness = detail.item.delivery_readiness;
  if (!readiness) return '';
  const labels = {
    official_route_required: 'Official application link needed', full_posting_required: 'Full posting needed',
    cv_required: 'Tailored CV needed', package_required: 'Exact package review needed',
    inspection_required: 'Form inspection needed', input_required: 'Answers needed',
    lifecycle_unavailable: 'Not available for a new application', delivery_unresolved: 'Previous attempt needs reconciliation',
    manual_or_unsupported_portal: 'Manual or unsupported portal',
    posting_unavailable: 'Saved application link unavailable', posting_identity_mismatch: 'Destination identity needs verification',
    posting_url_invalid: 'Application link needs correction', posting_redirect_unverified: 'Application redirect needs verification',
  };
  const inspection = detail.form_inspection;
  const route = detail.item.verified_route || detail.item.application_route;
  const browser = detail.item.browser_observation;
  const verifiedTarget = safeURL(route?.application_url);
  const questions = Array.isArray(inspection?.questions) ? inspection.questions : [];
  return `<section class="detail-section" aria-labelledby="readiness-heading">
    <div class="section-heading"><h2 id="readiness-heading">Agent delivery</h2><span class="stage ${readiness.state === 'supported_after_approval' ? 'positive' : 'warning'}">${escapeHTML(readiness.label)}</span></div>
    ${readiness.adapter ? `<p class="action-context">Route: ${escapeHTML(humanize(readiness.adapter))}</p>` : ''}
    ${route ? `<dl class="route-facts"><div><dt>Verified destination</dt><dd>${verifiedTarget ? `<a href="${escapeHTML(verifiedTarget)}" target="_blank" rel="noopener noreferrer">${escapeHTML(new URL(verifiedTarget).host)}</a>` : 'Not verified'}</dd></div><div><dt>Route status</dt><dd>${escapeHTML(humanize(route.status))}</dd></div></dl>` : ''}
    ${browser ? `<p class="action-context">${escapeHTML(humanize(browser.details?.state || browser.phase))}${browser.details?.required_action || browser.details?.message ? `: ${escapeHTML(browser.details.required_action || browser.details.message)}` : ''}</p>` : ''}
    <ul class="concern-list">${(readiness.reasons || []).map((reason) => `<li>${escapeHTML(labels[reason] || humanize(reason))}</li>`).join('')}</ul>
    ${(readiness.adapter === 'greenhouse' || detail.delivery_mode === 'hybrid') && !guards.archived ? actionButton('inspect-form', detail.form_check_pending ? 'Checking form...' : 'Check form', guards['inspect-form'], '', 'Queueing...') : ''}
    ${inspection ? `${inspection.message || inspection.checked_at ? `<p class="action-context">${escapeHTML(inspection.message || '')}${inspection.checked_at ? ` &middot; ${escapeHTML(dateText(inspection.checked_at, true))}` : ''}</p>` : ''}
      ${questions.length ? `<details><summary>Published form fields (${questions.length})</summary><ul class="concern-list">${questions.map((q) => `<li>${escapeHTML(q.label)}${q.required ? ' (required)' : ''}</li>`).join('')}</ul></details>` : ''}` : ''}
  </section>`;
}

function packageMarkup(detail, guards) {
  const { item, package: pkg } = detail;
  if (!pkg) {
    return `<section class="detail-section" aria-labelledby="package-heading"><div class="section-heading"><h2 id="package-heading">Application package</h2><span class="stage">${item.has_cv || item.cv_url ? 'Review unavailable' : 'Not prepared'}</span></div>
      ${safeURL(item.cv_url, { localOnly: true }) ? pdfMarkup(item.cv_url, item, false, item.cv_preview_url) : `<p class="muted">${item.has_cv ? 'Private PDF unavailable.' : 'No CV created.'}</p>`}
    </section>`;
  }
  const answers = Array.isArray(pkg.answers) ? pkg.answers.filter((answer) => answer && typeof answer === 'object' && !String(answer.key).startsWith('review.')) : [];
  const formBinding = Array.isArray(pkg.answers) ? pkg.answers.find((answer) => answer?.key === 'review.form_schema_sha256')?.value : null;
  const destinationLink = safeURL(pkg.official_destination, { email: true });
  const destination = destinationLink ? `<a href="${escapeHTML(destinationLink)}" target="_blank" rel="noopener noreferrer">${escapeHTML(valueText(pkg.official_destination))}</a>` : missing(pkg.official_destination);
  const approved = pkg.approved === true;
  return `<section class="detail-section" aria-labelledby="package-heading">
    <div class="section-heading"><div><h2 id="package-heading">Package review</h2><p class="package-version">Package version ${missing(pkg.version, 'not reported')}</p></div><span class="stage ${approved ? 'positive' : 'warning'}">${approved ? 'Approved' : 'Not approved'}</span></div>
    <dl class="route-facts"><div><dt>Delivery route</dt><dd>${missing(present(pkg.delivery_route) ? humanize(pkg.delivery_route) : '')}</dd></div><div><dt>Official destination</dt><dd>${destination}</dd></div><div><dt>CV version</dt><dd>${missing(pkg.cv_version, 'Not reported')}</dd></div></dl>
    ${pdfMarkup(pkg.cv_url, item, true, pkg.cv_preview_url)}
    <div class="answers-section"><h3>Reviewed answers</h3>${answers.length ? `<dl class="answer-list">${answers.map((answer) => `<div><dt>${escapeHTML(humanize(String(answer.key || 'Answer').replace(/^identity\./, '')))}</dt><dd>${missing(answer.value, 'No answer recorded')}</dd></div>`).join('')}</dl>` : `<p class="muted action-context">${Array.isArray(pkg.answers) ? 'No answers included in this package.' : 'Not reported.'}</p>`}</div>
    ${present(pkg.email_subject) ? `<div class="email-review"><h3>Email subject</h3><p class="prose">${escapeHTML(pkg.email_subject)}</p></div>` : ''}
    ${present(pkg.email_body) ? `<div class="email-review"><h3>Email body</h3><p class="prose">${escapeHTML(pkg.email_body)}</p></div>` : ''}
    <details class="technical-details"><summary>Technical details</summary><dl class="route-facts"><div><dt>Package fingerprint</dt><dd><code class="fingerprint">${missing(guards.fingerprint, 'Not available')}</code></dd></div>${formBinding ? `<div><dt>Form review signature</dt><dd><code class="fingerprint">${escapeHTML(formBinding)}</code></dd></div>` : ''}</dl></details>
  </section>`;
}

function answersMarkup(detail, guards) {
  const questions = questionsFor(detail);
  if (!questions.length) return '';
  const draft = draftFor(String(detail.item.id));
  const fields = questions.map((question, index) => {
    const id = `question-${index}`;
    const value = draft.answers[question.key] || '';
    const control = question.options.length
      ? `<select id="${id}" data-question="${escapeHTML(question.key)}" ${question.required ? 'required' : ''} data-edit-control><option value="">Select an answer</option>${question.options.map((option) => {
        const optionValue = typeof option === 'object' ? option.value ?? option.label : option;
        const optionLabel = typeof option === 'object' ? option.label ?? option.value : option;
        return `<option value="${escapeHTML(optionValue)}" ${String(optionValue) === value ? 'selected' : ''}>${escapeHTML(optionLabel)}</option>`;
      }).join('')}</select>`
      : `<textarea id="${id}" rows="2" data-question="${escapeHTML(question.key)}" ${question.required ? 'required' : ''} data-edit-control>${escapeHTML(value)}</textarea>`;
    return `<div class="form-field"><label for="${id}">${escapeHTML(question.label)} ${question.required ? '<span class="required-note">Required</span>' : ''}</label>${control}</div>`;
  }).join('');
  return `<section class="detail-section" aria-labelledby="answers-heading"><div class="section-heading"><h2 id="answers-heading">Your input</h2><span class="stage warning">${questions.length} ${questions.length === 1 ? 'question' : 'questions'}</span></div>
    <form id="answers-form" class="revision-form"><fieldset data-edit-control data-edit-allowed="${guards.answers}" aria-label="Application answers" ${guards.answers ? '' : 'disabled'}>${fields}
      <p class="action-context">Answers update the package without redrafting the CV. A changed package needs a new approval.</p>
      ${actionButton('answers', 'Save answers', guards.answers, '', 'Saving...')}
    </fieldset></form></section>`;
}

function revisionMarkup(detail, guards) {
  if (guards.archived) return '';
  const draft = draftFor(String(detail.item.id));
  return `<details id="revision-panel" class="revision-panel" ${draft.revisionOpen ? 'open' : ''}><summary>CV feedback${draft.feedback ? ' (draft)' : ''}</summary>
    <form id="revision-form" class="revision-form"><fieldset data-edit-control data-edit-allowed="${guards.revise}" aria-label="CV revision" ${guards.revise ? '' : 'disabled'}><div class="form-field"><label for="revision-feedback">Requested changes</label><textarea id="revision-feedback" name="feedback" rows="3" required data-edit-control>${escapeHTML(draft.feedback)}</textarea></div>
      <p class="action-context">A revised CV package needs a new approval.</p>
      ${actionButton('revise', 'Send request', guards.revise, '', 'Sending...')}
    </fieldset></form></details>`;
}

function renderDetail() {
  const root = $('#detail-pane');
  if (!root) return;
  $('.workspace')?.classList.toggle('has-detail', Boolean(state.selectedId));
  if (!state.selectedId) { root.innerHTML = '<div class="detail-placeholder">No application selected</div>'; return; }
  const back = '<button id="detail-back" class="button button-quiet detail-back" type="button"><span aria-hidden="true">&#8592;</span> Back to applications</button>';
  if (state.detailLoading && !state.detail) {
    root.innerHTML = `<div class="detail-inner">${back}<div class="empty-state"><span class="loading-line" role="status"><span class="spinner" aria-hidden="true"></span> Loading application</span></div></div>`;
    $('#detail-back').addEventListener('click', closeDetail);
    return;
  }
  if (state.detailError || !state.detail) {
    root.innerHTML = `<div class="detail-inner">${back}${emptyMarkup('Review unavailable', state.detailError || 'Refresh this application.') }<button id="retry-detail" class="button" type="button">Refresh review</button></div>`;
    $('#detail-back').addEventListener('click', closeDetail);
    $('#retry-detail').addEventListener('click', loadDetail);
    return;
  }
  const detail = state.detail;
  const item = detail.item;
  const guards = reviewGuards(detail);
  const concerns = Array.isArray(item.concerns) ? item.concerns : [];
  const official = externalLink(item.official_url, 'Official application');
  const source = externalLink(item.source_url, 'Original listing');
  const streamlined = state.overview?.ready_to_apply === true;
  const manual = streamlined && item.workspace_bucket === 'manual';
  const ready = streamlined && item.workspace_bucket === 'ready';
  const reviewed = detail.package?.approved === true;
  root.innerHTML = `<div class="detail-inner">${back}
    <header class="detail-heading"><p class="detail-company">${missing(item.company, 'Company not listed')}</p><h2 id="detail-title" class="detail-title" tabindex="-1">${missing(item.title, 'Role not listed')}</h2><p class="detail-location">${missing(item.location, 'Location not listed')}</p><div class="detail-status">${stageBadge(item)}${scoreMarkup(item)}</div></header>
    <dl class="facts-grid"><div><dt>Required experience</dt><dd>${missing(experience(item))}</dd></div><div><dt>Work mode</dt><dd>${missing(workMode(item))}</dd></div><div><dt>Salary</dt><dd>${missing(salary(item))}</dd></div><div><dt>Added</dt><dd>${escapeHTML(dateText(item.created_at))}</dd></div></dl>
    ${manual ? `<section class="workflow-section"><h2>Manual application</h2><p>${missing(detail.form_inspection?.message, 'The employer form is not supported for automatic submission.')}</p><div class="link-row">${externalLink(item.cv_url, 'View CV', { localOnly: true })}${official}</div></section>` : ''}
    ${ready ? `<section class="workflow-section"><div class="primary-actions">${reviewed ? actionButton('deliver', 'Resume delivery', guards.deliver, 'button-primary', 'Resuming...') : actionButton('approve', 'Apply with this CV', guards.approve, 'button-primary', 'Approving...')}<button id="request-changes" class="button" type="button">Request CV changes</button></div>${revisionMarkup(detail, guards)}</section>` : ''}
    ${!streamlined ? workflowMarkup(detail, guards) + deliveryReadinessMarkup(detail, guards) + answersMarkup(detail, guards) : ''}
    ${!streamlined || ready ? packageMarkup(detail, guards) : ''}
    <section class="detail-section" aria-labelledby="job-facts-heading"><div class="section-heading"><h2 id="job-facts-heading">Role &amp; fit</h2></div><p class="prose">${missing(item.match_reason, 'Match reason not provided.')}</p>
      <details class="description-details"><summary>Job description</summary><div class="prose">${missing(item.description, 'Job description not provided.')}</div></details>
      <h3 class="gaps-heading">Gaps &amp; concerns</h3>${concerns.length ? `<ul class="concern-list">${concerns.map((concern) => `<li>${escapeHTML(concern && typeof concern === 'object' ? concern.message || concern.description || concern.reason || valueText(concern) : valueText(concern))}</li>`).join('')}</ul>` : '<p class="muted action-context">No concerns reported.</p>'}
      <div class="link-row">${official || '<span class="missing">Official application URL not listed</span>'}${source || '<span class="missing">Source URL not listed</span>'}</div>
      ${streamlined ? '' : detailsEditor(detail, guards)}
    </section>
    ${detail.submission?.proof_url && safeURL(detail.submission.proof_url, { localOnly: true }) ? `<section class="detail-section"><h2>Submission confirmed</h2><p class="action-context">${escapeHTML(dateText(detail.submission.timestamp, true))} &middot; CV ${escapeHTML(detail.submission.cv_version)}</p><img class="receipt-proof" src="${escapeHTML(safeURL(detail.submission.proof_url, { localOnly: true }))}" alt="Portal confirmation screenshot" loading="lazy"></section>` : ''}
    ${present(item.legacy_status) ? `<p class="action-context">Previous tracker stage: ${escapeHTML(valueText(item.legacy_status))}</p>` : ''}
  </div>`;
  $('#detail-back').addEventListener('click', closeDetail);
  $('#edit-application-link')?.addEventListener('click', () => {
    draftFor(String(item.id)).detailsOpen = true;
    renderDetail();
    $('#official-url')?.scrollIntoView({ block: 'center' });
    $('#official-url')?.focus();
  });
  for (const preview of $$('.pdf-preview', root)) {
    preview.addEventListener('error', () => {
      preview.classList.add('is-unavailable');
      $('.pdf-preview-error', preview.parentElement).hidden = false;
    });
  }
  for (const button of $$('[data-mutation]', root)) {
    if (!['answers', 'revise', 'details'].includes(button.dataset.mutation)) button.addEventListener('click', () => mutate(button.dataset.mutation, {}, button.dataset.fingerprint));
  }
  $('#answers-form')?.addEventListener('input', saveAnswersDraft);
  $('#answers-form')?.addEventListener('change', saveAnswersDraft);
  $('#answers-form')?.addEventListener('submit', submitAnswers);
  $('#revision-form')?.addEventListener('input', saveRevisionDraft);
  $('#revision-form')?.addEventListener('change', saveRevisionDraft);
  $('#revision-form')?.addEventListener('submit', submitRevision);
  $('#revision-panel')?.addEventListener('toggle', (event) => {
    if (!event.currentTarget.isConnected) return;
    const open = event.currentTarget.open;
    draftFor(String(item.id)).revisionOpen = open;
    $('#request-changes')?.setAttribute('aria-expanded', String(open));
  });
  $('#request-changes')?.addEventListener('click', () => {
    const panel = $('#revision-panel');
    panel.open = true;
    draftFor(String(item.id)).revisionOpen = true;
    $('#request-changes').setAttribute('aria-expanded', 'true');
    $('#revision-feedback').focus();
  });
  $('#details-editor')?.addEventListener('toggle', (event) => {
    if (event.currentTarget.isConnected) draftFor(String(item.id)).detailsOpen = event.currentTarget.open;
  });
  $('#details-form')?.addEventListener('input', saveDetailsDraft);
  $('#details-form')?.addEventListener('submit', submitDetails);
  updateBusyUI();
}

function saveRevisionDraft() {
  if (!state.selectedId) return;
  const draft = draftFor(state.selectedId);
  draft.feedback = $('#revision-feedback').value;
}

function saveAnswersDraft() {
  if (!state.selectedId) return;
  const draft = draftFor(state.selectedId);
  for (const control of $$('[data-question]')) draft.answers[control.dataset.question] = control.value;
}

function saveDetailsDraft() {
  if (!state.selectedId) return;
  draftFor(state.selectedId).details = {
    description: $('#job-description').value,
    official_url: $('#official-url').value,
    years_experience: $('#years-experience').value,
  };
}

function submitAnswers(event) {
  event.preventDefault();
  if (!state.detail) return;
  saveAnswersDraft();
  const draft = draftFor(state.selectedId);
  const answers = Object.create(null);
  const questions = questionsFor(state.detail);
  for (const [index, question] of questions.entries()) {
    const answer = draft.answers[question.key]?.trim();
    if (!answer && question.required) { $(`#question-${index}`)?.focus(); return; }
    if (answer) answers[question.key] = answer;
  }
  if (!Object.keys(answers).length) return;
  return mutate('answers', { answers }, $('[data-mutation="answers"]').dataset.fingerprint);
}

function submitRevision(event) {
  event.preventDefault();
  if (!state.detail) return;
  saveRevisionDraft();
  const feedback = draftFor(state.selectedId).feedback.trim();
  if (!feedback) { $('#revision-feedback').focus(); return; }
  const button = $('[data-mutation="revise"]');
  return mutate('revise', { feedback }, button.dataset.fingerprint);
}

function submitDetails(event) {
  event.preventDefault();
  saveDetailsDraft();
  const values = draftFor(state.selectedId).details;
  const officialURL = values.official_url.trim();
  if (!safeURL(officialURL, { email: true }) || !/^(https?:|mailto:)/i.test(officialURL)) {
    $('#official-url').setCustomValidity('Enter a complete official application URL.');
    $('#official-url').reportValidity();
    $('#official-url').addEventListener('input', () => $('#official-url')?.setCustomValidity(''), { once: true });
    return;
  }
  mutate('details', {
    description: values.description.trim(), official_url: officialURL,
    years_experience: values.years_experience.trim(),
  }, $('[data-mutation="details"]').dataset.fingerprint);
}

async function mutate(action, extra = {}, renderedFingerprint = '') {
  if (state.mutation || state.refreshing || state.detailLoading || !state.detail || !state.session) return;
  const id = state.selectedId;
  const epoch = state.epoch;
  const detail = state.detail;
  const guards = reviewGuards(detail);
  if (renderedFingerprint !== (detail.package?.fingerprint || '')) {
    setNotice('The package changed. Refresh and review the current version before acting.', 'warning');
    await loadDetail();
    return;
  }
  if (!guards[action]) { setNotice('This action is not available for the current application package. Refresh its review.', 'warning'); return; }
  let body = extra;
  if (['approve', 'deliver'].includes(action)) body = { fingerprint: guards.fingerprint };
  if (action === 'revise' && guards.fingerprint) body = { ...extra, fingerprint: guards.fingerprint };
  state.mutation = { id, action, fingerprint: guards.fingerprint };
  updateBusyUI();
  try {
    const outcome = await api(`/api/applications/${encodeURIComponent(id)}/${action}`, { method: 'POST', body });
    if (epoch !== state.epoch) return;
    const status = outcome?.status;
    const succeeded = status === 'succeeded';
    const uncertain = status === 'uncertain' || status === 'submission_unknown' || /uncertain|submission_unknown/.test(outcome?.reason_code || '');
    if (uncertain || (['approve', 'deliver'].includes(action) && !['succeeded', 'blocked', 'needs_input', 'retryable_failure', 'permanent_failure'].includes(status))) state.deliveryHolds.set(id, guards.fingerprint);
    if (succeeded && action === 'approve') state.queued.set(id, guards.fingerprint);
    if (succeeded && action === 'revise') {
      draftFor(id).feedback = '';
    }
    if (succeeded && action === 'answers') draftFor(id).answers = Object.create(null);
    if (succeeded && action === 'details') {
      draftFor(id).details = null;
      state.queued.delete(id);
    }
    const messages = {
      approve: 'Exact package approved and application queued.',
      prepare: 'Preparation completed. Review the current application state.',
      deliver: 'Delivery action completed. Review the current application state.',
      answers: 'Answers saved. Review the updated package before applying.',
      revise: 'Revision requested. Review the new package before approving it.',
      details: 'Job details saved. Review the current package before approving it.',
      tailor: 'CV creation queued.',
      'refresh-details': 'Job details refresh requested.',
      'inspect-form': 'Public form check queued.',
    };
    const fallback = succeeded ? messages[action] : 'The action did not complete. Refresh and review the application state before trying again.';
    setNotice(outcome?.message || fallback, succeeded ? 'info' : uncertain || ['blocked', 'needs_input', 'retryable_failure'].includes(status) ? 'warning' : 'error', outcome);
  } catch (error) {
    if (epoch !== state.epoch || error.status === 401) return;
    if (error.status === 409) {
      setNotice('This review is stale or the application is already in progress. The latest state has been requested; review it before taking another action.', 'warning', error.payload);
    } else {
      if (['approve', 'deliver'].includes(action) && (error.status === 0 || error.status >= 500 || error.status === 200)) {
        state.deliveryHolds.set(id, guards.fingerprint);
        setNotice('The application action could not be confirmed. Its current state has been requested. Do not repeat delivery until the existing attempt is reconciled.', 'error', error.payload);
      } else setNotice(error.message, 'error', error.payload);
    }
  } finally {
    if (epoch === state.epoch) {
      try {
        await loadOverview({ quiet: true });
        if (state.selectedId === id) await loadDetail();
      } finally {
        if (epoch === state.epoch) { state.mutation = null; updateBusyUI(); }
      }
    }
  }
}

function healthStatus(value) {
  if (value?.healthy === true) return { label: 'Healthy', tone: 'positive' };
  if (value?.healthy === false) return { label: 'Needs attention', tone: 'danger' };
  if (present(value?.status)) {
    const status = String(value.status).toLowerCase();
    return { label: humanize(status), tone: ['ok', 'healthy', 'succeeded'].includes(status) ? 'positive' : ['error', 'failed', 'blocked', 'unhealthy'].includes(status) ? 'danger' : 'warning' };
  }
  return { label: 'Not reported', tone: '' };
}

function renderHealth() {
  const root = $('#view-panel');
  if (!state.overview) {
    root.innerHTML = `<section class="health-page"><h1>Health</h1>${state.overviewError ? emptyMarkup('Health unavailable', state.overviewError, true) : '<div class="empty-state"><span class="loading-line" role="status"><span class="spinner" aria-hidden="true"></span> Loading health</span></div>'}</section>`;
    $('[data-retry]', root)?.addEventListener('click', () => loadOverview());
    return;
  }
  const health = state.overview.health;
  const status = healthStatus(health);
  let components = [];
  if (Array.isArray(health)) components = health.map((value, index) => [value?.name || value?.component || `Check ${index + 1}`, value]);
  else if (health && typeof health === 'object') {
    const source = health.components ?? health.checks;
    if (Array.isArray(source)) components = source.map((value, index) => [value?.name || value?.component || `Check ${index + 1}`, value]);
    else if (source && typeof source === 'object') components = Object.entries(source);
    else components = Object.entries(health).filter(([key, value]) => value !== null && typeof value === 'object' && !['outcome'].includes(key));
  }
  const topFacts = health && typeof health === 'object' && !Array.isArray(health)
    ? Object.entries(health).filter(([key, value]) => !['healthy', 'status', 'components', 'checks'].includes(key) && (value === null || typeof value !== 'object')) : [];
  root.innerHTML = `<section class="health-page" aria-labelledby="health-title"><header class="health-heading"><div><p class="eyebrow">Operations</p><h1 id="health-title">Health</h1><p>${state.overviewLoading ? 'Refreshing...' : `Updated ${escapeHTML(dateText(state.overview.updated_at, true))}`}</p></div><span class="stage ${status.tone}">${escapeHTML(status.label)}</span></header>
    <div class="health-summary"><h2>Runtime checks</h2><p>${components.length} ${components.length === 1 ? 'component' : 'components'} reported</p></div>
    ${topFacts.length ? `<dl class="health-facts health-top-facts">${topFacts.map(([key, value]) => `<div><dt>${escapeHTML(humanize(key))}</dt><dd>${escapeHTML(valueText(value))}</dd></div>`).join('')}</dl>` : ''}
    ${components.length ? `<ul class="health-list">${components.map(([name, component]) => {
      const record = component && typeof component === 'object' ? component : { value: component };
      const componentStatus = healthStatus(record);
      const facts = Object.entries(record).filter(([key]) => !['healthy', 'status', 'name', 'component'].includes(key));
      return `<li class="health-row"><div class="health-row-heading"><h2>${escapeHTML(humanize(name))}</h2><span class="stage ${componentStatus.tone}">${escapeHTML(componentStatus.label)}</span></div><dl class="health-facts">${facts.length ? facts.map(([key, value]) => `<div><dt>${escapeHTML(humanize(key))}</dt><dd>${escapeHTML(valueText(value))}</dd></div>`).join('') : '<div><dt>Details</dt><dd>Not reported</dd></div>'}</dl></li>`;
    }).join('')}</ul>` : emptyMarkup('No health checks reported')}
  </section>`;
}

function selectTab(tab, focus = false) {
  if (!tabs.includes(tab)) return;
  state.tab = tab;
  state.query = '';
  state.mode = 'all';
  state.selectedId = null;
  state.detail = null;
  state.detailLoading = false;
  state.detailRequest += 1;
  renderChrome();
  renderView();
  if (focus) $(`#tab-${tab}`).focus();
}

async function refreshAll() {
  if (state.refreshing || state.mutation) return;
  const epoch = state.epoch;
  state.refreshing = true;
  updateBusyUI();
  try {
    const session = await api('/api/session');
    if (epoch !== state.epoch) return;
    if (!session?.authenticated) { showPairing('', session?.csrf_token); return; }
    state.session = session;
    await loadOverview({ quiet: true });
    if (state.selectedId) await loadDetail();
    announce('Workspace refreshed.');
  } catch (error) {
    if (epoch === state.epoch) setNotice(error.message, 'error', error.payload);
  } finally {
    if (epoch === state.epoch) { state.refreshing = false; renderChrome(); }
  }
}

$('#pair-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const code = $('#pair-code').value.trim();
  if (!code || $('#pair-submit').disabled) return;
  $('#pair-error').hidden = true;
  $('#pair-submit').disabled = true;
  $('#pair-code').disabled = true;
  $('#pair-submit').textContent = 'Pairing...';
  try {
    await api('/api/pair', { method: 'POST', body: { code }, pairing: true });
    $('#pair-form').reset();
    const session = await api('/api/session', { pairing: true });
    if (!session?.authenticated) throw new Error('The browser could not be paired. Request a new code and try again.');
    state.epoch += 1;
    showWorkspace(session);
    await loadOverview();
    await openRequestedApplication();
    $('#main-content').focus();
  } catch (error) {
    $('#pair-error').textContent = error.message;
    $('#pair-error').hidden = false;
    $('#pair-code').value = '';
  } finally {
    $('#pair-submit').disabled = false;
    $('#pair-code').disabled = false;
    $('#pair-submit').textContent = 'Pair browser';
    if (!$('#pair-screen').hidden) $('#pair-code').focus();
  }
});

$('#logout-button').addEventListener('click', async () => {
  if (state.mutation || state.refreshing) return;
  state.refreshing = true;
  updateBusyUI();
  try {
    await api('/api/logout', { method: 'POST', body: {} });
    showPairing();
  } catch (error) {
    if (state.session) setNotice(error.message, 'error', error.payload);
  } finally {
    state.refreshing = false;
    if (state.session) updateBusyUI();
  }
});

$('#refresh-button').addEventListener('click', refreshAll);
for (const button of $$('.tab')) {
  button.addEventListener('click', () => selectTab(button.dataset.tab));
  button.addEventListener('keydown', (event) => {
    const visibleTabs = tabs.filter((tab) => !$(`#tab-${tab}`).hidden);
    const index = visibleTabs.indexOf(button.dataset.tab);
    let next;
    if (event.key === 'ArrowRight') next = visibleTabs[(index + 1) % visibleTabs.length];
    if (event.key === 'ArrowLeft') next = visibleTabs[(index + visibleTabs.length - 1) % visibleTabs.length];
    if (event.key === 'Home') next = visibleTabs[0];
    if (event.key === 'End') next = visibleTabs[visibleTabs.length - 1];
    if (next) { event.preventDefault(); selectTab(next, true); }
  });
}

window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && state.selectedId && !desktop.matches && !state.mutation && !/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) closeDetail();
});

// A restored page must not retain a previous session's private review in the DOM.
window.addEventListener('pageshow', (event) => { if (event.persisted) window.location.reload(); });

function detailEditorFocused() {
  return document.activeElement?.matches('#detail-pane input, #detail-pane textarea, #detail-pane select');
}

function refreshPendingWork() {
  if (!state.session || state.mutation || state.refreshing || state.overviewLoading || state.detailLoading) return;
  if (detailEditorFocused()) return;
  // Finish watching a selected task even when its tab is in the background.
  // Inactive queues still skip overview polling.
  if (document.hidden && !state.selectedId) return;
  const pending = state.selectedId
    ? activeDeliveryStages.has(normalizedStage(state.detail?.item)) || state.queued.has(state.selectedId) || state.detail?.form_check_pending
    : items().some((item) => activeDeliveryStages.has(normalizedStage(item)));
  if (pending && state.selectedId) void loadDetail({ quiet: true });
  else if (pending) void loadOverview({ quiet: true });
  else if (!document.hidden && state.overview?.ready_to_apply && Date.now() - lastQuietOverview >= 30000) {
    lastQuietOverview = Date.now();
    void loadOverview({ quiet: true });
  }
}
let lastQuietOverview = Date.now();
window.setInterval(refreshPendingWork, 2500);
window.addEventListener('focus', refreshPendingWork);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshPendingWork(); });
document.addEventListener('focusout', (event) => {
  if (event.target.matches('#detail-pane input, #detail-pane textarea, #detail-pane select')) {
    window.setTimeout(refreshPendingWork, 0);
  }
});
void start();
