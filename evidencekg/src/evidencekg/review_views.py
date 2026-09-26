"""Read-only, version-bound views of attributed assurance results."""

from .assurance import assurance_data, assurance_status
from .db import dump, ident, sha
from .retrieval import API


def assurance_records(store, run_id):
    offset, items = 0, []
    while True:
        page = assurance_data(store, run_id, offset=offset, limit=1000)
        items.extend(page["items"])
        offset = page["next_offset"]
        if offset is None:
            return items


def review_evidence(store, run_id, cursor=None, limit=100):
    run = store.one("SELECT snapshot_id FROM runs WHERE id=?", (run_id,))
    # Active reviews can append results. Bind continuation to exact result set:
    # a changed view rejects the cursor rather than skipping or duplicating rows.
    items = [{**r, "id": r.get("id", ident("V", r))} for r in assurance_records(store, run_id)]
    version = sha(dump(items))
    result = API(store).page(
        run["snapshot_id"],
        {"kind": "review_evidence", "run_id": run_id, "view_sha": version},
        items,
        cursor=cursor,
        limit=limit,
    )
    result["assurance"] = assurance_status(store, run_id)
    result["basis"] = "attributed model interpretations; never mechanical facts"
    return result
