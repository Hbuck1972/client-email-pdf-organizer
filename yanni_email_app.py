import argparse
import base64
import json
import os
import re
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from dataclasses import dataclass, asdict
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Dict, Tuple

# Gmail API imports
# Install with:
# pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# -----------------------------
# Data Models
# -----------------------------

@dataclass
class TimeshareRecord:
    client_name: str
    owner_name: str
    resort_name: str
    contract_number: str
    source_email_subject: str
    source_email_date: str
    gmail_message_id: str


# -----------------------------
# Utility Functions
# -----------------------------

def safe_filename(value: str, max_len: int = 120) -> str:
    """
    Makes text safe for Windows folder/file names.
    """
    if not value:
        return "UNKNOWN"

    value = value.strip()
    value = re.sub(r'[<>:"/\\|?*]', "_", value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" .")

    if len(value) > max_len:
        value = value[:max_len].rstrip()

    return value or "UNKNOWN"


def decode_base64url(data: str) -> bytes:
    """
    Decodes Gmail base64url attachment/body data.
    """
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def get_header(headers: List[Dict], name: str) -> str:
    """
    Pulls a header value from a Gmail message.
    """
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def extract_text_from_payload(payload: Dict) -> str:
    """
    Extracts plain text from Gmail message payload.
    """
    text_parts = []

    def walk(part):
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})

        if mime_type == "text/plain" and body.get("data"):
            try:
                text_parts.append(decode_base64url(body["data"]).decode("utf-8", errors="replace"))
            except Exception:
                pass

        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return "\n".join(text_parts).strip()


# -----------------------------
# Extraction Logic
# -----------------------------

def find_first(patterns: List[str], text: str) -> str:
    """
    Returns first regex group match from a list of patterns.
    """
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return ""


def extract_client_name(text: str, subject: str) -> str:
    combined = subject + "\n" + text

    return find_first([
        r"Client(?: Name)?\s*:\s*(.+)",
        r"Client\s*-\s*(.+)",
        r"Regarding Client\s*:\s*(.+)",
    ], combined) or "UNKNOWN CLIENT"


def extract_owner_name(text: str, subject: str) -> str:
    combined = subject + "\n" + text

    return find_first([
        r"Owner(?: Name)?\s*:\s*(.+)",
        r"Owners?\s*:\s*(.+)",
        r"Seller(?:s)?\s*:\s*(.+)",
        r"Buyer(?:s)?\s*:\s*(.+)",
    ], combined) or "UNKNOWN OWNER"


def extract_contract_number(text: str, filename: str = "") -> str:
    combined = filename + "\n" + text

    return find_first([
        r"Contract(?: Number| No\.?| #)?\s*:\s*([A-Za-z0-9\-]+)",
        r"Account(?: Number| No\.?| #)?\s*:\s*([A-Za-z0-9\-]+)",
        r"Member(?: Number| No\.?| #)?\s*:\s*([A-Za-z0-9\-]+)",
    ], combined) or "UNKNOWN CONTRACT"


def extract_resort_names(text: str, subject: str, filenames: List[str]) -> List[str]:
    """
    Attempts to find one or more timeshare/resort names.

    Important rule:
    If two different timeshares are mentioned, the app should create
    two separate folders and separate documents accordingly.
    """
    combined = subject + "\n" + text + "\n" + "\n".join(filenames)

    resort_patterns = [
        r"Resort(?: Name)?\s*:\s*(.+)",
        r"Property(?: Name)?\s*:\s*(.+)",
        r"Timeshare(?: Name)?\s*:\s*(.+)",
    ]

    found = []

    for pattern in resort_patterns:
        matches = re.findall(pattern, combined, re.IGNORECASE | re.MULTILINE)
        for item in matches:
            cleaned = item.strip()
            cleaned = re.split(r"\s{2,}|, Contract|, Account| Contract:| Account:", cleaned)[0].strip()
            if cleaned and cleaned.upper() not in [x.upper() for x in found]:
                found.append(cleaned)

    # Fallback hints from common abbreviations / filenames
    known_resorts = {
        "ACA": "ACA",
        "Wyndham": "Wyndham",
        "Diamond": "Diamond Resorts",
        "Hilton": "Hilton Grand Vacations",
        "Marriott": "Marriott Vacation Club",
        "Westgate": "Westgate Resorts",
        "Bluegreen": "Bluegreen Vacations",
    }

    for key, resort in known_resorts.items():
        if re.search(rf"\b{re.escape(key)}\b", combined, re.IGNORECASE):
            if resort.upper() not in [x.upper() for x in found]:
                found.append(resort)

    if not found:
        found.append("UNKNOWN TIMESHARE")

    return found


def build_timeshare_records(
    text: str,
    subject: str,
    date_value: str,
    gmail_message_id: str,
    attachment_names: List[str]
) -> List[TimeshareRecord]:

    client_name = extract_client_name(text, subject)
    owner_name = extract_owner_name(text, subject)
    contract_number = extract_contract_number(subject + '\n' + text)
    resorts = extract_resort_names(text, subject, attachment_names)

    records = []

    for resort in resorts:
        records.append(
            TimeshareRecord(
                client_name=client_name,
                owner_name=owner_name,
                resort_name=resort,
                contract_number=contract_number,
                source_email_subject=subject,
                source_email_date=date_value,
                gmail_message_id=gmail_message_id,
            )
        )

    return records


# -----------------------------
# Gmail Access
# -----------------------------

def get_gmail_service(credentials_path: str, token_path: str):
    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"Missing credentials file: {credentials_path}\n"
                    "Place your Google OAuth credentials JSON file in the app folder."
                )

            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_connected_gmail_account(service) -> str:
    """
    Returns the Gmail account currently authorized by token.json.
    """
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "UNKNOWN ACCOUNT")


def confirm_correct_gmail_account(connected_email: str, expected_account: str = ""):
    """
    Safety check before reading real Gmail messages.
    """
    print()
    print("======================================")
    print("GMAIL ACCOUNT SAFETY CHECK")
    print("======================================")
    print(f"Connected Gmail account: {connected_email}")

    if expected_account:
        print(f"Expected Gmail account:  {expected_account}")

        if connected_email.lower() != expected_account.lower():
            raise RuntimeError(
                "Wrong Gmail account connected. Stop now.\n"
                f"Connected: {connected_email}\n"
                f"Expected:  {expected_account}\n\n"
                "To switch accounts, delete token.json or use a different --token file."
            )

    print()
    answer = input("Type YES to continue reading this Gmail account: ").strip()

    if answer != "YES":
        raise RuntimeError("User cancelled before Gmail processing started.")


def search_gmail_messages(service, query: str, max_results: int) -> List[str]:
    message_ids = []
    next_page_token = None

    while len(message_ids) < max_results:
        response = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=min(100, max_results - len(message_ids)),
            pageToken=next_page_token
        ).execute()

        for msg in response.get("messages", []):
            message_ids.append(msg["id"])

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    return message_ids


def read_gmail_message(service, message_id: str) -> Dict:
    return service.users().messages().get(
        userId="me",
        id=message_id,
        format="full"
    ).execute()


def collect_attachments_from_payload(payload: Dict) -> List[Tuple[str, str, str]]:
    """
    Returns list of:
    filename, attachment_id, mime_type
    """
    attachments = []

    def walk(part):
        filename = part.get("filename")
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")
        mime_type = part.get("mimeType", "")

        if filename and attachment_id:
            attachments.append((filename, attachment_id, mime_type))

        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return attachments


def download_attachment(service, message_id: str, attachment_id: str) -> bytes:
    attachment = service.users().messages().attachments().get(
        userId="me",
        messageId=message_id,
        id=attachment_id
    ).execute()

    return decode_base64url(attachment["data"])


# -----------------------------
# Output Creation
# -----------------------------

def make_record_folder(base_output: Path, record: TimeshareRecord) -> Path:
    folder_name = safe_filename(
        f"{record.client_name} - {record.owner_name} - {record.resort_name} - {record.contract_number}"
    )

    folder_path = base_output / folder_name
    folder_path.mkdir(parents=True, exist_ok=True)
    return folder_path



# --- YANNI SUBJECT PARSER OVERRIDES START ---

def clean_subject_for_parsing(subject: str) -> str:
    s = subject or ""
    s = re.sub(r"^\s*(re|fw|fwd)\s*:\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def title_keep_ampersand(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" -:_[]()")
    small_words = {"and", "or", "of", "at", "the"}

    parts = []
    for token in value.split(" "):
        if token == "&":
            parts.append(token)
        elif token.lower() in small_words:
            parts.append(token.lower())
        elif token.isupper() and len(token) <= 4:
            parts.append(token)
        else:
            parts.append(token[:1].upper() + token[1:].lower())

    return " ".join(parts).strip()


def format_owner_last_first(name: str) -> str:
    name = re.sub(r"\s+", " ", name or "").strip(" -:_[]()")
    name = re.sub(r"\b(owner|client)\b\s*:?", "", name, flags=re.IGNORECASE).strip()

    if not name:
        return "UNKNOWN OWNER"

    if "," in name:
        left, right = name.split(",", 1)
        return f"{title_keep_ampersand(left)}, {title_keep_ampersand(right)}".strip()

    words = name.split()
    if len(words) >= 2:
        last = words[-1]
        first = " ".join(words[:-1])
        return f"{title_keep_ampersand(last)}, {title_keep_ampersand(first)}"

    return title_keep_ampersand(name)


def extract_owner_from_new_file_subject(subject: str) -> str:
    s = clean_subject_for_parsing(subject)

    m = re.search(r"\bNew\s*File\b\s*[-:]*\s*(.+)", s, flags=re.IGNORECASE)
    if not m:
        return ""

    chunk = m.group(1).strip()

    stop_patterns = [
        r"\[",
        r"\bWyndham\b",
        r"\bBluegreen\b",
        r"\bGrand Lodge\b",
        r"\bVacation Village\b",
        r"\bVillas At Regal Palm[s]?\b",
        r"\bFrench Quarter\b",
        r"\bSpinnaker\b",
        r"\bHilton\b",
        r"\bWestgate\b",
        r"\bMarriott\b",
        r"\bDiamond\b",
        r"\bACA\b",
        r"\bCONGRATULATIONS\b",
        r"\bFOLLOW INSTRUCTIONS\b",
        r"\bLETTER OF EXPLANATION\b",
        r"\b\d{5,}\b",
    ]

    first_stop = len(chunk)
    for pat in stop_patterns:
        sm = re.search(pat, chunk, flags=re.IGNORECASE)
        if sm:
            first_stop = min(first_stop, sm.start())

    return chunk[:first_stop].strip(" -:_[]()")


def extract_owner_from_owner_id_subject(subject: str) -> str:
    s = clean_subject_for_parsing(subject)
    m = re.match(r"(.+?)-\s*Owner\s*ID\b", s, flags=re.IGNORECASE)
    if not m:
        return ""
    return m.group(1).strip()


def extract_client_name(text: str, subject: str) -> str:
    combined = f"{subject}\n{text}"

    if re.search(r"\bACA\b", combined, flags=re.IGNORECASE):
        return "ACA"

    if re.search(r"\bNew\s*File\b", subject or "", flags=re.IGNORECASE):
        return "ACA"

    return "UNKNOWN CLIENT"


def extract_owner_name(text: str, subject: str) -> str:
    combined = f"{subject}\n{text}"

    owner = extract_owner_from_owner_id_subject(subject)
    if owner:
        return format_owner_last_first(owner)

    owner = extract_owner_from_new_file_subject(subject)
    if owner:
        return format_owner_last_first(owner)

    body_patterns = [
        r"Owner(?:\s+Name)?\s*[:\-]\s*([A-Za-z ,.&'\-]+)",
        r"Purchaser(?:\s+Name)?\s*[:\-]\s*([A-Za-z ,.&'\-]+)",
        r"Client(?:\s+Name)?\s*[:\-]\s*([A-Za-z ,.&'\-]+)",
    ]

    for pat in body_patterns:
        m = re.search(pat, combined, flags=re.IGNORECASE)
        if m:
            value = m.group(1).strip().splitlines()[0].strip()
            if value and not re.search(r"\bACA\b", value, flags=re.IGNORECASE):
                return format_owner_last_first(value)

    return "UNKNOWN OWNER"


def extract_contract_number(text: str) -> str:
    combined = text or ""

    patterns = [
        r"Owner\s*ID\s*[:#\-]?\s*(\d{4,})",
        r"Contract(?:\s+Number| #| No\.?)?\s*[:#\-]?\s*(\d{4,})",
        r"Account(?:\s+Number| #| No\.?)?\s*[:#\-]?\s*(\d{4,})",
        r"\[[^\]]*-\s*(\d{4,})\]",
        r"\b(\d{5,})\b",
    ]

    for pat in patterns:
        m = re.search(pat, combined, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()

    return "UNKNOWN CONTRACT"


def extract_resort_names(text: str, subject: str, filenames: List[str]) -> List[str]:
    combined = subject + "\n" + text + "\n" + "\n".join(filenames)

    known_resorts = {
        "Wyndham": "Wyndham",
        "Club Wyndham": "Wyndham",
        "Bluegreen": "Bluegreen Vacations",
        "Bluegreen Vacations": "Bluegreen Vacations",
        "Spinnaker": "Spinnaker",
        "Hilton Grand Vacations": "Hilton Grand Vacations",
        "Hilton": "Hilton Grand Vacations",
        "French Quarter": "French Quarter",
        "Vacation Village": "Vacation Village",
        "Grand Lodge": "Grand Lodge",
        "Villas At Regal Palm": "Villas At Regal Palm",
        "Villas At Regal Palms": "Villas At Regal Palm",
        "Westgate": "Westgate Resorts",
        "Marriott": "Marriott",
        "Diamond Resorts": "Diamond Resorts",
        "Diamond": "Diamond Resorts",
    }

    found = []

    # Bracket format: [Bluegreen -2813075]
    for bracket in re.findall(r"\[([^\]]+)\]", combined):
        left = bracket.split("-")[0].strip()
        for key, resort in known_resorts.items():
            if re.search(rf"\b{re.escape(key)}\b", left, re.IGNORECASE):
                if resort.upper() not in [x.upper() for x in found]:
                    found.append(resort)

    for key, resort in known_resorts.items():
        if re.search(rf"\b{re.escape(key)}\b", combined, re.IGNORECASE):
            if resort.upper() not in [x.upper() for x in found]:
                found.append(resort)

    if not found:
        found.append("UNKNOWN TIMESHARE")

    return found

# --- YANNI SUBJECT PARSER OVERRIDES END ---


def write_summary_files(folder: Path, record: TimeshareRecord, body_text: str):
    summary_path = folder / "00_CLIENT_SUMMARY.txt"
    source_path = folder / "00_SOURCE_EMAIL_BODY.txt"

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("CLIENT SUMMARY\n")
        f.write("====================\n\n")
        f.write(f"Client Name: {record.client_name}\n")
        f.write(f"Owner Name: {record.owner_name}\n")
        f.write(f"Timeshare / Resort: {record.resort_name}\n")
        f.write(f"Contract Number: {record.contract_number}\n")
        f.write(f"Email Subject: {record.source_email_subject}\n")
        f.write(f"Email Date: {record.source_email_date}\n")
        f.write(f"Gmail Message ID: {record.gmail_message_id}\n")

    with open(source_path, "w", encoding="utf-8") as f:
        f.write(body_text)


def save_manifest(base_output: Path, records: List[TimeshareRecord]):
    manifest_path = base_output / "00_RUN_MANIFEST.json"

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in records], f, indent=2)

def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 2
    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def write_pdf_pages(reader: PdfReader, page_indexes: List[int], output_path: Path):
    writer = PdfWriter()

    for page_index in page_indexes:
        writer.add_page(reader.pages[page_index])

    output_path = unique_path(output_path)

    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path


def split_spoa_sdd_pdf_if_needed(folder: Path, attachment_filename: str, attachment_bytes: bytes, record: TimeshareRecord) -> bool:
    if not attachment_filename.lower().endswith(".pdf"):
        return False

    try:
        reader = PdfReader(BytesIO(attachment_bytes))
    except Exception:
        return False

    page_texts = []

    for page in reader.pages:
        try:
            page_texts.append(page.extract_text() or "")
        except Exception:
            page_texts.append("")

    spoa_start = None
    sdd_start = None
    certificate_start = None

    for i, text in enumerate(page_texts):
        upper = text.upper()

        if spoa_start is None and "SPECIAL POWER OF ATTORNEY" in upper:
            spoa_start = i

        if sdd_start is None and "STRATEGIC DEFAULT DISCLOSURE" in upper:
            sdd_start = i

        if certificate_start is None and (
            "CERTIFICATE OF COMPLETION" in upper
            or "ELECTRONIC RECORD AND SIGNATURE DISCLOSURE" in upper
        ):
            certificate_start = i

    if spoa_start is None or sdd_start is None:
        return False

    prefix = safe_filename(f"{record.owner_name} - {record.resort_name}")

    # SPOA pages: from SPOA start up to page before SDD
    spoa_pages = list(range(spoa_start, sdd_start))

    # SDD pages: from SDD start up to certificate/disclosure pages, or end of PDF
    sdd_end = certificate_start if certificate_start is not None and certificate_start > sdd_start else len(reader.pages)
    sdd_pages = list(range(sdd_start, sdd_end))

    if spoa_pages:
        spoa_name = f"{prefix} - SPOA - Special Power of Attorney.pdf"
        spoa_path = folder / safe_filename(spoa_name)
        saved_spoa = write_pdf_pages(reader, spoa_pages, spoa_path)
        print(f"Split SPOA PDF: {saved_spoa.name}")

    if sdd_pages:
        sdd_name = f"{prefix} - SDD - Strategic Default Disclosure.pdf"
        sdd_path = folder / safe_filename(sdd_name)
        saved_sdd = write_pdf_pages(reader, sdd_pages, sdd_path)
        print(f"Split SDD PDF: {saved_sdd.name}")

    return True

def copy_attachment_to_record_folder(
    folder: Path,
    attachment_filename: str,
    attachment_bytes: bytes,
    record: TimeshareRecord
):
    did_split = split_spoa_sdd_pdf_if_needed(
        folder=folder,
        attachment_filename=attachment_filename,
        attachment_bytes=attachment_bytes,
        record=record
    )

    if did_split:
        return

    original_name = safe_filename(attachment_filename)
    prefix = safe_filename(f"{record.owner_name} - {record.resort_name}")

    final_name = f"{prefix} - {original_name}"
    final_path = unique_path(folder / final_name)

    with open(final_path, "wb") as f:
        f.write(attachment_bytes)

# -----------------------------
# Real Gmail Processing
# -----------------------------

def run_real_mode(args):
    if not args.confirm_real:
        raise RuntimeError(
            "Real mode requires --confirm-real so files are not created by accident."
        )

    output_dir = Path(args.output).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    service = get_gmail_service(
        credentials_path=args.credentials,
        token_path=args.token
    )

    connected_email = get_connected_gmail_account(service)

    confirm_correct_gmail_account(
        connected_email=connected_email,
        expected_account=args.expected_account
    )

    message_ids = search_gmail_messages(
        service=service,
        query=args.gmail_query,
        max_results=args.max_emails
    )

    print(f"Found {len(message_ids)} Gmail messages.")

    all_records = []

    for index, message_id in enumerate(message_ids, start=1):
        print(f"Processing email {index}/{len(message_ids)}: {message_id}")

        message = read_gmail_message(service, message_id)
        payload = message.get("payload", {})
        headers = payload.get("headers", [])

        subject = get_header(headers, "Subject")
        date_value = get_header(headers, "Date")

        try:
            parsed_date = parsedate_to_datetime(date_value).isoformat()
        except Exception:
            parsed_date = date_value

        body_text = extract_text_from_payload(payload)
        attachments = collect_attachments_from_payload(payload)
        attachment_names = [a[0] for a in attachments]

        records = build_timeshare_records(
            text=body_text,
            subject=subject,
            date_value=parsed_date,
            gmail_message_id=message_id,
            attachment_names=attachment_names
        )

        all_records.extend(records)

        downloaded_attachments = []

        for filename, attachment_id, mime_type in attachments:
            try:
                content = download_attachment(service, message_id, attachment_id)
                downloaded_attachments.append((filename, content))
            except Exception as e:
                print(f"Could not download attachment {filename}: {e}")

        for record in records:
            folder = make_record_folder(output_dir, record)
            write_summary_files(folder, record, body_text)

            for filename, content in downloaded_attachments:
                copy_attachment_to_record_folder(
                    folder=folder,
                    attachment_filename=filename,
                    attachment_bytes=content,
                    record=record
                )

    save_manifest(output_dir, all_records)

    print()
    print("Done.")
    print(f"Output folder: {output_dir}")


# -----------------------------
# Sandbox Processing
# -----------------------------

SAMPLE_EMAILS = [
    {
        "message_id": "sandbox-message-001",
        "subject": "ACA Deed Package - Rosenthal, Nathan",
        "date": "2026-05-25",
        "body": """
Client Name: Rosenthal, Nathan
Owner Name: Rosenthal, Nathan
Resort Name: ACA
Contract Number: 12345-ACA

Please process the attached deed and maintenance fee documents.
""",
        "attachments": [
            "Rosenthal, Nathan - ACA_Deed.pdf",
            "Maintenance Fee-2026.pdf"
        ]
    },
    {
        "message_id": "sandbox-message-002",
        "subject": "Two timeshares for same client",
        "date": "2026-05-25",
        "body": """
Client Name: Test Client
Owner Name: Test Owner

Resort Name: Wyndham
Contract Number: WYN-111

Resort Name: Hilton Grand Vacations
Contract Number: HGV-222

This sample should create separate folders because two different timeshares are mentioned.
""",
        "attachments": [
            "Wyndham Contract.pdf",
            "Hilton Contract.pdf"
        ]
    }
]


def run_sandbox_mode(args):
    output_dir = Path(args.output).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    all_records = []

    for sample in SAMPLE_EMAILS:
        records = build_timeshare_records(
            text=sample["body"],
            subject=sample["subject"],
            date_value=sample["date"],
            gmail_message_id=sample["message_id"],
            attachment_names=sample["attachments"]
        )

        all_records.extend(records)

        for record in records:
            folder = make_record_folder(output_dir, record)
            write_summary_files(folder, record, sample["body"])

            for attachment_name in sample["attachments"]:
                fake_content = (
                    f"Sandbox placeholder for {attachment_name}\n"
                    f"This file represents an attachment that would be downloaded in real mode.\n"
                ).encode("utf-8")

                copy_attachment_to_record_folder(
                    folder=folder,
                    attachment_filename=attachment_name,
                    attachment_bytes=fake_content,
                    record=record
                )

    save_manifest(output_dir, all_records)

    print()
    print("Sandbox run complete.")
    print(f"Output folder: {output_dir}")


# -----------------------------
# Main
# -----------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Yanni Email App - Gmail email extraction and timeshare folder creation"
    )

    parser.add_argument(
        "--mode",
        choices=["sandbox", "real"],
        default="sandbox",
        help="sandbox creates test files; real reads Gmail and downloads attachments"
    )

    parser.add_argument(
        "--confirm-real",
        action="store_true",
        help="Required when using --mode real"
    )

    parser.add_argument(
        "--output",
        default=r"C:\Users\buckl\OneDrive\Content\Yanni Email Project\Test Folder",
        help="Output folder where client folders will be created"
    )

    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Google OAuth credentials JSON file"
    )

    parser.add_argument(
        "--token",
        default="token.json",
        help="Saved Gmail OAuth token file"
    )

    parser.add_argument(
        "--gmail-query",
        default='has:attachment newer_than:30d',
        help="Gmail search query"
    )

    parser.add_argument(
        "--max-emails",
        type=int,
        default=25,
        help="Maximum number of emails to process"
    )

    parser.add_argument(
        "--expected-account",
        default="",
        help="Optional safety check. If provided, app will stop unless Gmail account matches this email."
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.mode == "sandbox":
        run_sandbox_mode(args)
    elif args.mode == "real":
        run_real_mode(args)
    else:
        raise ValueError(f"Unknown mode: {args.mode}")


if __name__ == "__main__":
    main()

