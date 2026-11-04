import { useEffect } from "react";
import { shareStory } from "./toast.js";

// Document-level save/share for markup that is not a React component: any element with
// data-save="<id>" or data-share="<id>" (prototype shell.js behaviour). The React Acts /
// SrcRow buttons call the same store and shareStory directly.
export default function GlobalActions({ toggleSaved }) {
  useEffect(() => {
    const click = (e) => {
      const sv = e.target.closest("[data-save]"), sh = e.target.closest("[data-share]");
      if (sv) { e.preventDefault(); toggleSaved(sv.dataset.save); }
      else if (sh) { e.preventDefault(); shareStory(sh.dataset.share); }
    };
    document.addEventListener("click", click);
    return () => document.removeEventListener("click", click);
  }, [toggleSaved]);
  return null;
}
