import os
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

MY_EMAIL = os.getenv("MY_EMAIL")
APP_PASSWORD = os.getenv("APP_PASSWORD")  # sửa lỗi ở đây

def send_email_to_self(subject: str, body: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = MY_EMAIL
    msg["To"] = MY_EMAIL
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(MY_EMAIL, APP_PASSWORD)
        smtp.send_message(msg)

if __name__ == "__main__":
    send_email_to_self(
        "Test from Python",
        "Hello from my script."
    )