import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Button from "../components/ui/button";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }) => <a href={href} data-next-link="true" {...props}>{children}</a>,
}));

describe("Button file downloads", () => {
  it("uses a native browser anchor instead of Next client navigation", () => {
    render(<Button href="/api/browser-extension/download" download="posterpro-extension.zip">Download extension</Button>);
    const link = screen.getByRole("link", { name: "Download extension" });
    expect(link).toHaveAttribute("href", "/api/browser-extension/download");
    expect(link).toHaveAttribute("download", "posterpro-extension.zip");
    expect(link).not.toHaveAttribute("data-next-link");
  });
});
