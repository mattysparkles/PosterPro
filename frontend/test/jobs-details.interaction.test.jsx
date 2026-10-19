import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import JobsPage from "../pages/jobs";
import {
  fetchMarketplaceJobsOverview,
  fetchCrosspostJob,
} from "../lib/api";

const mocks = vi.hoisted(() => ({
  router: { isReady: true, pathname: "/jobs", query: {}, replace: vi.fn().mockResolvedValue(true) },
  overview: {
    crosspost_jobs: [
      { id: 739, listing_id: 2229, listing_title: "Smart Caregiver Call Button", target_marketplaces: ["ebay"], status: "failed", operation: "CREATE", attempt_count: 1, created_at: "2026-09-30T12:00:00Z", updated_at: "2026-09-30T12:02:00Z", last_error: "Cross-post execution failed for: ebay" },
      { id: 741, listing_id: 2239, listing_title: "12V RV Wall Sconce", target_marketplaces: ["ebay"], status: "blocked", operation: "CREATE", attempt_count: 0, created_at: "2026-09-30T12:03:00Z", updated_at: "2026-09-30T12:03:00Z", last_error: "Category requires a verified Type aspect." },
      { id: 1043, listing_id: 2750, listing_title: "Cyberpunk Reflex Drop Sticks Challenge Game", target_marketplaces: ["ebay", "facebook", "mercari"], status: "awaiting_operator_review", operation: "CREATE", attempt_count: 1, created_at: "2026-10-03T09:48:40Z", updated_at: "2026-10-03T10:09:34Z", last_error: "Cross-post execution failed for: ebay" },
    ],
    import_jobs: [],
    correction_jobs: [],
    system_status: {},
    queue_control: { paused: false },
  },
  fetchDetails: vi.fn(),
}));

vi.mock("next/router", () => ({ useRouter: () => mocks.router }));
vi.mock("next/link", () => ({ default: ({ href, children, ...props }) => <a href={href} {...props}>{children}</a> }));
vi.mock("../components/layout/AppShell", () => ({ default: ({ children }) => <main>{children}</main> }));
vi.mock("../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: 2, can_access_vine_import: false } }) }));
vi.mock("../hooks/useDashboardData", () => ({ default: () => ({ autonomousConfig: {}, reload: vi.fn() }) }));
vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    fetchMarketplaceJobsOverview: vi.fn(async () => mocks.overview),
    fetchProcessingHealth: vi.fn(async () => ({})),
    fetchMediaRetentionQueue: vi.fn(async () => ({ items: [], total: 0 })),
    fetchAssistedMarketplaceJobs: vi.fn(async () => []),
    fetchCrosspostJob: mocks.fetchDetails,
  };
});

const detailRows = {
  739: { ...mocks.overview.crosspost_jobs[0], preflight_status: "ready", provider_error: { code: "EBAY_SELLER_RESTRICTED", message: "Seller account cannot create listings." }, execution_plan: { targets: ["ebay"] } },
  741: { ...mocks.overview.crosspost_jobs[1], preflight_status: "blocked", blocker_codes: ["REQUIRED_ASPECT"], blocker_messages: ["Category requires a verified Type aspect."], execution_plan: { targets: ["ebay"] } },
  1043: {
    ...mocks.overview.crosspost_jobs[2],
    source_listing_status: "PROCESSED",
    source_price: 29.99,
    destination_operations: [
      { marketplace: "ebay", status: "BLOCKED_EXTERNAL", operation: "CREATE", error_code: "EBAY_SELLER_RESTRICTED", error_detail: "eBay seller restriction 25019", failure_stage: "/sell/inventory/v1/offer/publish", submission_performed: true, extension_job_id: null, publish_attempt_id: 991, attempt_count: 1 },
      { marketplace: "facebook", status: "AWAITING_OPERATOR_REVIEW", operation: "CREATE", error_code: "REQUIRED_FIELDS_UNRESOLVED", error_detail: "location", failure_stage: "AWAITING_OPERATOR_REVIEW", submission_performed: false, extension_job_id: 59, attempt_count: 1 },
      { marketplace: "mercari", status: "QUEUED", operation: "CREATE", error_detail: null, submission_performed: false, extension_job_id: 60, attempt_count: 0 },
    ],
  },
};

describe("real Jobs page Details interaction", () => {
  beforeEach(() => {
    mocks.router.query = {};
    mocks.router.replace.mockClear();
    mocks.fetchDetails.mockImplementation(async (id) => detailRows[id]);
    fetchMarketplaceJobsOverview.mockClear();
    window.sessionStorage.clear();
  });

  afterEach(() => { vi.clearAllMocks(); });

  it("opens the selected job, renders its fetched failure/blocker, closes, and switches rows", async () => {
    render(<JobsPage />);

    const failedDetails = await screen.findByTestId("crosspost-details-739");
    fireEvent.click(failedDetails);
    const failedDialog = await screen.findByRole("dialog", { name: "Cross-post job #739" });
    expect(failedDialog).toBeVisible();
    expect(await screen.findByText("Smart Caregiver Call Button")).toBeVisible();
    expect((await screen.findAllByText("Cross-post execution failed for: ebay")).length).toBeGreaterThan(0);
    expect(mocks.fetchDetails).toHaveBeenCalledWith(739);

    fireEvent.click(screen.getByTitle("Close panel"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(screen.getByTestId("crosspost-details-741"));
    const blockedDialog = await screen.findByRole("dialog", { name: "Cross-post job #741" });
    expect(blockedDialog).toBeVisible();
    expect(await screen.findByText("12V RV Wall Sconce")).toBeVisible();
    expect((await screen.findAllByText("Category requires a verified Type aspect.")).length).toBeGreaterThan(0);
    expect(mocks.fetchDetails).toHaveBeenCalledWith(741);
  });

  it("opens production-shaped all-destination failure details and shows each independent outcome", async () => {
    render(<JobsPage />);
    fireEvent.click(await screen.findByTestId("crosspost-details-1043"));
    const dialog = await screen.findByRole("dialog", { name: "Cross-post job #1043" });
    expect(dialog).toBeVisible();
    expect(await screen.findByText("Cyberpunk Reflex Drop Sticks Challenge Game")).toBeVisible();
    expect(await screen.findByText("Destination operations")).toBeVisible();
    expect(await screen.findByText(/eBay seller restriction 25019/i)).toBeVisible();
    expect(await screen.findByText(/location/i)).toBeVisible();
    expect((await screen.findAllByText("Mercari")).length).toBeGreaterThan(0);
    expect(mocks.fetchDetails).toHaveBeenCalledWith(1043);
  });
});
