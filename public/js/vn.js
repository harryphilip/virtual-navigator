/* Shared client helpers, loaded by every page before its own script:
   the account widget in the header, vnMe() to gate features on, one time
   format, HTML escaping, a fetch wrapper that raises the server's message,
   and a countdown. Nothing here touches page-specific state. */
let _me;
async function vnMe(force) {
  if (_me === undefined || force) {
    try {
      _me = (await (await fetch('/api/auth/me')).json()).user;
    } catch { _me = null; }
  }
  return _me;
}
const VN_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/* one time format everywhere: "04 Sep 13:00Z" */
function vnTime(t, withYear) {
  const d = new Date(t * 1000), p = n => String(n).padStart(2, '0');
  return `${p(d.getUTCDate())} ${VN_MONTHS[d.getUTCMonth()]}${withYear ? ' ' + d.getUTCFullYear() : ''} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}Z`;
}
function vnEsc(s) { const d = document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; }
const esc = vnEsc;

/* fetch JSON; on a non-2xx answer throw the server's own message */
async function api(path, opts) {
  const res = await fetch(path, opts);
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(j.error || res.statusText);
  return j;
}

/* "in 2 d 4 h" until a unix time, or "started" */
function until(ts) {
  const s = ts - Date.now() / 1000;
  if (s <= 0) return 'started';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `in ${d} d ${h} h` : h ? `in ${h} h ${m} min` : `in ${m} min`;
}

async function vnHeader() {
  const slot = document.querySelector('header.site .right');
  if (!slot) return;
  // remember what the page put in the slot so a re-render after sign-in
  // (no reload) starts from the same base, not from the previous render
  if (slot.dataset.base === undefined) slot.dataset.base = slot.innerHTML;
  const base = slot.dataset.base;
  const u = await vnMe();
  if (u) {
    slot.innerHTML = `${base ? base + ' · ' : ''}` +
      `<a href="/user?u=${encodeURIComponent(u.username)}">${vnEsc(u.display_name)}</a>` +
      (u.is_admin ? ' <span class="badge real">admin</span>' : '') +
      ` · <a href="#" id="vn_logout">Sign out</a>`;
    document.getElementById('vn_logout').onclick = async (ev) => {
      ev.preventDefault();
      await fetch('/api/auth/logout', { method: 'POST' });
      location.reload();
    };
  } else {
    slot.innerHTML = `${base ? base + ' · ' : ''}` +
      `<a href="/#account">Sign in</a>`;
  }
}
vnHeader();
