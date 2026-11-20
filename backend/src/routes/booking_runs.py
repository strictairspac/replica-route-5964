from fastapi import APIRouter, Depends
from fastapi.responses import Response
import json

from src.config.database import db_session
from src.utils.security import current, writer
from src.schemas.booking_run import BookingRunInput
from src.services import booking_runs as service

router = APIRouter()


@router.get('/booking_runs')
def listing(db=Depends(db_session), user=Depends(current)):
    return service.listing(db)


@router.post('/booking_runs', status_code=201)
def create(data: BookingRunInput, db=Depends(db_session), user=Depends(writer)):
    return service.create(db, data, user)


@router.get('/booking_runs/{ident}')
def detail(ident: int, db=Depends(db_session), user=Depends(current)):
    return service.detail(db, ident)


@router.get('/booking_runs/{ident}/export')
def export(ident: int, db=Depends(db_session), user=Depends(current)):
    return Response(json.dumps(service.detail(db, ident), ensure_ascii=False, indent=2),
                    media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="booking-{ident}.json"'})
