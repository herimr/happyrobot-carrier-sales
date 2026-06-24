import time

import pytest

from app.services import otp_store


@pytest.fixture(autouse=True)
def _clear_store():
    otp_store._STORE.clear()
    yield
    otp_store._STORE.clear()


# --- Standard ---------------------------------------------------------------

def test_correct_code_validates_on_first_try():
    code, _ = otp_store.generate_otp("call-1")
    valid, remaining, locked = otp_store.validate_otp("call-1", code)
    assert valid is True
    assert locked is False


def test_validated_code_cannot_be_reused_replay():
    code, _ = otp_store.generate_otp("call-2")
    first = otp_store.validate_otp("call-2", code)
    assert first[0] is True
    second = otp_store.validate_otp("call-2", code)
    assert second[0] is False, "a consumed code must not validate again"


# --- Edge cases --------------------------------------------------------------

def test_expired_code_is_rejected():
    otp_store.OTP_TTL_SECONDS  # noqa: B018 (just touching for clarity)
    code, _ = otp_store.generate_otp("call-3")
    otp_store._STORE["call-3"].expires_at = time.time() - 1  # force expiry
    valid, _, locked = otp_store.validate_otp("call-3", code)
    assert valid is False
    assert locked is True


def test_one_resend_allowed_second_resend_denied():
    otp_store.generate_otp("call-4")
    allowed_1, code_1, _ = otp_store.resend_otp("call-4")
    assert allowed_1 is True
    assert code_1 is not None

    allowed_2, code_2, _ = otp_store.resend_otp("call-4")
    assert allowed_2 is False
    assert code_2 is None


def test_unknown_call_id_never_validates():
    valid, remaining, locked = otp_store.validate_otp("never-generated", "123456")
    assert valid is False
    assert locked is True


# --- Adversarial: brute force / social engineering pressure ----------------

def test_three_wrong_attempts_locks_the_call_regardless_of_correct_code_after():
    code, _ = otp_store.generate_otp("call-5")
    wrong = "000000" if code != "000000" else "111111"

    otp_store.validate_otp("call-5", wrong)
    otp_store.validate_otp("call-5", wrong)
    valid, remaining, locked = otp_store.validate_otp("call-5", wrong)
    assert valid is False
    assert locked is True
    assert remaining == 0

    # Even the genuinely correct code must now fail closed -- this is the
    # core anti-social-engineering property: persistence cannot win.
    valid_after_lock, _, locked_after = otp_store.validate_otp("call-5", code)
    assert valid_after_lock is False
    assert locked_after is True


def test_sequential_brute_force_of_all_six_digit_codes_is_bounded_by_attempt_limit():
    code, _ = otp_store.generate_otp("call-6")
    guesses_tried = 0
    locked = False
    for guess in range(0, 1000):  # would take far longer than 3 tries to hit by chance
        guess_str = f"{guess:06d}"
        if guess_str == code:
            continue
        valid, remaining, locked = otp_store.validate_otp("call-6", guess_str)
        guesses_tried += 1
        if locked:
            break
    assert locked is True
    assert guesses_tried == otp_store.MAX_ATTEMPTS, "brute force must be cut off at exactly MAX_ATTEMPTS wrong guesses"


def test_resend_spam_eventually_locks_instead_of_granting_infinite_codes():
    otp_store.generate_otp("call-7")
    otp_store.resend_otp("call-7")  # uses the one allowed resend
    for _ in range(otp_store.MAX_ATTEMPTS):
        otp_store.resend_otp("call-7")  # each further resend burns an attempt
    record = otp_store._STORE.get("call-7")
    assert record is None or record.locked is True
