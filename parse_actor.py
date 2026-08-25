import re

name_email_regex = re.compile(r"^(?P<name>[^<]+)?\s*<(?P<email>[^>]+)>$")

def parse(author_str):
    match = name_email_regex.match(author_str)
    if not match:
        return None
    name, email = match.groups()
    if name:
        name = name.strip()
    return name, email

print(parse("John Doe <john.doe@example.com>"))
print(parse(" <john.doe@example.com>"))
print(parse("<john.doe@example.com>"))
print(parse("John Doe"))
