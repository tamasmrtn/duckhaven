import type { SystemCatalog } from "@/types/systemCatalog";
import listing from "./systemCatalog.json";

// What the API lists for an admin, who may read every table. The JSON is written
// from the API's own table definitions by
// api/tests/unit/routers/test_system_catalog_listing.py, which fails while it is
// stale (rerun it with UPDATE_SYSTEM_CATALOG_FIXTURE=1).
export const systemCatalog: SystemCatalog = listing;
