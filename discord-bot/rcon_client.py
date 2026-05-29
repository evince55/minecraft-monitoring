from mcrcon import MCRcon


class RCONClient:
    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password

    def command(self, cmd: str) -> str:
        with MCRcon(self.host, self.password, self.port) as rcon:
            return rcon.command(cmd)
