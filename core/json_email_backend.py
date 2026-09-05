"""
Custom email backend that stores emails to a JSON file for viewing.
No SMTP - just logs to sent_emails.json.
"""
import json, threading
from pathlib import Path

EMAIL_LOG = Path(__file__).resolve().parent.parent / "sent_emails.json"
_lock = threading.Lock()

class JsonFileEmailBackend:
    def __init__(self, **kwargs):
        pass

    def open(self):
        return self

    def close(self):
        pass

    def send_messages(self, email_messages):
        for msg in email_messages:
            html = getattr(msg, 'html_message', '')
            if not html and hasattr(msg, 'alternatives'):
                for content, mime in msg.alternatives:
                    if mime == 'text/html':
                        html = content
                        break
            entry = {
                'from': msg.from_email,
                'to': msg.to,
                'subject': msg.subject,
                'body': msg.body,
                'html': html,
            }
            with _lock:
                try:
                    data = json.loads(EMAIL_LOG.read_text(encoding='utf-8')) if EMAIL_LOG.exists() else []
                except Exception:
                    data = []
                data.append(entry)
                EMAIL_LOG.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')
        return len(email_messages)
