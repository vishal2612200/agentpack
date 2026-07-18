pub struct TokenStore;
impl TokenStore { pub fn lookup(value: &str) -> &str { value } }
pub fn validate(value: &str) -> &str { TokenStore::lookup(value) }
