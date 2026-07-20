import subprocess

def a():
    # SHOULD hit: real shell=True call
    subprocess.run("echo hi", shell=True)

def b():
    # SHOULD NOT hit: shell=False (default)
    subprocess.run(["echo", "hi"])

def c():
    # SHOULD NOT hit: literal string "shell=True" in a docstring/comment
    x = "shell=True"  # <- just a string
    return x
