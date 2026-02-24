import requests
from urllib.parse import urlencode
from src.Autonomous_State_Synchronization.config import (
    JIRA_API_BASE,
    JIRA_CLIENT_ID,
    JIRA_CLIENT_SECRET,
    JIRA_REDIRECT_URI,
    JIRA_AUTH_URL,
    JIRA_TOKEN_URL,
    JIRA_API_BASE_URL
)
from src.token_store import save_tokens

SCOPES = [
    "read:jira-work",
    "write:jira-work",
    "offline_access"
]

def build_auth_url(user_id: str):
    params = {
        "audience": "api.atlassian.com",
        "client_id": JIRA_CLIENT_ID,
        "scope": " ".join(SCOPES),
        "redirect_uri": JIRA_REDIRECT_URI,
        "state": user_id,
        "response_type": "code",
        "prompt": "consent"
    }
    return f"{JIRA_AUTH_URL}?{urlencode(params)}"

def exchange_code_for_token(code: str, user_id: str):
    payload = {
        "grant_type": "authorization_code",
        "client_id": JIRA_CLIENT_ID,
        "client_secret": JIRA_CLIENT_SECRET,
        "code": code,
        "redirect_uri": JIRA_REDIRECT_URI
    }

    response = requests.post(JIRA_TOKEN_URL, json=payload)
    response.raise_for_status()

    tokens = response.json()
    save_tokens(user_id, tokens)

    return tokens

def get_accessible_resources(access_token: str):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    # Correct Endpoint: https://api.atlassian.com/oauth/token/accessible-resources
    # Use JIRA_API_BASE from config.py which is "https://api.atlassian.com"
    url = f"{JIRA_API_BASE}/oauth/token/accessible-resources"
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()
