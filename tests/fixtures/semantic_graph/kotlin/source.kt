package auth
interface TokenStore { fun lookup(value: String): String }
class AuthService : TokenStore { override fun lookup(value: String) = value }
