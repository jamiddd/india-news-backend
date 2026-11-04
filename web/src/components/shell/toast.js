export function toast(msg, ms = 2600) {
  const t = document.createElement("div");
  t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, ms);
}

export function load(k, def) { try { return JSON.parse(localStorage.getItem(k)) || def; } catch (e) { return def; } }
export function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

// Native share sheet when the browser has one, otherwise copy the link.
export function shareStory(id, title) {
  const url = location.origin + "/story/" + id;
  if (navigator.share) { navigator.share({ title: title || "Open Indian News", url }).catch(() => {}); return; }
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(() => toast("Link copied"), () => toast(url));
  else toast(url);
}
