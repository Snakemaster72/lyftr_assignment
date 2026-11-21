import { describe, expect, it } from "vitest";
import { describeError, formatDetail } from "./api";

describe("formatDetail", () => {
  it("passes string details through", () => {
    expect(formatDetail("session not found")).toBe("session not found");
  });

  it("formats FastAPI validation lists into readable text", () => {
    const detail = [
      { loc: ["body", "message"], msg: "Value error, message must not be empty" },
      { loc: ["body", "session_id"], msg: "Field required" },
    ];
    expect(formatDetail(detail)).toBe("message: message must not be empty; session_id: Field required");
  });

  it("handles unexpected shapes", () => {
    expect(formatDetail({ weird: true })).toBe("unexpected error");
  });
});

describe("describeError", () => {
  it("explains 422 and 503", () => {
    expect(describeError(422, "bad header")).toBe("Invalid request: bad header");
    expect(describeError(503, "all providers unavailable")).toMatch(/All providers are unavailable/);
  });
});
