import logging
from twilio.rest import Client
from config.settings import settings

logger = logging.getLogger("TwilioClient")

def send_whatsapp_alert(message: str) -> bool:
    """
    Sends a WhatsApp alert via Twilio SMS/WhatsApp Gateway.
    If Twilio environment variables are missing, falls back to logging the message.
    
    Returns:
        bool: True if sent successfully (or mock logged), False if error occurs.
    """
    # Read Twilio settings
    sid = settings.TWILIO_ACCOUNT_SID
    token = settings.TWILIO_AUTH_TOKEN
    from_num = settings.TWILIO_FROM_NUMBER
    to_num = settings.TWILIO_TO_NUMBER

    # Check if credentials are provided. If not, run in mock mode.
    if not all([sid, token, from_num, to_num]):
        logger.info(
            f"[MOCK WHATSAPP NOTIFICATION] Twilio settings not fully configured. "
            f"Alert message: '{message}'"
        )
        # Return True since mock execution is successful
        return True

    try:
        # Enforce that the numbers are formatted for WhatsApp
        if not from_num.startswith("whatsapp:"):
            from_num = f"whatsapp:{from_num}"
        if not to_num.startswith("whatsapp:"):
            to_num = f"whatsapp:{to_num}"

        client = Client(sid, token)
        msg_instance = client.messages.create(
            body=message,
            from_=from_num,
            to=to_num
        )
        logger.info(f"Twilio WhatsApp alert dispatched successfully. Message SID: {msg_instance.sid}")
        return True
    except Exception as e:
        logger.error(f"Failed to dispatch Twilio WhatsApp notification: {e}")
        return False
