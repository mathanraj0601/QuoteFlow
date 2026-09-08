from gmail_trigger_poc.pipeline import process_email


SAMPLE = {
    "id": "18e0f",
    "thread_id": "18e0f",
    "from": "client@example.com",
    "subject": "Need a quote for 50 widgets",
    "body": "Hi, please quote 50 units of part X.",
}


def test_process_email_shape():
    out = process_email(SAMPLE)
    assert out["status"] == "received"
    assert out["message_id"] == "18e0f"
    assert out["subject"] == "Need a quote for 50 widgets"
    assert out["body_chars"] == len(SAMPLE["body"])
    assert out["quotation_id"] is None


def test_process_email_is_idempotent():
    assert process_email(SAMPLE) == process_email(SAMPLE)


def test_process_email_tolerates_missing_fields():
    out = process_email({})
    assert out["status"] == "received"
    assert out["message_id"] is None
    assert out["body_chars"] == 0
