from src.Autonomous_State_Synchronization.commit_parser import detect_intent

def decide_jira_action(issue, commit_message: str):
    """
    Determines actions based on issue status and commit intent.
    """
    current_status = issue["fields"]["status"]["name"].lower()
    intent = detect_intent(commit_message)

    # Never update completed issues
    if current_status == "done":
        return None

    # Base action: Always comment
    decision = {
        "action": "comment",
        "message": f"📌 Commit linked (Intent: {intent}):\n{commit_message}"
    }

    # If intent is 'feature' and it's not started, trigger transition
    if intent == "feature" and current_status != "in progress":
        decision["action"] = "transition"
        decision["target_status"] = "In Progress"

    return decision