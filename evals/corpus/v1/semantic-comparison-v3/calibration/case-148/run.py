from jinja2 import Environment

prefix = "Status: "
env = Environment()
template = env.from_string(prefix + "{{ state }}")
output = template.render(state="operational")
