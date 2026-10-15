"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

# scope="function": commit/rollback runs when the route function returns, before the
# response is sent. See get_session.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
