import html
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from io import BytesIO
from typing import List

import requests
from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv()

YEAR = 2026
MONTHS = [1,2,3,4,5,6,7,8,9,10,11,12]
PDF_URLS = {
    "mixed": "https://www.pgk-radomsko.pl/images/zom/harmonogramy/m_radomsko/2026/zmieszane_2026.pdf",
    "selective": "https://www.pgk-radomsko.pl/images/zom/harmonogramy/m_radomsko/2026/selektywna_system_workowy_2026.pdf",
    "bio": "https://www.pgk-radomsko.pl/images/zom/harmonogramy/m_radomsko/2026/bio_zielone_system_workowy_2026.pdf",
}

# Ustalony na podstawie oficjalnych harmonogramów PGK dla zabudowy jednorodzinnej na 2026 r.
STREET_RULES = {
    "KILIŃSKIEGO": {
        "mixed": {"region": "I", "nr": 1},
        "selective": {"region": "V", "nr": 5},
        "bio": {"region": "IX", "nr": 3},
    }
}

TYPE_LABELS = {
    "mixed": "odpady zmieszane",
    "selective": "worki selektywne",
    "bio": "bioodpady / odpady zielone",
}

TYPE_DESCRIPTIONS = {
    "mixed": "Pojemnik z odpadami zmieszanymi wystaw wieczorem albo rano przed odbiorem.",
    "selective": "Przy workach część terminów może też dotyczyć tekstyliów i gabarytów po zgłoszeniu, zgodnie z opisem PGK.",
    "bio": "Dotyczy bioodpadów i odpadów zielonych zgodnie z harmonogramem PGK.",
}


@dataclass
class PickupEvent:
    waste_type: str
    pickup_date: date
    source_url: str
    row_label: str


class PGKError(Exception):
    pass


def normalize_street_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper())


def fetch_pdf_text(url: str) -> str:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    reader = PdfReader(BytesIO(response.content))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    text = "\n".join(pages)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def parse_row_dates(pdf_text: str, row_nr: int) -> List[date]:
    pattern = re.compile(
        rf"Nr\s*{row_nr}\b(.*?)(?=Nr\s*{row_nr + 1}\b|Warunkiem odbioru|Napełnione worki|ciąg dalszy harmonogramu|$)",
        re.S | re.I,
    )
    match = pattern.search(pdf_text)
    if not match:
        raise PGKError(f"Nie udało się znaleźć wiersza Nr {row_nr} w PDF.")

    block = match.group(1)
    nums = [int(x) for x in re.findall(r"\b\d{1,2}\b", block)]

    if len(nums) >= 20:
        nums = nums[-20:]
        months = [1, 2, 3] + [4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10] + [11, 12]
    elif len(nums) >= 12:
        nums = nums[-12:]
        months = MONTHS
    else:
        raise PGKError(f"Wiersz Nr {row_nr} znaleziony, ale liczba dat wygląda podejrzanie: {nums}")

    return [date(YEAR, month, day) for month, day in zip(months, nums)]


def load_events_for_street(street: str) -> List[PickupEvent]:
    street_key = normalize_street_name(street)
    if street_key not in STREET_RULES:
        raise PGKError(f"Brak zdefiniowanego mapowania dla ulicy: {street}")

    rules = STREET_RULES[street_key]
    all_events: List[PickupEvent] = []

    for waste_type, rule in rules.items():
        pdf_text = fetch_pdf_text(PDF_URLS[waste_type])
        dates = parse_row_dates(pdf_text, rule["nr"])
        for pickup_day in dates:
            all_events.append(
                PickupEvent(
                    waste_type=waste_type,
                    pickup_date=pickup_day,
                    source_url=PDF_URLS[waste_type],
                    row_label=f"Rejon {rule['region']} / Nr {rule['nr']}",
                )
            )

    return sorted(all_events, key=lambda e: e.pickup_date)


def build_subject(events: List[PickupEvent], street: str, notify_for: date) -> str:
    kinds = " + ".join(TYPE_LABELS[event.waste_type] for event in events)
    return f"PGK Radomsko: jutro odbiór — {kinds} — ul. {street} ({notify_for:%d.%m.%Y})"


def build_plain_text(events: List[PickupEvent], street: str, notify_for: date) -> str:
    labels = [TYPE_LABELS[event.waste_type] for event in events]
    lines = [
        f"Przypomnienie: jutro ({notify_for:%d.%m.%Y}) jest odbiór odpadów dla ul. {street}.",
        "",
        f"Do wystawienia: {', '.join(labels)}.",
        "",
        "Szczegóły:",
    ]

    for event in events:
        lines.append(f"- {TYPE_LABELS[event.waste_type]}")
        lines.append(f"  Termin: {event.pickup_date:%d.%m.%Y}")
        lines.append(f"  Rejon: {event.row_label}")
        lines.append(f"  Uwagi: {TYPE_DESCRIPTIONS[event.waste_type]}")
        lines.append(f"  Źródło: {event.source_url}")

    lines.extend(
        [
            "",
            "To automatyczne przypomnienie zostało wygenerowane na podstawie harmonogramu PGK Radomsko.",
            "Założenie: zabudowa jednorodzinna dla ul. Kilińskiego.",
        ]
    )
    return "\n".join(lines)


def build_html(events: List[PickupEvent], street: str, notify_for: date) -> str:
    items = []
    for event in events:
        items.append(
            """
            <li style=\"margin-bottom:12px;\">
              <strong>{label}</strong><br>
              Termin: {date}<br>
              Rejon: {row}<br>
              Uwagi: {note}<br>
              Źródło: <a href=\"{url}\">harmonogram PGK</a>
            </li>
            """.format(
                label=html.escape(TYPE_LABELS[event.waste_type]),
                date=event.pickup_date.strftime("%d.%m.%Y"),
                row=html.escape(event.row_label),
                note=html.escape(TYPE_DESCRIPTIONS[event.waste_type]),
                url=html.escape(event.source_url),
            )
        )

    labels = ", ".join(TYPE_LABELS[event.waste_type] for event in events)
    return f"""\
<!DOCTYPE html>
<html lang=\"pl\">
  <body style=\"font-family: Arial, sans-serif; line-height: 1.5; color: #222;\">
    <p><strong>Przypomnienie:</strong> jutro ({notify_for:%d.%m.%Y}) jest odbiór odpadów dla ul. {html.escape(street)}.</p>
    <p><strong>Do wystawienia:</strong> {html.escape(labels)}.</p>
    <ul>
      {''.join(items)}
    </ul>
    <p style=\"font-size: 14px; color: #555;\">
      To automatyczne przypomnienie zostało wygenerowane na podstawie harmonogramu PGK Radomsko.<br>
      Założenie: zabudowa jednorodzinna dla ul. Kilińskiego.
    </p>
  </body>
</html>
"""


def build_email(events: List[PickupEvent], street: str) -> EmailMessage:
    sender = os.environ["SMTP_USERNAME"]
    recipient = os.environ["NOTIFY_EMAIL_TO"]
    notify_for = min(event.pickup_date for event in events)

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = build_subject(events, street, notify_for)

    plain_text = build_plain_text(events, street, notify_for)
    html_text = build_html(events, street, notify_for)

    message.set_content(plain_text)
    message.add_alternative(html_text, subtype="html")
    return message


def send_email(message: EmailMessage) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

    if use_tls:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port) as server:
            server.starttls(context=context)
            server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(username, password)
            server.send_message(message)


def main() -> int:
    street = os.environ.get("STREET_NAME", "Kilińskiego")
    dry_run = "--dry-run" in sys.argv
    target_date_env = os.environ.get("TARGET_DATE")
    today = datetime.strptime(target_date_env, "%Y-%m-%d").date() if target_date_env else date.today()
    notify_for = today + timedelta(days=1)

    try:
        events = load_events_for_street(street)
    except Exception as exc:
        print(f"Błąd podczas pobierania harmonogramu: {exc}", file=sys.stderr)
        return 1

    matching = [event for event in events if event.pickup_date == notify_for]
    if not matching:
        print(f"Brak odbioru dla ul. {street} na dzień {notify_for:%Y-%m-%d}.")
        return 0

    message = build_email(matching, street)
    if dry_run:
        print(message)
        return 0

    try:
        send_email(message)
    except Exception as exc:
        print(f"Błąd podczas wysyłki maila: {exc}", file=sys.stderr)
        return 2

    print(f"Wysłano przypomnienie dla {street} na {notify_for:%Y-%m-%d}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
