import { Link } from "react-router-dom";
import { brand, footer } from "../brand.js";

// The site footer's links, moved here for phones (the footer is hidden there; the Profile tab
// is the "more" place). Hidden on wider screens, where the footer shows them.
export default function AboutLinks() {
  return (
    <section className="pr-sec pr-about" aria-labelledby="pr-about-h">
      <h2 id="pr-about-h">About</h2>
      <div className="pr-list">
        {footer.map(([label, href]) => href.startsWith("/")
          ? <Link key={label} to={href}>{label}<i aria-hidden="true">&rsaquo;</i></Link>
          : <a key={label} href={href}>{label}<i aria-hidden="true">&rsaquo;</i></a>)}
      </div>
      <p className="pr-copy">&copy; {new Date().getFullYear()} {brand.name}</p>
    </section>
  );
}
