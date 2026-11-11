import { describe, expect, it } from "vitest";
import { chunkBlocks, planFront } from "../src/components/home/frontPlan.js";

const st = (id, category, extra = {}) => ({ id, category, headline: "H" + id, image: "i" + id, outlet_count: 1, minutes_ago: id, ...extra });

describe("planFront", () => {
  const S = [st(1, "India", { outlet_count: 9 }), st(2, "Business"), st(3, "Business"), st(4, "Business"), st(5, "Business"), st(6, "World", { video: { duration: 90 } }),
    st(7, "World"), st(8, "World"), st(9, "World"), st(10, "Sports"), st(11, "Sports"), st(12, "Tech")];

  it("uses every story at most once", () => {
    const p = planFront(S);
    const ids = [p.lead, p.a, p.video, p.b, p.row5[0], p.row5[1], p.p1 && p.p1.head, p.p2 && p.p2.head]
      .concat(p.p1 ? p.p1.links : [], p.p2 ? p.p2.links : [], p.relLead, p.relA, p.relB).filter(Boolean).map((x) => x.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("leads with the first story and flags the most covered one", () => {
    const p = planFront(S);
    expect(p.lead.id).toBe(1);
    expect(p.topCovered.id).toBe(1);
  });

  it("puts a video story in the video cell", () => {
    expect(planFront(S).video.video).toBeTruthy();
  });

  it("fills the video cell with an ordinary story when no story has a video", () => {
    const p = planFront(S.map((x) => ({ ...x, video: undefined })));
    expect(p.video).toBeNull();
    expect(p.vfill).toBeTruthy();
  });

  it("builds two packages from different categories", () => {
    const p = planFront(S);
    expect(p.p1 && p.p2).toBeTruthy();
    expect(p.p1.name).not.toBe(p.p2.name);
  });

  it("copes with a tiny feed", () => {
    const p = planFront([st(1, "India")]);
    expect(p.lead.id).toBe(1);
    expect(p.p1).toBeNull();
  });
});

describe("chunkBlocks", () => {
  const list = (n) => Array.from({ length: n }, (_, i) => ({ id: i + 1 }));
  it("keeps every story exactly once, in order", () => {
    for (const n of [1, 2, 3, 5, 12, 31, 55]) {
      const ids = chunkBlocks(list(n)).flatMap((b) => b.L.map((x) => x.id));
      expect(ids).toEqual(list(n).map((x) => x.id));
    }
  });
  it("cycles block positions from the start index", () => {
    expect(chunkBlocks(list(40), 2).map((b) => b.at).slice(0, 3)).toEqual([2, 3, 4]);
  });
  it("is empty for no stories", () => { expect(chunkBlocks([])).toEqual([]); });
});
