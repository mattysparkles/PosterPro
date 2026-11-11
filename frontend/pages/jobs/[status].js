import { useRouter } from "next/router";
import JobsPage from "../jobs";

const ALLOWED = new Set(["active", "completed", "failed"]);

export default function JobsStatusPage() {
  const router = useRouter();
  const status = typeof router.query.status === "string" ? router.query.status : "";
  const normalized = String(status || "").toLowerCase();

  if (!ALLOWED.has(normalized)) {
    return <JobsPage statusOverride={null} />;
  }

  return <JobsPage statusOverride={normalized} />;
}

