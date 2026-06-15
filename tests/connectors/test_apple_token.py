import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from needledrop.connectors.apple_token import make_developer_token


def _p8_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_make_developer_token_has_expected_header_and_claims():
    pem = _p8_pem()
    token = make_developer_token(
        pem, team_id="TEAM000000", key_id="KEY0000000", now=1_700_000_000, ttl=3600
    )

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "KEY0000000"

    public_key = serialization.load_pem_private_key(pem.encode(), password=None).public_key()
    claims = jwt.decode(token, public_key, algorithms=["ES256"], options={"verify_exp": False})
    assert claims["iss"] == "TEAM000000"
    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] == 1_700_000_000 + 3600


def test_make_developer_token_is_a_str():
    token = make_developer_token(_p8_pem(), team_id="T", key_id="K")
    assert isinstance(token, str)
