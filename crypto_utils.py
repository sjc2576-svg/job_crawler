"""
사용자별 Anthropic API 키처럼 DB에 저장은 하되 나중에 그대로 다시 꺼내 써야 하는
값(비밀번호처럼 단방향 해시로 저장할 수 없는 값)을 암호화/복호화한다.
SECRET_KEY(config.py)에서 유도한 키로 대칭 암호화(Fernet)한다.
SECRET_KEY가 유출되면 이 값들도 함께 복호화될 수 있으므로, config.py를
저장소에 커밋하지 않는 것(.gitignore)이 이 암호화의 전제 조건이다.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import SECRET_KEY

_fernet_key = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode("utf-8")).digest())
_fernet = Fernet(_fernet_key)


def encrypt_secret(plain_text):
    """평문 문자열을 암호화해서 저장용 문자열로 반환. None/빈 문자열이면 None 반환."""
    if not plain_text:
        return None
    return _fernet.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted_text):
    """저장된 암호문을 평문으로 복원. None이거나 복호화 실패 시 None 반환."""
    if not encrypted_text:
        return None
    try:
        return _fernet.decrypt(encrypted_text.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
