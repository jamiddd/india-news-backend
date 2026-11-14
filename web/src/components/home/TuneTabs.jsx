import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { sourceList, titleCase } from "../../api.js";
import { Fav } from "../Photo.jsx";
import { POPULAR, isUserKey, stripEmoji, writeTabs } from "../../feedTabs.js";
import { slug } from "./shared.jsx";

// Customize Feed (web): add a publisher or your own topic as a tab, turn built-in categories on or
// off, and put them in the order you like. Changes apply to the strip straight away.
export default function TuneTabs({ view, onChange, onClose }) {
  const [sources, setSources] = useState(null), [text, setText] = useState(""), [cat, setCat] = useState(""), [menu, setMenu] = useState(false), menuRef = useRef(null);
  useEffect(() => { let on = true; sourceList().then((l) => on && setSources(l || [])); return () => { on = false; }; }, []);
  useEffect(() => {
    const key = (e) => { if (e.key === "Escape") { if (menuRef.current) setMenu(false); else onClose(); } };
    const click = (e) => { if (menuRef.current && !menuRef.current.contains(e.target)) setMenu(false); };
    document.addEventListener("keydown", key); document.addEventListener("click", click);
    return () => { document.removeEventListener("keydown", key); document.removeEventListener("click", click); };
  }, [onClose]);

  // Drag a row by its handle (mouse, touch or pen): the row follows the pointer and the list reorders live;
  // arrow keys on the handle do the same for keyboard users. Saved when the pointer is released.
  const [live, setLive] = useState(null), rows = useRef({}), dragKey = useRef(null);
  const shown = live || view.shown;
  const commit = (seq) => {
    let n = 0;
    const order = view.order.map((k) => (view.off.indexOf(k) > -1 ? k : seq[n++]));
    save({ order, off: view.off.slice(), user: view.user.slice() });
  };
  const dragStart = (e, k) => {
    if (e.button > 0) return;
    e.currentTarget.setPointerCapture(e.pointerId); dragKey.current = k; setLive(view.shown.slice());
  };
  const dragMove = (e) => {
    const k = dragKey.current;
    if (!k) return;
    setLive((cur) => {
      const seq = cur.slice(), from = seq.indexOf(k);
      let to = from;
      seq.forEach((x, i) => { const r = rows.current[x] && rows.current[x].getBoundingClientRect(); if (r && e.clientY >= r.top && e.clientY <= r.bottom && e.clientX >= r.left && e.clientX <= r.right) to = i; });
      if (to === from) return cur;
      seq.splice(from, 1); seq.splice(to, 0, k); return seq;
    });
  };
  const dragEnd = () => {
    if (!dragKey.current) return;
    dragKey.current = null;
    if (live) commit(live);
    setLive(null);
  };
  const nudge = (e, k) => {
    const d = e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0, i = view.shown.indexOf(k);
    if (!d || i + d < 0 || i + d >= view.shown.length) return;
    e.preventDefault();
    const seq = view.shown.slice(); seq.splice(i, 1); seq.splice(i + d, 0, k); commit(seq);
  };

  const save = (p) => { writeTabs(p); onChange(p); };
  const cur = () => ({ order: view.order.slice(), off: view.off.slice(), user: view.user.slice() });
  const remove = (k) => {
    const p = cur();
    if (isUserKey(k)) { p.user = p.user.filter((u) => u.key !== k); p.order = p.order.filter((x) => x !== k); }
    else p.off.push(k);
    save(p);
  };
  const restore = (k) => { const p = cur(); p.off = p.off.filter((x) => x !== k); save(p); };
  const addUser = (key, label) => {
    const p = cur();
    if (p.user.some((u) => u.key === key)) return;
    p.user.push({ key, label }); p.order.push(key); save(p);
  };
  const toggleSource = (s) => {
    const key = "src:" + slug(s.name);
    if (view.order.indexOf(key) > -1) remove(key); else addUser(key, s.name);
  };
  const topic = stripEmoji(text), needle = topic.toLowerCase();
  const addTopic = (e) => {
    e.preventDefault();
    if (topic.length < 2) return;
    addUser("q:" + needle, topic);
    setText("");
  };

  const byName = {};
  (sources || []).filter((s) => !s.status || s.status === "active").forEach((s) => { byName[s.name] = s; });
  const cats = [...new Set(Object.keys(byName).map((n) => titleCase(byName[n].category || "")).filter(Boolean))].sort();
  const inCat = (s) => !cat || titleCase(s.category || "") === cat;
  const found = needle.length < 2 ? [] : Object.keys(byName).filter((n) => n.toLowerCase().indexOf(needle) > -1).map((n) => byName[n]).filter(inCat).slice(0, 12);
  const browse = cat ? Object.keys(byName).map((n) => byName[n]).filter(inCat) : null;
  const chip = (s) => { const on = view.order.indexOf("src:" + slug(s.name)) > -1; return <button type="button" key={s.id || s.name} className={on ? "on" : ""} aria-pressed={on} onClick={() => toggleSource(s)}><Fav name={s.name} />{s.name}{on ? " ✓" : ""}</button>; };
  let popular = POPULAR.map((n) => byName[n]).filter(Boolean);
  if (popular.length < 4) popular = Object.keys(byName).slice(0, 12).map((n) => byName[n]);

  return createPortal(
    <div className="tune-scrim" onClick={onClose}>
      <div className="tune-sheet" role="dialog" aria-modal="true" aria-label="Customize feed" onClick={(e) => e.stopPropagation()}>
        <div className="tune-top"><button type="button" className="tune-back" aria-label="Back" onClick={onClose}>{"←"}</button><h2>Customize feed</h2><button type="button" className="tune-x" aria-label="Close" onClick={onClose}>{"×"}</button></div>
        <p className="tune-blurb">Drag tabs to reorder {"·"} tap {"×"} to remove</p>
        <div className="tune-cols"><div className="tune-col">
        <h3>Your tabs</h3>
        <ul className="tune-list">
          <li className="fixed"><span>All</span><em>always first</em></li>
          {shown.map((k) => (
            <li key={k} ref={(el) => { rows.current[k] = el; }} className={dragKey.current === k ? "drag" : ""}>
              <button type="button" className="grip" aria-label={"Reorder " + view.label[k] + " (drag, or use the arrow keys)"} onPointerDown={(e) => dragStart(e, k)} onPointerMove={dragMove} onPointerUp={dragEnd} onPointerCancel={dragEnd} onKeyDown={(e) => nudge(e, k)}>{"⠿"}</button>
              <span>{view.label[k]}{k.indexOf("src:") === 0 ? <em>publisher</em> : k.indexOf("q:") === 0 ? <em>topic</em> : null}</span>
              <button type="button" aria-label={"Remove " + view.label[k]} onClick={() => remove(k)}>{"×"}</button>
            </li>
          ))}
        </ul>

        </div><div className="tune-col">
        <div className="tune-s3"><form className="tune-search" onSubmit={addTopic} role="search">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
          <input type="search" autoFocus={!matchMedia("(max-width:700px)").matches} value={text} onChange={(e) => setText(e.target.value)} placeholder="Search publishers, or type any topic to add it" aria-label="Search publishers or add a topic" maxLength={60} />
          {cats.length > 1 ? (
            <span className="tune-filter" ref={menuRef}>
              <button type="button" className={"tune-filter-btn" + (cat ? " on" : "")} aria-haspopup="listbox" aria-expanded={menu} aria-label={"Filter publishers by category" + (cat ? ": " + cat : "")} onClick={() => setMenu((m) => !m)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 5h18l-7 8v6l-4-2v-4z" /></svg>{cat || "Filter"}
              </button>
              {menu ? (
                <ul className="tune-menu" role="listbox" aria-label="Categories">
                  {[""].concat(cats).map((k) => <li key={k || "all"} role="option" aria-selected={cat === k}><button type="button" className={cat === k ? "on" : ""} onClick={() => { setCat(k); setMenu(false); }}>{k || "All categories"}</button></li>)}
                </ul>
              ) : null}
            </span>
          ) : null}
        </form>
        {topic.length >= 2 ? (
          <div className="tune-results">
            {found.length ? <div className="tune-chips">{found.map(chip)}</div> : null}
            <button type="button" className="tune-topic" onClick={addTopic}>+ Add {"“"}{topic}{"”"} as a topic tab</button>
          </div>
        ) : null}</div>
        <section className="tune-s1"><h3 className="tune-after">Not showing</h3>
        <p className="tune-blurb">Tap to put one back on the strip.</p>
        <div className="tune-chips">
          {view.hidden.length ? view.hidden.map((k) => <button type="button" key={k} onClick={() => restore(k)}>+ {view.label[k]}</button>) : <span className="tune-none">Every category is on your strip.</span>}
        </div>

        </section><section className="tune-s2"><h3>{cat ? cat + " sources" : "Popular sources"} <Link to="/sources" onClick={onClose}>See all</Link></h3>
        <p className="tune-blurb">Follow a publisher and it becomes its own tab, with only their stories.</p>
        <div className="tune-chips">
          {sources === null ? <span className="tune-none">Loading publishers{"…"}</span>
            : (browse || popular).length ? (browse || popular).map(chip)
              : <span className="tune-none">Couldn{"’"}t load publishers.</span>}
        </div>

        </section></div></div>
        <div className="tune-actions"><button type="button" onClick={() => save(null)}>Reset to default</button><button type="button" className="done" onClick={onClose}>Done</button></div>
      </div>
    </div>, document.body);
}
