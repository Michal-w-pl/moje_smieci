# PGK Radomsko notifier

Prosty skrypt w Pythonie, który:
- pobiera aktualne PDF-y harmonogramów z PGK Radomsko,
- sprawdza terminy dla ul. **Kilińskiego**,
- wysyła mail **dzień przed odbiorem**.

## Założenie
Ten wariant jest przygotowany dla **zabudowy jednorodzinnej** dla ul. Kilińskiego.

## Co obsługuje
- odpady zmieszane,
- selektywną zbiórkę w workach,
- bioodpady / odpady zielone.

## Instalacja
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Uzupełnij `.env` danymi SMTP.

## Test działania
Podgląd maila bez wysyłki:
```bash
python app.py --dry-run
```

Test dla konkretnej daty:
```bash
TARGET_DATE=2026-01-11 python app.py --dry-run
```

## Uruchamianie codziennie
Najprościej przez crona, np. codziennie o 08:00:
```bash
0 8 * * * cd /ścieżka/do/pgk-radomsko-notify && /ścieżka/do/.venv/bin/python app.py >> notifier.log 2>&1
```

## Jak to działa
Skrypt pobiera trzy oficjalne PDF-y PGK i parsuje odpowiednie wiersze dla ul. Kilińskiego:
- zmieszane,
- selektywne worki,
- bio / zielone.

Potem porównuje, czy **jutro** występuje odbiór dla któregoś typu odpadów. Jeśli tak, wysyła wiadomość email.

## Uwaga
W harmonogramie dla worków część terminów może dodatkowo obejmować tekstylia, gabaryty, sprzęt RTV/AGD lub opony, ale ich rozróżnienie w samym PDF-ie jest oznaczane formatowaniem (podkreślenie / pogrubienie). Ten prosty wariant traktuje te terminy jako część przypomnienia dla odbioru workowego.


## Wersja całkowicie automatyczna: GitHub Actions
Nie musisz nic uruchamiać ręcznie. Repozytorium może odpalać skrypt samo raz dziennie.

1. Wrzuć ten katalog do repozytorium na GitHub.
2. W repozytorium wejdź w **Settings → Secrets and variables → Actions**.
3. Dodaj sekrety:
   - `STREET_NAME` = `Kilińskiego`
   - `SMTP_HOST`
   - `SMTP_PORT`
   - `SMTP_USE_TLS`
   - `SMTP_USERNAME`
   - `SMTP_PASSWORD`
   - `NOTIFY_EMAIL_TO`
4. Workflow `.github/workflows/notify.yml` uruchomi się codziennie automatycznie.
5. Możesz też uruchomić go ręcznie z zakładki **Actions** przez `workflow_dispatch`.

### Zalety
- zero ręcznego odpalania,
- nie potrzebujesz własnego serwera,
- działa jako zaplanowane zadanie w chmurze.

### Uwaga o godzinie
GitHub Actions używa czasu UTC. W pliku workflow ustawiłem `0 6 * * *`, co zwykle daje poranny start w Polsce. Jeśli chcesz inną godzinę, zmień wartość `cron`.
