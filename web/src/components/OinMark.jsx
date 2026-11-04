// The o-i-n mark (same paths as store/assets/brand/oin-mark*.svg and the Android OinMark).
// Colours follow the site theme through --oin-body / --oin-i (see site.css).
// tone="dark": for surfaces that are dark in both themes (the desktop header) - white + light blue.
export default function OinMark({ size = 32, className = "", title, tone }) {
  const w = (size * 81.389) / 67;
  return (
    <svg className={"oin-mark " + (tone === "dark" ? "oin-on-dark " : "") + className} width={w} height={size} viewBox="0 0 81.389 67" role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : "true"}>
      <path fill="var(--oin-body)" d="M49.331,47.743A25,25 0 1 1 42.599,24.244L34.151,32.767A13,13 0 1 0 37.652,44.986ZM42.42,24.069L69.389,50.269L69.389,17L81.389,17L81.389,67L69.389,67L34.059,32.676Z" />
      <path fill="var(--oin-i)" d="M38,17L50,17L50,42L38,42ZM50,42A25,25 0 0 1 49.148,48.47L37.557,45.365A13,13 0 0 0 38,42Z" />
      <path fill="var(--oin-i)" d="M38,6A6,6 0 1 0 50,6A6,6 0 1 0 38,6Z" />
    </svg>
  );
}
