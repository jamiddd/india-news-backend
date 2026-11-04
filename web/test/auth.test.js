import { describe, expect, it } from "vitest";
import * as auth from "../src/auth.js";

describe("auth", () => {
  it("is configured with the project's API key", () => {
    expect(auth.configured()).toBe(true);
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
});
