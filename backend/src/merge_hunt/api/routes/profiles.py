"""
file: profiles.py
importable: router

The user's named profiles. GET /profiles/default gives the settings a new
profile starts from, it comes from the code and is not a saved row, so it can
not be changed or deleted and always matches FilterProfile. Scoring does not
come through here, the UI loads a profile and sends its settings to
POST /scans/{id}/results.

description: The API endpoints that read, save, change and delete named profiles.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status

from merge_hunt.api.schemas import ProfileAnswer, ProfileSettings, ProfileWrite
from merge_hunt.storage.stores import ProfileNameTakenError

router = APIRouter(prefix="/profiles", tags=["profiles"])

def _missing(profile_id: int) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Profile {profile_id} does not exist")

@router.get("")
async def list_profiles(request: Request) -> list[ProfileAnswer]:
    return [ProfileAnswer.of(saved) for saved in await request.app.state.profiles.all()]

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_profile(body: ProfileWrite, request: Request) -> ProfileAnswer:
    try:
        saved = await request.app.state.profiles.create(body.name, body.settings.model_dump(mode="json"), datetime.now(UTC))
    except ProfileNameTakenError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    return ProfileAnswer.of(saved)

# The path is a fixed word and the one below an int, default never reaches it.
@router.get("/default")
async def default_profile() -> ProfileSettings:
    return ProfileSettings()

@router.get("/{profile_id}")
async def get_profile(profile_id: int, request: Request) -> ProfileAnswer:
    saved = await request.app.state.profiles.load(profile_id)
    if saved is None:
        raise _missing(profile_id)
    return ProfileAnswer.of(saved)

@router.put("/{profile_id}")
async def update_profile(profile_id: int, body: ProfileWrite, request: Request) -> ProfileAnswer:
    settings = body.settings.model_dump(mode="json")
    try:
        saved = await request.app.state.profiles.update(profile_id, body.name, settings, datetime.now(UTC))
    except ProfileNameTakenError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    if saved is None:
        raise _missing(profile_id)
    return ProfileAnswer.of(saved)

@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: int, request: Request) -> None:
    if not await request.app.state.profiles.delete(profile_id):
        raise _missing(profile_id)
