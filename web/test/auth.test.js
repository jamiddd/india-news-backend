import { describe, expect, it } from "vitest";
import * as auth from "../src/auth.js";

describe("auth", () => {
  it("is not configured until the Firebase web app config is filled in", () => {
    expect(auth.configured()).toBe(false);
  });
  it("refuses to sign in while unconfigured, with a message people can read", async () => {
    await expect(auth.signInWithGoogle()).rejects.toMatchObject({ name: "AuthError", message: expect.stringContaining("isn't available") });
    await expect(auth.signInWithEmail("a@b.co", "secret1")).rejects.toMatchObject({ name: "AuthError" });
  });
  it("maps Firebase error codes to messages and form fields", () => {
    expect(auth.explain({ code: "auth/invalid-credential" })).toMatchObject({ message: "That email and password don't match.", field: "pw" });
    expect(auth.explain({ code: "auth/email-already-in-use" })).toMatchObject({ field: "email" });
    expect(auth.explain({ code: "auth/weak-password" })).toMatchObject({ message: "Use at least 6 characters.", field: "pw" });
    expect(auth.explain({ code: "auth/too-many-requests" }).field).toBeNull();
    expect(auth.explain({ code: "auth/something-new" }).message).toBe("Something went wrong. Try again.");
  });
  it("treats a closed Google window as a cancellation, not an error", () => {
    expect(auth.cancelled({ code: "auth/popup-closed-by-user" })).toBe(true);
    expect(auth.cancelled({ code: "auth/network-request-failed" })).toBe(false);
  });
  it("has no signed-in user and no token while unconfigured", async () => {
    expect(await auth.restoredUser()).toBeNull();
    expect(await auth.idToken()).toBeNull();
  });
});
