import os
import sys
import queue
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

APP_DIR = Path(__file__).resolve().parent
CLI_SCRIPT = APP_DIR / "yanni_email_app.py"

DEFAULT_OUTPUT = r"C:\Users\buckl\OneDrive\Content\Yanni Email Project\Test Folder"
DEFAULT_ACCOUNT = "tslegalaction@gmail.com"
DEFAULT_QUERY = "has:attachment filename:pdf newer_than:30d"
DEFAULT_CREDENTIALS = "credentials.json"
DEFAULT_TOKEN = "token-tslegalaction.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def resolve_local_path(value):
    path = Path(value)
    if path.is_absolute():
        return path
    return APP_DIR / path


class YanniGuiApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Yanni Email PDF Organizer")
        self.root.geometry("900x650")

        self.log_queue = queue.Queue()
        self.running = False

        self.expected_account = tk.StringVar(value=DEFAULT_ACCOUNT)
        self.gmail_query = tk.StringVar(value=DEFAULT_QUERY)
        self.max_emails = tk.StringVar(value="1")
        self.output_folder = tk.StringVar(value=DEFAULT_OUTPUT)
        self.credentials_file = tk.StringVar(value=DEFAULT_CREDENTIALS)
        self.token_file = tk.StringVar(value=DEFAULT_TOKEN)
        self.connected_account = tk.StringVar(value="Not checked yet")
        self.mode_status = tk.StringVar(value="Mode: Gmail read-only / local PDF download only")

        self.build_ui()
        self.poll_log_queue()

    def build_ui(self):
        pad = {"padx": 10, "pady": 5}

        title = tk.Label(
            self.root,
            text="Yanni Email PDF Organizer",
            font=("Segoe UI", 18, "bold")
        )
        title.pack(anchor="w", padx=12, pady=(12, 4))

        mode = tk.Label(
            self.root,
            textvariable=self.mode_status,
            fg="green",
            font=("Segoe UI", 10, "bold")
        )
        mode.pack(anchor="w", padx=12)

        account_frame = tk.LabelFrame(self.root, text="Gmail Account Safety Check")
        account_frame.pack(fill="x", **pad)

        tk.Label(account_frame, text="Expected Gmail account:").grid(row=0, column=0, sticky="w", **pad)
        tk.Entry(account_frame, textvariable=self.expected_account, width=45).grid(row=0, column=1, sticky="w", **pad)

        tk.Label(account_frame, text="Connected Gmail account:").grid(row=1, column=0, sticky="w", **pad)
        tk.Label(account_frame, textvariable=self.connected_account, fg="blue").grid(row=1, column=1, sticky="w", **pad)

        tk.Button(account_frame, text="Check Connected Gmail", command=self.check_connected_gmail).grid(row=0, column=2, rowspan=2, **pad)
        tk.Button(account_frame, text="Reset Gmail Token", command=self.reset_token).grid(row=0, column=3, rowspan=2, **pad)

        settings_frame = tk.LabelFrame(self.root, text="Run Settings")
        settings_frame.pack(fill="x", **pad)

        tk.Label(settings_frame, text="Gmail search query:").grid(row=0, column=0, sticky="w", **pad)
        tk.Entry(settings_frame, textvariable=self.gmail_query, width=80).grid(row=0, column=1, columnspan=3, sticky="we", **pad)

        tk.Label(settings_frame, text="Max emails:").grid(row=1, column=0, sticky="w", **pad)
        tk.Entry(settings_frame, textvariable=self.max_emails, width=10).grid(row=1, column=1, sticky="w", **pad)

        tk.Label(settings_frame, text="Output folder:").grid(row=2, column=0, sticky="w", **pad)
        tk.Entry(settings_frame, textvariable=self.output_folder, width=65).grid(row=2, column=1, columnspan=2, sticky="we", **pad)
        tk.Button(settings_frame, text="Choose Folder", command=self.choose_output_folder).grid(row=2, column=3, **pad)

        tk.Label(settings_frame, text="Credentials file:").grid(row=3, column=0, sticky="w", **pad)
        tk.Entry(settings_frame, textvariable=self.credentials_file, width=35).grid(row=3, column=1, sticky="w", **pad)

        tk.Label(settings_frame, text="Token file:").grid(row=3, column=2, sticky="e", **pad)
        tk.Entry(settings_frame, textvariable=self.token_file, width=30).grid(row=3, column=3, sticky="w", **pad)

        button_frame = tk.Frame(self.root)
        button_frame.pack(fill="x", padx=10, pady=8)

        tk.Button(button_frame, text="Run 1 Email Test", height=2, command=lambda: self.run_import(1)).pack(side="left", padx=5)
        tk.Button(button_frame, text="Run 10 Email Test", height=2, command=lambda: self.run_import(10)).pack(side="left", padx=5)
        tk.Button(button_frame, text="Run Custom Amount", height=2, command=self.run_custom).pack(side="left", padx=5)
        tk.Button(button_frame, text="Open Output Folder", height=2, command=self.open_output_folder).pack(side="left", padx=5)
        tk.Button(button_frame, text="Clear Log", height=2, command=self.clear_log).pack(side="left", padx=5)

        log_frame = tk.LabelFrame(self.root, text="App Log")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log_box = scrolledtext.ScrolledText(log_frame, wrap="word", height=18)
        self.log_box.pack(fill="both", expand=True, padx=5, pady=5)

        self.log("Ready.")
        self.log("This GUI calls your existing yanni_email_app.py file.")
        self.log("Gmail scope is read-only. Files are downloaded locally to the output folder.")

    def log(self, message):
        self.log_queue.put(str(message))

    def poll_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_box.insert("end", message + "\n")
                self.log_box.see("end")
        except queue.Empty:
            pass
        self.root.after(100, self.poll_log_queue)

    def clear_log(self):
        self.log_box.delete("1.0", "end")

    def choose_output_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_folder.get())
        if folder:
            self.output_folder.set(folder)

    def open_output_folder(self):
        folder = Path(self.output_folder.get())
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def reset_token(self):
        token_path = resolve_local_path(self.token_file.get())
        if token_path.exists():
            token_path.unlink()
            self.connected_account.set("Token removed. Run again to sign into Gmail.")
            self.log(f"Removed token file: {token_path}")
            messagebox.showinfo("Token removed", "The Gmail token was removed. Next run will ask you to sign in again.")
        else:
            self.log("No token file found to remove.")
            messagebox.showinfo("No token found", "No token file was found.")

    def check_connected_gmail(self):
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            token_path = resolve_local_path(self.token_file.get())

            if not token_path.exists():
                self.connected_account.set("No token found")
                self.log("No token found. Run a test first to sign into Gmail.")
                return

            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")

            service = build("gmail", "v1", credentials=creds)
            profile = service.users().getProfile(userId="me").execute()
            email = profile.get("emailAddress", "Unknown")

            self.connected_account.set(email)
            self.log(f"Connected Gmail account: {email}")

            expected = self.expected_account.get().strip().lower()
            if expected and email.lower() != expected:
                messagebox.showwarning(
                    "Wrong Gmail account",
                    f"Connected: {email}\nExpected: {self.expected_account.get()}"
                )
            else:
                messagebox.showinfo("Gmail account verified", f"Connected to:\n{email}")

        except Exception as e:
            self.connected_account.set("Check failed")
            self.log(f"Could not check connected Gmail: {e}")
            messagebox.showerror("Check failed", str(e))

    def run_custom(self):
        try:
            limit = int(self.max_emails.get())
            if limit < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid number", "Max emails must be a number greater than 0.")
            return

        self.run_import(limit)

    def run_import(self, limit):
        if self.running:
            messagebox.showwarning("Already running", "The app is already running.")
            return

        if not CLI_SCRIPT.exists():
            messagebox.showerror("Missing file", f"Could not find:\n{CLI_SCRIPT}")
            return

        expected = self.expected_account.get().strip()
        output = self.output_folder.get().strip()

        if not expected:
            messagebox.showerror("Missing Gmail account", "Enter the expected Gmail account first.")
            return

        confirm = messagebox.askyesno(
            "Confirm Gmail Read",
            f"This will read Gmail using read-only access.\n\n"
            f"Expected account:\n{expected}\n\n"
            f"Maximum emails:\n{limit}\n\n"
            f"Output folder:\n{output}\n\n"
            f"Continue?"
        )

        if not confirm:
            self.log("Run cancelled before starting.")
            return

        worker = threading.Thread(target=self.run_worker, args=(limit,), daemon=True)
        worker.start()

    def run_worker(self, limit):
        self.running = True

        try:
            output_path = Path(self.output_folder.get())
            output_path.mkdir(parents=True, exist_ok=True)

            credentials_path = resolve_local_path(self.credentials_file.get())
            token_path = resolve_local_path(self.token_file.get())

            cmd = [
                sys.executable,
                str(CLI_SCRIPT),
                "--mode", "real",
                "--confirm-real",
                "--credentials", str(credentials_path),
                "--token", str(token_path),
                "--gmail-query", self.gmail_query.get(),
                "--max-emails", str(limit),
                "--expected-account", self.expected_account.get(),
                "--output", str(output_path),
            ]

            self.log("")
            self.log("Starting Yanni Email app...")
            self.log("Command:")
            self.log(subprocess.list2cmdline(cmd))
            self.log("")

            process = subprocess.Popen(
                cmd,
                cwd=str(APP_DIR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            try:
                process.stdin.write("YES\n")
                process.stdin.flush()
            except Exception:
                pass

            for line in process.stdout:
                self.log(line.rstrip())

            return_code = process.wait()

            if return_code == 0:
                self.log("")
                self.log("Finished successfully.")
            else:
                self.log("")
                self.log(f"Finished with error code: {return_code}")

        except Exception as e:
            self.log(f"Run failed: {e}")

        finally:
            self.running = False


if __name__ == "__main__":
    root = tk.Tk()
    app = YanniGuiApp(root)
    root.mainloop()
