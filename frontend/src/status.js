// Single source of truth for turning an /enquiries row into a Desk "bucket".
// Desk (chips + card badges) and Dashboard (pie/stat cards) both key off this
// so they can never disagree about what "drafted", "sent", etc. mean.
// "sent" is always computed (every supplier draft sent), never a stored status.
export function deskBucket(e) {
  if (e.status === "drafted" && e.drafts > 0 && e.sent === e.drafts) return "sent";
  if (e.status === "new" || e.status === "processing" || e.status === "drafted")
    return e.status;
  return "logged";
}

export const BUCKET_ORDER = ["new", "processing", "drafted", "sent", "logged"];

// Aggregate/category labels — Title Case, used for filter chips, chart
// legends and stat cards (matches the original UI mockup's stat-chip/filter
// labels: "All", "New", "Processing", "Drafted", "Fully sent").
export const BUCKET_LABEL = {
  new: "New",
  processing: "Processing",
  drafted: "Drafted",
  sent: "Fully sent",
  logged: "Logged",
};

// Per-row badge text — lowercase (the pill's own CSS uppercases it), used
// wherever a SINGLE enquiry's badge renders (a Desk card, a Dashboard table
// row). Keeping this separate from BUCKET_LABEL above is deliberate: the same
// enquiry must show identical badge text in every view it appears in.
export const BADGE_TEXT = {
  new: "new",
  processing: "processing",
  drafted: "drafted",
  sent: "all sent",
};

// A logged enquiry shows its extracted scenario when there is one (more
// informative than a generic "logged"), exactly like the Desk card always has.
export function rowBadge(e) {
  const bucket = deskBucket(e);
  return bucket === "logged" ? e.scenario || "logged" : BADGE_TEXT[bucket];
}

// CSS custom-property name (without var()) each bucket renders as, so charts
// and badges pull from the same palette instead of a second hardcoded one.
export const BUCKET_COLOR_VAR = {
  new: "--info",
  processing: "--warn",
  drafted: "--ok",
  sent: "--accent-2",
  logged: "--text-faint",
};
