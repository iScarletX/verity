from jinja2 import Template

display_name = "Alice"
template = Template(f"Welcome, {display_name}!")
output = template.render()
