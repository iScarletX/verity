import hashlib

password_hash = hashlib.md5(b"user-password").hexdigest()
