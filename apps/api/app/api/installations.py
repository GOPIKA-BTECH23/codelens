"""Installation-scoped endpoints. They exist in M2 mainly to prove RBAC end to end:
  GET /installations/{id}          any member
  GET /installations/{id}/members  admin or owner (the enforcement proof)
M6 adds the real repo and review endpoints on the same guards.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.me import InstallationOut, installation_out
from app.db.models.enums import MemberRole
from app.deps import SessionDep, require_installation_role
from app.services import rbac

router = APIRouter(prefix="/installations", tags=["installations"])

MemberDep = Annotated[rbac.Membership, Depends(require_installation_role(MemberRole.MEMBER))]
AdminDep = Annotated[rbac.Membership, Depends(require_installation_role(MemberRole.ADMIN))]


class MemberOut(BaseModel):
    user_id: uuid.UUID
    login: str
    name: str | None
    role: MemberRole


@router.get(
    "/{installation_id}",
    response_model=InstallationOut,
    summary="One installation (members only)",
    responses={404: {"description": "Not found, or you are not a member"}},
)
async def get_installation(membership: MemberDep) -> InstallationOut:
    return installation_out(membership)


@router.get(
    "/{installation_id}/members",
    response_model=list[MemberOut],
    summary="Members of an installation (admin or owner)",
    responses={
        403: {"description": "You are a member, but not an admin"},
        404: {"description": "Not found, or you are not a member"},
    },
)
async def list_installation_members(membership: AdminDep, db: SessionDep) -> list[MemberOut]:
    members = await rbac.list_members(db, membership.installation.id)
    return [MemberOut(user_id=u.id, login=u.login, name=u.name, role=role) for u, role in members]
