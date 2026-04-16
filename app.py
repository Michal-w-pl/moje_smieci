import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv()

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
    "selective": "selektywna zbiórka w workach (papier/szkło/metale i tworzywa; część terminów obejmuje też tekstylia i gabaryty po zgłoszeniu)",
    "bio": "bioodpady / odpady zielone",
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
    # porządki ułatwiające regexy
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def parse_row_dates(pdf_text: str, row_nr: int) -> List[date]:
    # Szuka fragmentu zaczynającego się od "Nr X" i wyciąga 12 lub 20 liczb będących dniami wywozu.
    pattern = re.compile(rf"Nr\s*{row_nr}\b(.*?)(?=Nr\s*{row_nr + 1}\b|Warunkiem odbioru|Napełnione worki|ciąg dalszy harmonogramu|$)", re.S | re.I)
    m = pattern.search(pdf_text)
    if not m:
        raise PGKError(f"Nie udało się znaleźć wiersza Nr {row_nr} w PDF.")

    block = m.group(1)
    nums = [int(x) for x in re.findall(r"\b\d{1,2}\b", block)]

    # W praktyce harmonogramy mają zwykle 12 terminów (1/mies.) albo 20 terminów (2/mies. w części roku).
    if len(nums) >= 20:
        nums = nums[-20:]
        months = [1, 2, 3] + [4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10] + [11, 12]
    elif len(nums) >= 12:
        nums = nums[-12:]
        months = MONTHS
    else:
        raise PGKError(f"Wiersz Nr {row_nr} znaleziony, ale liczba dat wygląda podejrzanie: {nums}")

    events = []
    for month, day in zip(months, nums):
        events.append(date(2026, month, day))
    return events


def load_events_for_street(street: str) -> List[PickupEvent]:
    street_key = normalize_street_name(street)
    if street_key not in STREET_RULES:
        raise PGKError(f"Brak zdefiniowanego mapowania dla ulicy: {street}")

    rules = STREET_RULES[street_key]
    all_events: List[PickupEvent] = []

    for waste_type, rule in rules.items():
        pdf_text = fetch_pdf_text(PDF_URLS[waste_type])
        dates = parse_row_dates(pdf_text, rule["nr"])
        for d in dates:
            all_events.append(
                PickupEvent(
                    waste_type=waste_type,
                    pickup_date=d,
                    source_url=PDF_URLS[waste_type],
                    row_label=f"Rejon {rule['region']} / Nr {rule['nr']}",
                )
            )

    return sorted(all_events, key=lambda e: e.pickup_date)


def build_email(events: List[PickupEvent], street: str) -> EmailMessage:
    msg = EmailMessage()
    sender = os.environ["SMTP_USERNAME"]
    recipient = os.environ["NOTIFY_EMAIL_TO"]
    tomorrow = min(e.pickup_date for e in events)

    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"PGK Radomsko: jutro odbiór odpadów dla ul. {street} ({tomorrow:%d.%m.%Y})"

    lines = [
        f"Przypomnienie: jutro ({tomorrow:%d.%m.%Y}) jest odbiór odpadów dla ul. {street}.",
        "",
        "Zaplanowane odbiory:",
    ]
    for e in events:
        lines.append(f"- {TYPE_LABELS[e.waste_type]} — {e.pickup_date:%d.%m.%Y} ({e.row_label})")
        lines.append(f"  Źródło: {e.source_url}")
    lines.append("")
    lines.append("Uwaga: ten skrypt zakłada zabudowę jednorodzinną dla ul. Kilińskiego.")
    lines.append("W przypadku worków część terminów może też dotyczyć tekstyliów i gabarytów zgodnie z opisem w harmonogramie PGK.")
    msg.set_content("\n".join(lines))
    return msg


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

    matching = [e for e in events if e.pickup_date == notify_for]
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
