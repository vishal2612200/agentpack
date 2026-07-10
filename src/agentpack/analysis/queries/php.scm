; PHP tree-sitter query used by tree_sitter_backend.
; Capture groups:
;   @class     class + interface + trait + enum declarations
;   @function  top-level function declarations
;   @method    method declarations inside classes/traits
;   @import    use / require / include / require_once / include_once statements

(class_declaration name: (name) @class.name) @class
(interface_declaration name: (name) @class.name) @class
(trait_declaration name: (name) @class.name) @class
(enum_declaration name: (name) @class.name) @class

(function_definition name: (name) @function.name) @function
(method_declaration name: (name) @method.name) @method

; `use App\Foo\Bar;` — grab the qualified path
(namespace_use_declaration
  (namespace_use_clause (qualified_name) @import.path)) @import

; require / include family — grab the string literal. PHP's grammar emits
; `string` for single-quoted literals but `encapsed_string` for
; double-quoted ones (even with no interpolation), so both must be
; captured or double-quoted paths (the common style) are silently dropped.
(expression_statement
  (require_expression [(string (string_content) @import.path)
                        (encapsed_string (string_content) @import.path)])) @import
(expression_statement
  (require_once_expression [(string (string_content) @import.path)
                             (encapsed_string (string_content) @import.path)])) @import
(expression_statement
  (include_expression [(string (string_content) @import.path)
                        (encapsed_string (string_content) @import.path)])) @import
(expression_statement
  (include_once_expression [(string (string_content) @import.path)
                             (encapsed_string (string_content) @import.path)])) @import
