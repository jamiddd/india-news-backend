import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./site.css";
import App from "./App.jsx";

createRoot(document.getElementById("root")).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>
);

// Service worker: makes the site installable as an app (Chrome's "Install app") and gives a
// friendly offline page. Production only: the dev server doesn't serve /sw.js.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
    import("./push.js").then((m) => m.syncPush()).catch(() => {});   // a reader with push on stays registered
  });
}
