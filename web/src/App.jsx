import { Routes, Route } from "react-router-dom";
import Layout from "./Layout.jsx";
import About from "./pages/About.jsx";
import Contact from "./pages/Contact.jsx";
import Context from "./pages/Context.jsx";
import Credits from "./pages/Credits.jsx";
import DeleteAccount from "./pages/DeleteAccount.jsx";
import Donate from "./pages/Donate.jsx";
import DonationThanks from "./pages/DonationThanks.jsx";
import Download from "./pages/Download.jsx";
import Explainer from "./pages/Explainer.jsx";
import Explainers from "./pages/Explainers.jsx";
import Feedback from "./pages/Feedback.jsx";
import Home from "./pages/Home.jsx";
import List from "./pages/List.jsx";
import M from "./pages/M.jsx";
import Pricing from "./pages/Pricing.jsx";
import Privacy from "./pages/Privacy.jsx";
import Profile from "./pages/Profile.jsx";
import Refunds from "./pages/Refunds.jsx";
import Saved from "./pages/Saved.jsx";
import Settings from "./pages/Settings.jsx";
import Sources from "./pages/Sources.jsx";
import Story from "./pages/Story.jsx";
import Terms from "./pages/Terms.jsx";
import Timeline from "./pages/Timeline.jsx";
import Timelines from "./pages/Timelines.jsx";
import Watch from "./pages/Watch.jsx";

// Clean URLs as mapped by prototype/server.py.
export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/story/:id" element={<Story />} />
        <Route path="/source/:id" element={<List />} />
        <Route path="/topic/:id" element={<List />} />
        <Route path="/timeline/:id" element={<Timeline />} />
        <Route path="/explainer/:id" element={<Explainer />} />
        {/* Share URLs the server renders (app/main.py): plural forms and legacy paths. */}
        <Route path="/timelines/:id" element={<Timeline />} />
        <Route path="/explainers/:id" element={<Explainer />} />
        <Route path="/donations/thanks" element={<DonationThanks />} />
        <Route path="/account-deletion" element={<DeleteAccount />} />
        <Route path="/about" element={<About />} />
        <Route path="/contact" element={<Contact />} />
        <Route path="/context" element={<Context />} />
        <Route path="/credits" element={<Credits />} />
        <Route path="/delete-account" element={<DeleteAccount />} />
        <Route path="/donate" element={<Donate />} />
        <Route path="/donation-thanks" element={<DonationThanks />} />
        <Route path="/download" element={<Download />} />
        <Route path="/explainer" element={<Explainer />} />
        <Route path="/explainers" element={<Explainers />} />
        <Route path="/feedback" element={<Feedback />} />
        <Route path="/list" element={<List />} />
        <Route path="/m" element={<M />} />
        <Route path="/pricing" element={<Pricing />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/refunds" element={<Refunds />} />
        <Route path="/saved" element={<Saved />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/sources" element={<Sources />} />
        <Route path="/story" element={<Story />} />
        <Route path="/terms" element={<Terms />} />
        <Route path="/timeline" element={<Timeline />} />
        <Route path="/timelines" element={<Timelines />} />
        <Route path="/watch" element={<Watch />} />
        <Route path="*" element={<main className="wide"><h1>Not found</h1></main>} />
      </Route>
    </Routes>
  );
}
