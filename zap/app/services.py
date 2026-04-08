import json
from datetime import datetime
from urllib.parse import urlencode

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


def _whatsapp_base(version: str) -> str:
    return f"https://graph.facebook.com/{version}"


def send_whatsapp_payload(token: str, version: str, phone_number_id: str, payload: dict):
    if not token or not phone_number_id:
        return {"ok": False, "error": "Credenciais do WhatsApp não configuradas."}

    url = f"{_whatsapp_base(version)}/{phone_number_id}/messages"
    response = requests.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return {"ok": response.ok, "status_code": response.status_code, "data": response.json() if response.content else {}}


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
            "Serviço",
            "Estado",
            "Etiquetas",
            "Vencimento",
            "Descrição",
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
        return {"ok": False, "error": "Credenciais do Sheets não configuradas."}
    if not spreadsheet_id:
        return {"ok": False, "error": "Spreadsheet ID não configurado."}
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
        return {"ok": False, "error": "Credenciais do Sheets não configuradas.", "rows": []}
    if not spreadsheet_id:
        return {"ok": False, "error": "Spreadsheet ID não configurado.", "rows": []}
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
