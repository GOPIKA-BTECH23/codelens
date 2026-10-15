"""GET /me: who am I, and which installations can I see with which role."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.db.models.enums import MemberRole
from app.deps import CurrentUserDep, SessionDep
from app.services import rbac

router = APIRouter(tags=["me"])


class UserOut(BaseModel):
    id: uuid.UUID
    login: str
    name: str | None
    avatar_url: str | None


class InstallationOut(BaseModel):
    id: uuid.UUID
    github_installation_id: int
    account_login: str
    account_type: str
    role: MemberRole


class MeResponse(BaseModel):
    user: UserOut
    installations: list[InstallationOut]


def installation_out(membership: rbac.Membership) -> InstallationOut:
    inst = membership.installation
    return InstallationOut(
        id=inst.id,
        github_installation_id=inst.github_installation_id,
        account_login=inst.account_login,
        account_type=inst.account_type,
        role=membership.role,
    )


@router.get("/me", response_model=MeResponse, summary="The signed-in user and their roles")
async def me(user: CurrentUserDep, db: SessionDep) -> MeResponse:
    memberships = await rbac.list_memberships(db, user.id)
    return MeResponse(
        user=UserOut(id=user.id, login=user.login, name=user.name, avatar_url=user.avatar_url),
        installations=[installation_out(m) for m in memberships],
    )
