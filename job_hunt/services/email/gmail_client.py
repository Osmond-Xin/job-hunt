from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from job_hunt.services.email.message_parser import ParsedEmail


GMAIL_READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_MODIFY_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailClient:
    """Thin Gmail API wrapper.

    The implementation intentionally keeps OAuth tokens outside git-tracked paths.
    Install the optional gmail dependencies before using this class:
    `pip install -e ".[gmail]"`.
    """

    def __init__(
        self,
        token_path: Path,
        credentials_path: Path,
        scopes: list[str] | None = None,
        auth_mode: str = "gcloud_adc",
    ):
        self.token_path = token_path
        self.credentials_path = credentials_path
        self.scopes = scopes or GMAIL_READONLY_SCOPES
        self.auth_mode = auth_mode
        self._service = None

    def available(self) -> bool:
        try:
            import googleapiclient.discovery  # noqa: F401
            import google_auth_oauthlib.flow  # noqa: F401
        except ImportError:
            return False
        return True

    def service(self):
        if self._service is not None:
            return self._service
        if not self.available():
            raise RuntimeError("Gmail dependencies are not installed. Run: pip install -e '.[gmail]'")

        from googleapiclient.discovery import build

        creds = self._load_credentials()
        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def _load_credentials(self):
        if self.auth_mode == "gcloud_adc":
            return self._load_adc_credentials()
        return self._load_oauth_file_credentials()

    def _load_adc_credentials(self):
        import google.auth
        from google.auth.transport.requests import Request

        creds, _project = google.auth.default(scopes=self.scopes)
        if creds and not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds or not creds.valid:
            raise RuntimeError(
                "gcloud ADC credentials are unavailable or invalid. Run "
                "`gcloud auth application-default login --scopes=https://www.googleapis.com/auth/gmail.readonly`."
            )
        return creds

    def _load_oauth_file_credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), self.scopes)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Gmail credentials not found at {self.credentials_path}. "
                        "Place your OAuth client JSON there or update config/settings.yml."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), self.scopes)
                creds = flow.run_local_server(port=0)
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def list_messages(self, query: str, label_ids: list[str] | None = None, max_results: int = 25) -> list[dict[str, Any]]:
        request = (
            self.service()
            .users()
            .messages()
            .list(userId="me", q=query, labelIds=label_ids or [], maxResults=max_results)
        )
        response = request.execute()
        return response.get("messages", [])

    def get_message(self, message_id: str, fmt: str = "full") -> dict[str, Any]:
        return self.service().users().messages().get(userId="me", id=message_id, format=fmt).execute()

    def parse_message(self, raw: dict[str, Any]) -> ParsedEmail:
        payload = raw.get("payload", {})
        headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers", [])}
        body = extract_body(payload)
        return ParsedEmail(
            message_id=raw.get("id", ""),
            thread_id=raw.get("threadId"),
            sender=headers.get("from", ""),
            subject=headers.get("subject", ""),
            snippet=raw.get("snippet", "") or body[:240],
            body=body,
            date=internal_date_to_datetime(raw.get("internalDate")),
        )

    def add_label(self, message_id: str, label_id: str) -> None:
        self.service().users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()


def extract_body(payload: dict[str, Any]) -> str:
    chunks: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if data and mime_type in {"text/plain", "text/html", ""}:
            chunks.append(decode_base64url(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def decode_base64url(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")


def internal_date_to_datetime(value: str | None):
    from datetime import datetime, timezone

    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
