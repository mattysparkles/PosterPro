import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";

const router = { isReady: true, pathname: "/intake/timeline", query: {}, push: vi.fn(), replace: vi.fn() };
const fetchTimeline = vi.fn();
const createRetroactiveSlate = vi.fn();

vi.mock("next/router", () => ({ useRouter: () => router }));
vi.mock("../components/layout/AppShell", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../components/ui/page-header", () => ({ default: ({ title, children }) => <header><h1>{title}</h1>{children}</header> }));
vi.mock("../components/ui/section-panel", () => ({ default: ({ children }) => <section>{children}</section> }));
vi.mock("../components/ui/button", () => ({ default: (props) => <button {...props} /> }));
vi.mock("../components/ui/action-link", () => ({ default: ({ children, ...props }) => <a {...props}>{children}</a> }));
vi.mock("../components/ui/status-pill", () => ({ default: ({ children }) => <span>{children}</span> }));
vi.mock("../components/ui/select", () => ({ default: (props) => <select {...props} /> }));
vi.mock("../components/ui/checkbox", () => ({ default: ({ label, ...props }) => <label>{label}<input type="checkbox" {...props} /></label> }));
vi.mock("../lib/api", () => ({
  fetchIntakeTimeline: (...args) => fetchTimeline(...args),
  fetchTimelineGooglePhotos: vi.fn(async () => ({ items: [] })),
  runIntakeMonitor: vi.fn(), insertTimelineGooglePhoto: vi.fn(), uploadTimelinePhoto: vi.fn(),
  createRetroactiveSlate: (...args) => createRetroactiveSlate(...args), classifyTimelineAssets: vi.fn(), resetTimelineClassifications: vi.fn(),
  deleteTimelineAsset: vi.fn(), deleteTimelineAssets: vi.fn(), setTimelinePrimary: vi.fn(),
  toThumbnailImageUrl: (value) => value || "",
}));

import IntakeTimeline from "../pages/intake/timeline";

const entry = (id, group, label) => ({
  image_group_id: `group-${group}`,
  image_group_index: group,
  photo: { id, image_group_id: `group-${group}`, image_group_index: group, group_photo_label: label, original_filename: `${label}.jpg`, image_type: "PHOTO", captured_at: "2026-09-30T10:00:00Z" },
});
const response = (items, extra = {}) => ({ items, total: 2000, photo_count: 1900, slate_count: 100, offset: 0, jump_index: 0, ...extra });

describe("real intake timeline Jump To interaction", () => {
  beforeEach(() => {
    router.query = {};
    router.push.mockReset();
    router.replace.mockReset();
    fetchTimeline.mockReset();
    createRetroactiveSlate.mockReset();
    fetchTimeline.mockResolvedValue(response([entry("initial", 262, "1718C")]));
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    window.scrollTo = vi.fn();
    window.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
  });
  afterEach(() => cleanup());

  it("jumps to a group, then to a photo, scrolling and addressably updating the URL", async () => {
    const { container } = render(<IntakeTimeline />);
    await screen.findByText("1718C.jpg");

    fetchTimeline.mockResolvedValueOnce(response([entry("group-photo", 85, "670A")]));
    fireEvent.change(screen.getByPlaceholderText("85"), { target: { value: "85" } });
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect((await screen.findAllByText("Item Group 85")).length).toBeGreaterThan(0);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(
      { pathname: "/intake/timeline", query: { group: "85" } }, undefined, { shallow: true, scroll: false },
    ));
    await waitFor(() => expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled());

    fetchTimeline.mockResolvedValueOnce(response([entry("photo-669f", 85, "669F")]));
    fireEvent.change(screen.getByPlaceholderText("669F"), { target: { value: "669f" } });
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(await screen.findByText("669F.jpg")).toBeTruthy();
    await waitFor(() => expect(router.push).toHaveBeenLastCalledWith(
      { pathname: "/intake/timeline", query: { photo: "669F" } }, undefined, { shallow: true, scroll: false },
    ));
    expect(container.querySelector('[data-timeline-photo-label="669F"]')?.classList.contains("timeline-jump-highlight")).toBe(true);
  });

  it("reports a missing group instead of silently doing nothing", async () => {
    render(<IntakeTimeline />);
    await screen.findByText("1718C.jpg");
    fetchTimeline.mockResolvedValueOnce(response([], { jump_index: null }));
    fireEvent.change(screen.getByPlaceholderText("85"), { target: { value: "9999" } });
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Item Group 9999 was not found.");
  });

  it("inserts a Slate immediately between visual neighbors without opening a dialog", async () => {
    const newest = entry("newest", 2, "2A");
    const older = entry("older", 1, "1A");
    fetchTimeline.mockResolvedValue({ items: [newest, older], total: 2, photo_count: 2, slate_count: 0, offset: 0 });
    createRetroactiveSlate.mockResolvedValue({ slate: { id: 77, item_id: "SLATE-77", metadata_json: {} } });
    render(<IntakeTimeline />);
    await screen.findByText("2A.jpg");

    fireEvent.click(screen.getByRole("button", { name: "Insert Slate between photos" }));
    await waitFor(() => expect(createRetroactiveSlate).toHaveBeenCalledWith(expect.objectContaining({
      retroactive: true,
      after_photo_id: "older",
      before_photo_id: "newest",
      boundary_position: "head",
    })));
    expect(screen.queryByRole("dialog", { name: "Create full item Slate" })).toBeNull();
  });

  it("reports a direct insertion error without opening a dialog", async () => {
    const newest = entry("newest", 2, "2A");
    const older = entry("older", 1, "1A");
    fetchTimeline.mockResolvedValueOnce({ items: [newest, older], total: 2, photo_count: 2, slate_count: 0, offset: 0 });
    createRetroactiveSlate.mockRejectedValue(new Error("boundary failed"));
    render(<IntakeTimeline />);
    await screen.findByText("2A.jpg");
    fireEvent.click(screen.getByRole("button", { name: "Insert Slate between photos" }));
    expect(await screen.findByRole("status")).toHaveTextContent("boundary failed");
    expect(screen.queryByRole("dialog", { name: "Create full item Slate" })).toBeNull();
  });

  it("localizes the Inserting state to the clicked boundary", async () => {
    const photos = ["a", "b", "c", "d", "e"].map((id, index) => entry(id, 1, `${index + 1}A`));
    fetchTimeline.mockResolvedValue({ items: photos, total: photos.length, photo_count: photos.length, slate_count: 0, offset: 0 });
    let resolveInsert;
    createRetroactiveSlate.mockReturnValue(new Promise((resolve) => { resolveInsert = resolve; }));
    render(<IntakeTimeline />);
    await screen.findByText("1A.jpg");
    const buttons = screen.getAllByRole("button", { name: "Insert Slate between photos" });
    expect(buttons).toHaveLength(4);
    fireEvent.click(buttons[1]);
    await waitFor(() => expect(screen.getAllByText("Inserting…")).toHaveLength(1));
    expect(screen.getAllByText("+ Slate")).toHaveLength(3);
    fireEvent.click(buttons[1]);
    expect(createRetroactiveSlate).toHaveBeenCalledTimes(1);
    resolveInsert({ slate: { id: 99, item_id: "SLATE-99", metadata_json: {} } });
  });
});
