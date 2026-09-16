import base64
import requests


def initiate_hubtel_payment(school, amount, phone, client_ref):
    # TEST MODE - like Paystack test
    # If client_id is empty or starts with test_, fake success
    if not school.hubtel_client_id or school.hubtel_client_id.startswith("test"):
        print(f"[HUBTEL TEST MODE] {client_ref} - GHS {amount} - {phone}")
        return {
            "status": "success",
            "transactionId": f"HUBTEL_TEST_{client_ref}",
            "is_test": True,
        }

    # REAL LIVE CALL (later when you have live keys)
    auth_str = f"{school.hubtel_client_id}:{school.hubtel_client_secret}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    headers = {"Authorization": f"Basic {b64_auth}", "Content-Type": "application/json"}
    url = f"https://api.hubtel.com/v1/merchantaccount/merchants/{school.hubtel_merchant_account}/receive/mobilemoney"
    payload = {
        "CustomerName": "School Fee",
        "CustomerMsisdn": phone,
        "Channel": "mtn-gh",
        "Amount": float(amount),
        "ClientReference": client_ref,
        "Description": client_ref,
        "PrimaryCallbackUrl": "https://yourdomain.com/webhook/hubtel/",
    }
    r = requests.post(url, json=payload, headers=headers, timeout=30)
    return r.json()
