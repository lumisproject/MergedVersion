import json
import re
import logging
import ast
import requests
from typing import List, Dict, Any
from src.services import get_llm_completion
from src.retriever import GraphRetriever
from src.answer_generator import AnswerGenerator
from src.query_processor import QueryProcessor

# --- INTEGRATED JIRA IMPORTS ---
from src.token_store import get_valid_token
from src.jira.client import get_accessible_resources, jira_headers

class LumisAgent:
    def __init__(self, project_id: str, mode: str = "single-turn", max_steps: int = 4):
        self.project_id = project_id
        self.mode = mode
        self.retriever = GraphRetriever(project_id)
        self.generator = AnswerGenerator(project_id)
        self.query_processor = QueryProcessor()
        self.max_steps = max_steps
        self.conversation_history: List[Dict[str, str]] = []
        self.logger = logging.getLogger(__name__)

    def ask(self, user_query: str, reasoning_enabled: bool = True, user_id: str = None) -> str:
        """
        Modified ask method to detect Jira intent or proceed with standard code analysis.
        """
        # 1. Detect Jira-related intent (e.g., "what next", "my tasks", "assigned to me")
        jira_keywords = ["task", "work", "next", "jira", "assigned", "todo", "to-do"]
        if any(word in user_query.lower() for word in jira_keywords):
            return self._handle_jira_tasks(user_query, user_id)

        # 2. Standard Code Analysis Workflow
        if self.mode == "single-turn":
            self.conversation_history = []

        scratchpad = []
        collected_elements: List[Dict[str, Any]] = [] 
        repo_structure = None 
        
        processed_query = self.query_processor.process(user_query, self.conversation_history)

        for step in range(self.max_steps):
            prompt = self._build_step_prompt(processed_query, scratchpad)
            
            response_text = get_llm_completion(
                self._get_system_prompt(), 
                prompt, 
                reasoning_enabled=reasoning_enabled
            )
            
            data = self._parse_response(response_text, fallback_query=user_query)
            
            if data.get("confidence", 0) >= 95 or data.get("action") == "final_answer":
                break

            if not data.get("action") or data.get("action") == "none": 
                break
            
            obs = self._execute_tool(data.get("action"), data.get("action_input"), collected_elements, scratchpad, processed_query)
            if data.get("action") == "list_files": repo_structure = obs 

        result = self.generator.generate(
            query=user_query, 
            collected_elements=collected_elements, 
            repo_structure=repo_structure,
            history=self.conversation_history
        )
        self._update_history(user_query, result['answer'])
        return result['answer']

    def _handle_jira_tasks(self, query: str, user_id: str) -> str:
        """
        Fetches active Jira issues and cross-references them with the codebase.
        """
        if not user_id:
            return "I need your user ID to access your Jira workspace. Please ensure you are logged in."
        
        token = get_valid_token(user_id)
        if not token:
            return "Your Jira account is not connected. Please go to the Dashboard to link your Jira workspace."

        try:
            # 1. Get Jira Workspace ID
            resources = get_accessible_resources(token)
            if not resources:
                return "No Jira projects found linked to your account."
            cloud_id = resources[0]["id"]
            
            # 2. Fetch Assigned Issues (JQL: issues assigned to current user, not done)
            # We add a small ad-hoc search request here
            jql = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
            search_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search"
            res = requests.get(search_url, headers=jira_headers(token), params={"jql": jql, "maxResults": 3})
            
            issues = res.json().get("issues", [])
            if not issues:
                return "You currently have no active tasks assigned in Jira. Great job!"

            # 3. Contextualize issues with the codebase
            task_summaries = []
            code_context = []

            for issue in issues:
                summary = issue['fields']['summary']
                key = issue['key']
                task_summaries.append(f"[{key}] {summary}")
                
                # Use GraphRetriever to find relevant code snippets based on the task summary
                relevant_code = self.retriever.search(summary, limit=2)
                for code in relevant_code:
                    code_context.append(f"Task {key} might involve {code['file_path']} (found logic related to '{summary}')")

            # 4. Final synthesis via LLM
            prompt = (
                f"User asked: '{query}'\n\n"
                f"ACTIVE JIRA TASKS:\n" + "\n".join(task_summaries) + "\n\n"
                f"CODEBASE CLUES:\n" + "\n".join(code_context) + "\n\n"
                "Explain what the user should work on next and point them to the specific files in the repository."
            )
            
            return get_llm_completion(
                "You are Lumis, the Digital Twin Agent. You help developers bridge the gap between tasks and code.",
                prompt
            )

        except Exception as e:
            self.logger.error(f"Jira Agent Error: {e}")
            return f"I encountered an error while checking Jira: {str(e)}"

    # --- HELPERS (Existing Logic) ---

    def _build_step_prompt(self, processed_query, scratchpad):
        history_text = ""
        if self.conversation_history and len(self.conversation_history) > 0:
            recent_msgs = self.conversation_history[-6:]
            history_text = "CONVERSATION HISTORY:\n" + "\n".join(
                [f"{m['role'].upper()}: {m['content']}" for m in recent_msgs]
            ) + "\n\n"
            
        progress = "\n".join([f"Action: {s['action']} -> {s['observation']}" for s in scratchpad])
        query_context = f"USER QUERY: {processed_query.original}"
        
        insights = []
        if processed_query.rewritten_query:
             insights.append(f"Search Hint: Try searching for '{processed_query.rewritten_query}'")
        if processed_query.pseudocode_hints:
             insights.append(f"Implementation Hint:\n{processed_query.pseudocode_hints}")
             
        insight_text = "\n\n".join(insights)
        return f"{history_text}{query_context}\n\n{insight_text}\n\nPROGRESS:\n{progress}\n\nNEXT JSON:"

    def _parse_response(self, text: str, fallback_query: str = "") -> Dict[str, Any]:
        if not text: return self._create_fallback(fallback_query, "Empty response")
        clean_text = text.replace("```json", "").replace("```", "").strip()
        start_idx = clean_text.find('{')
        end_idx = clean_text.rfind('}')
        if start_idx != -1 and end_idx != -1:
            try:
                json_str = self._sanitize_json_string(clean_text[start_idx:end_idx + 1])
                return json.loads(json_str)
            except: pass
        return self._create_fallback(fallback_query, text[:200])

    def _create_fallback(self, query: str, thought_snippet: str) -> Dict[str, Any]:
        return {"thought": f"Falling back to search. Raw: {thought_snippet}...", "action": "search_code", "action_input": query, "confidence": 50}

    def _sanitize_json_string(self, json_str: str) -> str:
        json_str = re.sub(r'//.*?\n', '\n', json_str)
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        return json_str

    def _execute_tool(self, action, inp, collected, scratchpad, processed_query=None):
        obs = "No results."
        try:
            if action == "list_files":
                files = self.retriever.list_all_files()
                obs = f"Repo contains {len(files)} files. First 50: {', '.join(files[:50])}"
            elif action == "read_file":
                path = str(inp).strip()
                data = self.retriever.fetch_file_content(path)
                if data:
                    collected.extend(data)
                    obs = f"Successfully read {path}."
                else:
                    obs = f"Error: File {path} not found."
            elif action == "search_code":
                search_input = str(inp)
                if processed_query and processed_query.rewritten_query:
                    search_input = f"{search_input} {processed_query.rewritten_query}"
                if processed_query and processed_query.pseudocode_hints:
                    search_input += f" {processed_query.pseudocode_hints}"
                data = self.retriever.search(search_input)
                if data:
                    collected.extend(data)
                    obs = f"Found {len(data)} matches."
        except Exception as e:
            obs = f"Tool Error: {str(e)}"
        scratchpad.append({"thought": "System Result", "action": f"{action}({inp})", "observation": obs})
        return obs

    def _get_system_prompt(self) -> str:
        return (
            "You are Lumis, a 'Scouting-First' code analysis agent.\n"
            "Your goal is to answer user queries with PRECISE code evidence.\n"
            "1. SCOUT: Use `list_files` or `search_code`.\n"
            "2. READ: Call `read_file` when 80%+ sure.\n"
            "3. ANSWER: Call `final_answer`."
        )

    def _update_history(self, q, a):
        if self.mode == "multi-turn":
            self.conversation_history.append({"role": "user", "content": q})
            self.conversation_history.append({"role": "assistant", "content": a})