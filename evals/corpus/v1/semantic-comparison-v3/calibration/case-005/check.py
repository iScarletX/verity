import requests

print(requests.get("https://case-five.invalid/status").status_code)
