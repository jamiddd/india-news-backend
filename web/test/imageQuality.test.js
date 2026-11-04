import { describe, expect, it } from "vitest";
import { bestFirst, hintedWidth, tier } from "../src/imageQuality.js";
import * as api from "../src/api.js";

// Real URL shapes from the cover story (306196).
const GUARDIAN_THUMB = "https://i.guim.co.uk/img/media/a8a2468cf86e121bf772fd951dc3411eeb18d984/0_0_3000_2400/master/3000.jpg?width=140&quality=85&auto=format&fit=max&s=838527c68cb5b671eed108abccce71d1";
const ALJAZEERA = "https://www.aljazeera.com/wp-content/uploads/2026/11/afp_6ae906978461-1793656471.jpg?resize=1920%2C1440";
const TIMESNOW = "https://images.timesnownews.com/thumb/msid-156460348,thumbsize-1356754,width-1280,height-720,resizemode-75/156460348.jpg";
const HT = "https://www.hindustantimes.com/ht-img/img/2026/11/04/1600x900/logo/USA-ELECTION-NEW-1.jpg";
const NDTV = "https://c.ndtvimg.com/2026-11/4t74kmag_jon-ossoff_625x300_04_November_26.jpeg";
const BBC = "https://ichef.bbci.co.uk/ace/standard/240/cpsprodpb/2e3c/live/a1b3d4e0.jpg";
const INDIATV = "https://resize.indiatvnews.com/en/resize/newbucket/1200_-/2026/11/james-talarico.jpg";
const PLAIN = "https://images.news18.com/ibnlive/uploads/2026/11/Pappas-New-2026-11.jpg";

describe("hintedWidth", () => {
  it("reads the width a URL states", () => {
    expect(hintedWidth(GUARDIAN_THUMB)).toBe(140);          // not the 3000 of the source file in the path
    expect(hintedWidth(ALJAZEERA)).toBe(1920);
    expect(hintedWidth(TIMESNOW)).toBe(1280);                // not the 1356754 byte count in thumbsize-
    expect(hintedWidth(HT)).toBe(1600);
    expect(hintedWidth(NDTV)).toBe(625);
    expect(hintedWidth(BBC)).toBe(240);
    expect(hintedWidth(INDIATV)).toBe(1200);
  });
  it("says nothing when the URL says nothing", () => {
    expect(hintedWidth(PLAIN)).toBeNull();
    expect(hintedWidth("")).toBeNull();
    expect(hintedWidth(null)).toBeNull();
  });
});

describe("tier and bestFirst", () => {
  it("grades high above unknown above medium above low", () => {
    expect([ALJAZEERA, PLAIN, NDTV, GUARDIAN_THUMB].map(tier)).toEqual([3, 2, 1, 0]);
  });
  it("puts the sharp pictures first and keeps the order inside a group", () => {
    const sorted = bestFirst([GUARDIAN_THUMB, BBC, PLAIN, NDTV, ALJAZEERA, TIMESNOW]);
    expect(sorted).toEqual([ALJAZEERA, TIMESNOW, PLAIN, NDTV, GUARDIAN_THUMB, BBC]);
  });
});

describe("story media uses it", () => {
  const art = (id, image_url, extra = {}) => ({ id, source_id: id, source_name: "S" + id, title: "t", published_at: "2026-11-04T00:00:00Z", image_url, video_url: null, has_pending_video: false, ...extra });

  it("leads the carousel with the sharpest image, not the first article's thumbnail", () => {
    const m = api.adaptMedia([art(1, GUARDIAN_THUMB), art(2, ALJAZEERA), art(3, PLAIN)]);
    expect(m.map((x) => x.src)).toEqual([ALJAZEERA, PLAIN, GUARDIAN_THUMB]);
  });

  it("keeps a video at the end and still leaves room for it", () => {
    const arts = [art(1, GUARDIAN_THUMB), ...Array.from({ length: 9 }, (_, i) => art(i + 2, "https://img/" + i + ".jpg")), art(99, "https://img/v.jpg", { has_pending_video: true })];
    const m = api.adaptMedia(arts);
    expect(m).toHaveLength(8);
    expect(m[m.length - 1].kind).toBe("video");
    expect(m[0].src).not.toBe(GUARDIAN_THUMB);
  });

  it("uses the sharpest image as the story's card image", () => {
    const c = { id: 1, headline: "h", article_count: 3, articles: [art(1, GUARDIAN_THUMB), art(2, HT), art(3, PLAIN)] };
    expect(api.adaptCluster(c, { sources: api.indexSources([]), now: Date.now() }).image).toBe(HT);
  });
});
