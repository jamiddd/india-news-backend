import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

// Phone-size frame around another page: /m?u=/&w=390&h=844
export default function M() {
  const [q] = useSearchParams();
  useEffect(() => {
    const prev = document.body.style.background;
    document.body.style.background = "#444";
    return () => { document.body.style.background = prev; };
  }, []);
  return <iframe title="frame" style={{ border: 0, display: "block", margin: "0 auto" }} width={q.get("w") || 390} height={q.get("h") || 844} src={q.get("u") || "/"}></iframe>;
}
