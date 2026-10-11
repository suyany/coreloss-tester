def parse_error(reply):
    code, message = reply.split(",", 1)
    code = int(code)
    message = message.strip().replace('"', "")
    return (code, message)