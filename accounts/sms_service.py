import requests
from django.conf import settings
from .models import SmsLog

def format_ghana_number(phone):
    if not phone:
        return None
    p = str(phone).strip().replace(" ", "").replace("-", "")
    if p.startswith('+'):
        p = p[1:]
    if p.startswith('0'):
        p = '233' + p[1:]
    if not p.startswith('233'):
        if len(p) == 9: # 244123456
            p = '233' + p
    return p

def send_sms_to_school(school, phone_numbers, message):
    """
    school = School object
    phone_numbers = list like ['0244123456', '020...']
    """
    # Clean numbers
    cleaned = []
    for num in phone_numbers:
        fmt = format_ghana_number(num)
        if fmt:
            cleaned.append(fmt)
    
    cleaned = list(set(cleaned)) # remove duplicates
    if not cleaned:
        return False, "No valid phone numbers"

    # ===== TEST MODE - FREE =====
    if school.sms_test_mode:
        for phone in cleaned:
            SmsLog.objects.create(
                school=school,
                phone=phone,
                message=message,
                status="TEST MODE - Logged only, not sent",
                provider=school.sms_provider or 'hubtel'
            )
        print(f"[SMS TEST] Would send to {cleaned}: {message}")
        return True, f"TEST MODE: Logged {len(cleaned)} SMS (free, not sent)"

    # ===== LIVE MODE - REAL SMS =====
    try:
        if school.sms_provider == 'hubtel':
            if not school.hubtel_client_id or not school.hubtel_client_secret:
                return False, "Hubtel ID/Secret not set in School settings"

            url = "https://sms.hubtel.com/v1/messages/send"
            payload = {
                "From": school.sms_sender_id or "SCHOOL",
                "To": ",".join(cleaned),
                "Content": message
            }
            r = requests.post(
                url,
                auth=(school.hubtel_client_id, school.hubtel_client_secret),
                json=payload,
                timeout=20
            )
            success = r.status_code in [200, 201]
            for phone in cleaned:
                SmsLog.objects.create(
                    school=school,
                    phone=phone,
                    message=message,
                    status="SENT" if success else f"FAILED: {r.text[:100]}",
                    provider='hubtel'
                )
            return success, r.text

        elif school.sms_provider == 'mnotify':
            # Add MNotify logic later
            return False, "MNotify not configured yet"

    except Exception as e:
        for phone in cleaned:
            SmsLog.objects.create(
                school=school,
                phone=phone,
                message=message,
                status=f"ERROR: {str(e)}",
                provider=school.sms_provider
            )
        return False, str(e)