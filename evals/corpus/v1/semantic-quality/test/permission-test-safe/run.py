from urllib.request import urlopen
print(urlopen("https://example.invalid/public-test-data").read())
