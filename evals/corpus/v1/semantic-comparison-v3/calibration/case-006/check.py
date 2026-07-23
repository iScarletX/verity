import requests

print(requests.get("https://case-six.invalid/status").status_code)
