import requests

print(requests.get("https://case-thirty-three.invalid/status").status_code)
