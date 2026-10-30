"""
file: scans.py
importable: router

POST /scans starts a scan, GET /scans lists the latest ones and GET /scans/{id}
tells how one is going. POST /scans/{id}/results scores the repos of a scan with
the settings in its body. It is a POST because those settings do not fit in a
URL, nothing is saved and a scan still running gives the repos done so far.
The parts come from app.state, main.py puts them there when the app starts, so
these functions never build anything themselves.

description: The API endpoints that start a scan, report its progress and give its scored repos.
"""

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from merge_hunt.api.schemas import ProfileSettings, ScanCreated, ScanRequest
from merge_hunt.domain.scans import Scan
from merge_hunt.domain.scoring import ScoredRepo
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

# The UI uses this to find the last scan when it opens.
@router.get("")
async def list_scans(request: Request, limit: int = Query(20, ge=1, le=100)) -> list[Scan]:
    return await request.app.state.scans.recent(limit)

# The UI calls this every few seconds while a scan runs. Scan is a frozen
# dataclass, FastAPI sends it as JSON as it is.
@router.get("/{scan_id}")
async def get_scan(scan_id: int, request: Request) -> Scan:
    scan = await request.app.state.scans.load(scan_id)
    if scan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Scan {scan_id} does not exist")
    return scan

# No body means the default profile. Repos that are not enriched yet are simply
# not in the answer, Scorer leaves them out.
@router.post("/{scan_id}/results")
async def scan_results(
    scan_id: int,
    request: Request,
    body: ProfileSettings = Body(default_factory=ProfileSettings),
) -> list[ScoredRepo]:
    state = request.app.state
    if await state.scans.load(scan_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Scan {scan_id} does not exist")
    return await state.scorer.score(await state.scans.repo_ids(scan_id), body.filter_profile())
