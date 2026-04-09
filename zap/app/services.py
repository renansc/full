import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


def _whatsapp_base(version: str) -> str:
    return f"https://graph.facebook.com/{version}"


def normalize_whatsapp_phone_number(raw_phone: str, default_country_code: str = "55") -> str:
    digits = re.sub(r"\D", "", raw_phone or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in {10, 11} and not digits.startswith(default_country_code):
        digits = f"{default_country_code}{digits}"
    return digits


def whatsapp_phone_variants(raw_phone: str, default_country_code: str = "55") -> set[str]:
    variants: set[str] = set()
    raw_digits = re.sub(r"\D", "", raw_phone or "")
    if raw_digits.startswith("00"):
        raw_digits = raw_digits[2:]
    if raw_digits:
        variants.add(raw_digits)

    normalized = normalize_whatsapp_phone_number(raw_phone, default_country_code)
    if normalized:
        variants.add(normalized)

    def add_brazil_variants(digits: str):
        if not digits:
            return
        national = digits[len(default_country_code) :] if digits.startswith(default_country_code) else digits
        if len(national) < 10:
            return
        ddd = national[:2]
        local = national[2:]
        if len(local) == 8:
            variants.add(f"{default_country_code}{ddd}9{local}")
            variants.add(f"{ddd}9{local}")
        elif len(local) == 9 and local.startswith("9"):
            variants.add(f"{default_country_code}{ddd}{local[1:]}")
            variants.add(f"{ddd}{local[1:]}")

    add_brazil_variants(raw_digits)
    add_brazil_variants(normalized)
    return {variant for variant in variants if variant}


def _response_data(response: requests.Response) -> dict:
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _extract_error_message(data: dict | None, fallback: str) -> str:
    if not isinstance(data, dict):
        return fallback
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        error_data = error.get("error_data")
        details = error_data.get("details") if isinstance(error_data, dict) else None
        parts = [part for part in [message, details] if part]
        if parts:
            return " - ".join(parts)
    if error:
        return str(error)
    return data.get("message") or fallback


def send_whatsapp_payload(token: str, version: str, phone_number_id: str, payload: dict):
    if not token or not phone_number_id:
        return {"ok": False, "error": "Credenciais do WhatsApp nao configuradas."}

    url = f"{_whatsapp_base(version)}/{phone_number_id}/messages"
    try:
        response = requests.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "data": {}}

    data = _response_data(response)
    if response.ok:
        return {"ok": True, "status_code": response.status_code, "data": data}
    return {
        "ok": False,
        "status_code": response.status_code,
        "error": _extract_error_message(data, f"WhatsApp retornou HTTP {response.status_code}."),
        "data": data,
    }


def upload_whatsapp_media(token: str, version: str, phone_number_id: str, file_path: str, mime_type: str, filename: str | None = None):
    if not token or not phone_number_id:
        return {"ok": False, "error": "Credenciais do WhatsApp nao configuradas."}
    path = Path(file_path)
    if not path.exists():
        return {"ok": False, "error": "Arquivo do anexo nao encontrado."}
    url = f"{_whatsapp_base(version)}/{phone_number_id}/media"
    upload_name = filename or path.name
    guessed_type = mime_type or mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
    try:
        with path.open("rb") as handle:
            response = requests.post(
                url,
                data={"messaging_product": "whatsapp", "type": guessed_type},
                files={"file": (upload_name, handle, guessed_type)},
                headers={"Authorization": f"Bearer {token}"},
                timeout=60,
            )
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "data": {}}
    data = _response_data(response)
    if response.ok:
        return {"ok": True, "status_code": response.status_code, "data": data}
    return {
        "ok": False,
        "status_code": response.status_code,
        "error": _extract_error_message(data, f"WhatsApp retornou HTTP {response.status_code} no upload de midia."),
        "data": data,
    }


def send_whatsapp_media_by_id(token: str, version: str, phone_number_id: str, to: str, media_type: str, media_id: str, caption: str = "", filename: str = ""):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": media_type,
        media_type: {"id": media_id},
    }
    if caption and media_type in {"image", "video", "document"}:
        payload[media_type]["caption"] = caption
    if filename and media_type == "document":
        payload[media_type]["filename"] = filename
    return send_whatsapp_payload(token, version, phone_number_id, payload)


def download_whatsapp_media(token: str, version: str, media_id: str):
    if not token:
        return {"ok": False, "error": "Credenciais do WhatsApp nao configuradas."}
    metadata_url = f"{_whatsapp_base(version)}/{media_id}"
    try:
        metadata_response = requests.get(metadata_url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "data": {}}

    metadata = _response_data(metadata_response)
    if not metadata_response.ok:
        return {
            "ok": False,
            "status_code": metadata_response.status_code,
            "error": _extract_error_message(metadata, f"WhatsApp retornou HTTP {metadata_response.status_code} ao consultar midia."),
            "data": metadata,
        }

    media_url = metadata.get("url")
    if not media_url:
        return {"ok": False, "error": "WhatsApp nao retornou URL da midia.", "data": metadata}

    try:
        download_response = requests.get(media_url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "data": metadata}

    if not download_response.ok:
        return {
            "ok": False,
            "status_code": download_response.status_code,
            "error": _extract_error_message(_response_data(download_response), f"Falha ao baixar midia HTTP {download_response.status_code}."),
            "data": metadata,
        }

    filename = metadata.get("filename") or media_id
    return {
        "ok": True,
        "status_code": download_response.status_code,
        "data": metadata,
        "filename": filename,
        "mime_type": metadata.get("mime_type", ""),
        "content": download_response.content,
    }


def send_whatsapp_text(token: str, version: str, phone_number_id: str, to: str, body: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    return send_whatsapp_payload(token, version, phone_number_id, payload)


def send_whatsapp_media(token: str, version: str, phone_number_id: str, to: str, media_type: str, link: str, caption: str = ""):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": media_type,
        media_type: {"link": link, "caption": caption} if caption else {"link": link},
    }
    return send_whatsapp_payload(token, version, phone_number_id, payload)


def send_whatsapp_template(token: str, version: str, phone_number_id: str, to: str, template_name: str, language_code: str = "pt_BR", components: list | None = None):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }
    if components:
        payload["template"]["components"] = components
    return send_whatsapp_payload(token, version, phone_number_id, payload)


def send_whatsapp_interactive(token: str, version: str, phone_number_id: str, to: str, interactive: dict):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }
    return send_whatsapp_payload(token, version, phone_number_id, payload)


def send_whatsapp_location(token: str, version: str, phone_number_id: str, to: str, latitude: float, longitude: float, name: str, address: str = ""):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "location",
        "location": {
            "latitude": latitude,
            "longitude": longitude,
            "name": name,
            "address": address,
        },
    }
    return send_whatsapp_payload(token, version, phone_number_id, payload)


def send_whatsapp_contact(token: str, version: str, phone_number_id: str, to: str, contact: dict):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "contacts",
        "contacts": [contact],
    }
    return send_whatsapp_payload(token, version, phone_number_id, payload)


def mark_whatsapp_message_read(token: str, version: str, phone_number_id: str, message_id: str):
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    return send_whatsapp_payload(token, version, phone_number_id, payload)


def make_google_auth_url(client_id: str, redirect_uri: str, scopes: list[str], state: str):
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def iso_now():
    return datetime.utcnow().isoformat() + "Z"


def _load_service_account_credentials(service_account_json: str):
    if not service_account_json:
        return None
    data = json.loads(service_account_json)
    return Credentials.from_service_account_info(
        data,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )


def build_sheets_service(service_account_json: str):
    credentials = _load_service_account_credentials(service_account_json)
    if not credentials:
        return None
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def sheet_rows_from_tickets(tickets):
    rows = [
        [
            "Ticket ID",
            "Cliente",
            "Telefone",
            "Empresa",
            "Servico",
            "Estado",
            "Etiquetas",
            "Vencimento",
            "Descricao",
            "Atualizado em",
        ]
    ]
    for ticket in tickets:
        rows.append(
            [
                ticket.id,
                ticket.client_name,
                ticket.client_phone,
                ticket.company,
                ticket.service,
                ticket.status.name if ticket.status else "",
                ", ".join(label.name for label in ticket.labels),
                ticket.due_at.isoformat(sep=" ", timespec="minutes") if ticket.due_at else "",
                ticket.description,
                ticket.updated_at.isoformat(sep=" ", timespec="seconds"),
            ]
        )
    return rows


def sync_tickets_to_sheet(service_account_json: str, spreadsheet_id: str, sheet_name: str, tickets):
    service = build_sheets_service(service_account_json)
    if not service:
        return {"ok": False, "error": "Credenciais do Sheets nao configuradas."}
    if not spreadsheet_id:
        return {"ok": False, "error": "Spreadsheet ID nao configurado."}
    sheet_name = sheet_name or "Agenda"
    rows = sheet_rows_from_tickets(tickets)
    range_name = f"{sheet_name}!A1"
    service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:Z").execute()
    response = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    return {"ok": True, "updatedRange": response.get("updatedRange"), "updatedRows": len(rows) - 1}


def preview_sheet_rows(service_account_json: str, spreadsheet_id: str, sheet_name: str, limit: int = 20):
    service = build_sheets_service(service_account_json)
    if not service:
        return {"ok": False, "error": "Credenciais do Sheets nao configuradas.", "rows": []}
    if not spreadsheet_id:
        return {"ok": False, "error": "Spreadsheet ID nao configurado.", "rows": []}
    sheet_name = sheet_name or "Agenda"
    response = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:J{limit + 1}",
    ).execute()
    values = response.get("values", [])
    if not values:
        return {"ok": True, "rows": []}
    header = values[0]
    rows = [dict(zip(header, row)) for row in values[1:]]
    return {"ok": True, "rows": rows, "header": header}
