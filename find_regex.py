import git

try:
    print(git.Actor.name_email_regex)
except AttributeError:
    print("Not found on Actor directly.")

try:
    print(git.Actor.name_email_regex)
except AttributeError:
    print("Not found on git module directly.")

print(dir(git.Actor))
