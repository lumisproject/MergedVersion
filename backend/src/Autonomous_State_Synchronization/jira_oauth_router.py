from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from src.Autonomous_State_Synchronization.jira_oauth import (
    build_auth_url,
    exchange_code_for_token,
    get_accessible_resources
)

jira_auth_router = APIRouter()

# Step 1 — Start Jira OAuth
@jira_auth_router.get("/auth/jira/connect")
def connect_jira(state: str):
    # We use the state passed from the frontend (the user_id)
    auth_url = build_auth_url(state)
    return RedirectResponse(auth_url)

# Step 2 — Jira OAuth callback
@jira_auth_router.get("/auth/jira/callback")
def jira_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state") # This is the user_id

    if not code or not state:
        return {"error": "Missing code or state"}

    tokens = exchange_code_for_token(code, state)
    
    # Redirect back to the frontend with a success message
    # Replace localhost:5173 with your production frontend URL if necessary
    return RedirectResponse(f"http://localhost:5173/auth/jira/callback?message=Jira connected successfully")