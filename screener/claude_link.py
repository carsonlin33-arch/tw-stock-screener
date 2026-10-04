"""「Claude 研判＋虛擬帳戶」連結的密碼保護。

網站是公開的，連結網址用密碼加密（PBKDF2 → AES-GCM）後才放進 claude_link.js，
點連結時輸入密碼、在瀏覽器解密才打開。密碼本身不存在任何地方。

換密碼或網址：python -m screener.claude_link "<網址>"   （會詢問密碼）
"""
from __future__ import annotations

import base64
import getpass
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

JS = Path(__file__).with_name("claude_link.js")
ITER = 200_000


def encrypt(url: str, password: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(hashes.SHA256(), 32, salt, ITER).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, url.encode(), None)
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return {"salt": b(salt), "iv": b(iv), "ct": b(ct), "iter": ITER}


def write(url: str, password: str) -> None:
    blob = json.dumps(encrypt(url, password))
    src = JS.read_text("utf-8").split("\n", 1)[1]
    JS.write_text(f"const CLAUDE_LINK={blob};\n{src}", "utf-8")


if __name__ == "__main__":
    pw = getpass.getpass("密碼：")
    if pw != getpass.getpass("再輸入一次："):
        sys.exit("兩次密碼不一樣")
    write(sys.argv[1], pw)
    print("已更新", JS)
