"""Small helpers for loading related rows in batches without changing API shapes."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select


def objects_by_id(db, model, ids):
    values = {x for x in ids if x is not None}
    if not values:
        return {}
    return {row.id: row for row in db.scalars(select(model).where(model.id.in_(values)))}


def rows_by_attr(db, model, attr, values):
    keys = {x for x in values if x is not None}
    if not keys:
        return {}
    result = defaultdict(list)
    for row in db.scalars(select(model).where(getattr(model, attr).in_(keys))):
        result[getattr(row, attr)].append(row)
    return result
