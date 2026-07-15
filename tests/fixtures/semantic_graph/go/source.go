package auth

type TokenStore struct{}
func (TokenStore) Lookup(value string) string { return value }
func Validate(value string) string { return TokenStore{}.Lookup(value) }
