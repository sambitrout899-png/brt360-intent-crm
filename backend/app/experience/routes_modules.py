from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.db import get_db
from app.licensing import MODULES, enabled, user_enabled
from app.models.auth import User

router = APIRouter(prefix='/api/modules', tags=['modules'])

@router.get('')
def modules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Each module: whether the organization has it, and whether this user may use it (a per-user service needs a seat)."""
    org = enabled(db, user.tenant_id)
    mine = user_enabled(db, user)
    return [{'key': key, **value, 'enabled': key in mine, 'organization': key in org} for key, value in MODULES.items()]
