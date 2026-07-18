<?php
namespace Auth;
interface TokenStore { public function lookup(string $value): string; }
class AuthService implements TokenStore { public function lookup(string $value): string { return $value; } }
