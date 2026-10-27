from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.db import get_db
from app.licensing import MODULES, enabled
from app.models.auth import User

router = APIRouter(prefix='/api/modules', tags=['modules'])

@router.get('')
def modules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    active = enabled(db, user.tenant_id)
    return [{'key': key, **value, 'enabled': key in active} for key, value in MODULES.items()]
