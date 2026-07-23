import subprocess

subprocess.run(["/usr/bin/printf", "%s", "twelve"], check=True)
