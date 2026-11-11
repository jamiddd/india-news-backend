import { describe, expect, it } from "vitest";
import { disablePush, enablePush, pushSupported, syncPush } from "../src/push.js";

// Node has no Notification, service worker or Firebase session, and the public key is not set yet:
// each entry point must quietly do nothing rather than throw.
describe("web push without browser support or a key", () => {
  it("reports unsupported", () => { expect(pushSupported()).toBe(false); });
  it("never registers", async () => {
    expect(await enablePush()).toBe(false);
    expect(await syncPush()).toBe(false);
  });
  it("turning it off with nothing registered is a no-op", async () => {
    await expect(disablePush()).resolves.toBeUndefined();
  });
});
