/* Shared account widget: fills header .right with the signed-in state and
   exposes vnMe() for pages to gate features on. */
let _me;
async function vnMe(force) {
  if (_me === undefined || force) {
    try {
      _me = (await (await fetch('/api/auth/me')).json()).user;
    } catch { _me = null; }
  }
  return _me;
}
function vnEsc(s) { const d = document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; }

async function vnHeader() {
  const slot = document.querySelector('header.site .right');
  if (!slot) return;
  const u = await vnMe();
  if (u) {
    slot.innerHTML = `${slot.innerHTML ? slot.innerHTML + ' · ' : ''}` +
      `<a href="/user?u=${encodeURIComponent(u.username)}">⛵ ${vnEsc(u.display_name)}</a>` +
      (u.is_admin ? ' <span class="badge real">admin</span>' : '') +
      ` · <a href="#" id="vn_logout">sign out</a>`;
    document.getElementById('vn_logout').onclick = async (ev) => {
      ev.preventDefault();
      await fetch('/api/auth/logout', { method: 'POST' });
      location.reload();
    };
  } else {
    slot.innerHTML = `${slot.innerHTML ? slot.innerHTML + ' · ' : ''}` +
      `<a href="/#account">sign in</a>`;
  }
}
vnHeader();
