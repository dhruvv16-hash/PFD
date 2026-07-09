import os
import urllib.parse
import urllib.request
import json
from datetime import datetime, timezone

from agents.base import BaseAgent, AgentValidationError
from audit.audit import log_audit
from config.settings import settings

class TelegramAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="TelegramAgent",
            version="1.0.0",
            role="Sends real-time trade signals to Telegram using the Telegram Bot API"
        )

    def validate_inputs(self, inputs: dict):
        super().validate_inputs(inputs)
        if "message" not in inputs:
            raise AgentValidationError("Missing required input field: 'message'")

    def execute(self, inputs: dict, job_id: str) -> dict:
        message = inputs["message"]
        log_audit(job_id, "TelegramNotify", f"Preparing to dispatch Telegram alert: {message[:60]}...")
        
        # Load bot credentials
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or getattr(settings, "telegram_bot_token", None)
        chat_id = os.environ.get("TELEGRAM_CHAT_ID") or getattr(settings, "telegram_chat_id", None)
        
        success = False
        dispatch_method = "MockFallback"
        error_msg = None
        
        if bot_token and chat_id:
            try:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                
                payload = {
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "Markdown"
                }
                
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
                
                with urllib.request.urlopen(req, timeout=5) as response:
                    res_body = response.read().decode("utf-8")
                    log_audit(job_id, "TelegramNotify", f"Telegram API successfully returned: {res_body[:100]}")
                    success = True
                    dispatch_method = "TelegramBotAPI"
            except Exception as e:
                error_msg = str(e)
                log_audit(job_id, "TelegramNotify", f"Telegram API failed: {error_msg}. Falling back to mock log.")
                
        if not success:
            db_dir = os.path.dirname(settings.db_path)
            project_root = os.path.dirname(db_dir)
            comm_dir = os.path.join(project_root, "communication")
            os.makedirs(comm_dir, exist_ok=True)
            
            outbox_path = os.path.join(comm_dir, "telegram_mock_outbox.log")
            timestamp = datetime.now(timezone.utc).isoformat()
            
            with open(outbox_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] [JOB {job_id}] CHAT_ID: {chat_id or 'MOCK_CHAT'} | MESSAGE:\n{message}\n" + "="*50 + "\n")
                
            log_audit(job_id, "TelegramNotify", f"Mock outbox log saved at {outbox_path}")
            success = True
            
        return {
            "success": success,
            "dispatch_method": dispatch_method,
            "error": error_msg
        }
