"""House Gift Travel attribution reviewed against original Clerk forms."""

# The URL's MT label is not authoritative. The official employee form names
# Elliott Shin as traveler and Gimenez as the supervising member.
SHIN_EMPLOYEE_FILING = (
    "https://disclosures-clerk.house.gov/gtimages/MT/2026/600010086.pdf"
)


def apply_reviewed_traveler(trip):
    """Correct a reviewed filing by exact source, without removing it."""
    if trip.get("doc_id") == "600010086" and trip.get("source_doc") == SHIN_EMPLOYEE_FILING:
        trip["traveler"] = "Elliott Shin"
        trip["filer_type"] = "staff"


def is_member_travel(trip):
    """Keep legacy unlabelled rows; exclude explicit staff and unverified rows."""
    return trip.get("filer_type") not in ("staff", "unverified")
