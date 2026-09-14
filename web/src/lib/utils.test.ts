import { describe, expect, it } from "vitest"

import { cn } from "./utils"

describe("foundation class composition", () => {
  it("resolves conflicting Tailwind utility classes deterministically", () => {
    expect(cn("px-2", "px-4")).toBe("px-4")
  })
})
