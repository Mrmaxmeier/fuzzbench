"""Minimal string helpers used by vendored stacktraces."""


def strip_from_left(string, prefix):
    if not string.startswith(prefix):
        return string
    return string[len(prefix):]


def strip_from_right(string, suffix):
    if not string.endswith(suffix):
        return string
    return string[:len(string) - len(suffix)]


def sub_string_exists_in(substring_list, string):
    for substring in substring_list:
        if substring in string:
            return True
    return False
