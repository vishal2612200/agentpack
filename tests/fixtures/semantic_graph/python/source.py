class TokenStore:
    def lookup(self, value):
        return value

def validate(value):
    return TokenStore().lookup(value)
