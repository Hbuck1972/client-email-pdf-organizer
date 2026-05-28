# Client Email & PDF Organizer

A workflow automation project designed to process client emails, extract key client and ownership information, and organize related PDF attachments into properly named folders.

## Purpose

This project is being built to reduce manual email and document sorting work. The goal is to help process large batches of client emails, identify important client details, and save attached PDF documents into organized folders.

## Problem This Solves

Manual email processing can be slow and inconsistent when handling many client messages and attachments. This project is designed to help:

* Process 100+ emails at a time
* Extract client names and owner names
* Identify timeshare references
* Save PDF attachments into organized folders
* Create separate folders when multiple timeshares are mentioned
* Keep naming consistent across client files

## Current Features

* Sandbox mode for safe testing
* Real mode for live Gmail processing
* Gmail email search support
* Attachment download handling
* Client summary text file creation
* Source email body text file creation
* Folder creation based on extracted client and timeshare information
* Safety confirmation required before running in real mode

## Important Rule

If two different timeshares are mentioned in the same email, the app should create two separate folders and separate the documents accordingly.

## Current App File

Main Python file:

```text
yanni_email_app.py
```

## Safe Test Command

Use sandbox mode first:

```bat
python yanni_email_app.py --mode sandbox
```

## Real Gmail Processing Command

Real mode requires confirmation:

```bat
python yanni_email_app.py --mode real --confirm-real --max-emails 25
```

## Private Files Not Uploaded

The following files should never be uploaded to GitHub:

```text
credentials.json
token.json
client_secret*.json
.env
```

These files are ignored by `.gitignore`.

## Project Status

Early working checkpoint.

This project is not finished production software yet. Current development focus is on:

* Improving email data extraction accuracy
* Improving PDF attachment matching
* Handling duplicate attachments
* Supporting large email batches
* Making the app easier to run from Windows
