import hashlib

checksum = hashlib.sha1(b"uploaded-file-bytes").hexdigest()
