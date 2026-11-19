"""Optional delivery adapters. Configure credentials in .env before enabling live sends."""
import os
from dotenv import load_dotenv
load_dotenv()


def send_email(to_email, subject, body):
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = to_email

    msg.set_content(body)

    msg.add_alternative(body, subtype="html")

    with smtplib.SMTP(
        os.environ["SMTP_HOST"],
        int(os.getenv("SMTP_PORT", "587"))
    ) as smtp:
        smtp.starttls()
        smtp.login(
            os.environ["SMTP_USER"],
            os.environ["SMTP_PASSWORD"]
        )
        smtp.send_message(msg)

def send_sms(to_phone, body):
    from twilio.rest import Client
    client=Client(os.environ["TWILIO_ACCOUNT_SID"],os.environ["TWILIO_AUTH_TOKEN"])
    return client.messages.create(to=to_phone, from_=os.environ["TWILIO_SMS_FROM"], body=body)

def send_whatsapp(to_phone, body):
    from twilio.rest import Client
    client=Client(os.environ["TWILIO_ACCOUNT_SID"],os.environ["TWILIO_AUTH_TOKEN"])
    # WhatsApp sender must be enabled/approved in your Twilio account.
    return client.messages.create(to="whatsapp:"+to_phone, from_=os.environ["TWILIO_WHATSAPP_FROM"], body=body)

def notify_student(user, subject, body):
    """Best-effort sends. Returns per-channel status; WhatsApp templates may be required."""
    results={}
    for channel, enabled, sender in [
        ("email", bool(os.getenv("SMTP_HOST")), lambda: send_email(user.email, subject, body)),
        ("sms", bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_SMS_FROM")), lambda: send_sms(user.phone, body)),
        ("whatsapp", bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_WHATSAPP_FROM")), lambda: send_whatsapp(user.phone, body)),
    ]:
        if not enabled or not user.phone and channel != "email":
            results[channel]="not_configured"
            continue
        try:
            sender(); results[channel]="submitted"
        except Exception as exc:
            results[channel]="failed: "+str(exc)
    return results
