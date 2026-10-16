"""Offset pagination shared by every list endpoint: `?limit=&offset=` in, `Page[T]` out."""

from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel, Field

MAX_LIMIT = 100
DEFAULT_LIMIT = 20


class PageParams(BaseModel):
    limit: int
    offset: int


def page_params(
    limit: Annotated[
        int, Query(ge=1, le=MAX_LIMIT, description=f"Page size (1-{MAX_LIMIT}).")
    ] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0, description="How many items to skip.")] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


PageParamsDep = Annotated[PageParams, Depends(page_params)]


class Page[T](BaseModel):
    """One page of a list. `total` counts all matching items, so a client can show
    "21-40 of 134" and know when to stop."""

    items: list[T]
    total: int = Field(description="Number of items across all pages.")
    limit: int
    offset: int
