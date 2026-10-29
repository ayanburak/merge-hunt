"""
file: scans.py
importable: router

POST /scans starts a scan and GET /scans/{id} tells how it is going. The
ScanJob and ScanStore come from app.state, main.py puts them there when the
app starts, so these functions never build anything themselves.

description: The API endpoints that start a scan and report its progress.
"""

from fastapi import APIRouter, HTTPException, Request, status

from merge_hunt.api.schemas import ScanCreated, ScanRequest
from merge_hunt.domain.scans import Scan
from merge_hunt.jobs.scans import ScanAlreadyRunningError

router = APIRouter(prefix="/scans", tags=["scans"])

# 202 because the scan has only started, the answer comes long before it ends.
@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_scan(body: ScanRequest, request: Request) -> ScanCreated:
    try:
        scan_id = await request.app.state.scan_job.start(body.discovery_options(), body.metric_options())
    except ScanAlreadyRunningError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    return ScanCreated(id=scan_id)

# The UI calls this every few seconds while a scan runs. Scan is a frozen
# dataclass, FastAPI sends it as JSON as it is.
@router.get("/{scan_id}")
async def get_scan(scan_id: int, request: Request) -> Scan:
    scan = await request.app.state.scans.load(scan_id)
    if scan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Scan {scan_id} does not exist")
    return scan
