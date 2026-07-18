; PHP semantic-core query captures.
(class_declaration name: (name) @class.name) @class
(interface_declaration name: (name) @class.name) @class
(trait_declaration name: (name) @class.name) @class
(function_definition name: (name) @function.name) @function
(method_declaration name: (name) @method.name) @method
(function_call_expression function: (_) @call.function) @call
(namespace_use_declaration (namespace_use_clause (qualified_name) @import.path)) @import
(require_expression (encapsed_string) @import.path) @import
(require_expression (string) @import.path) @import
(require_once_expression (encapsed_string) @import.path) @import
(require_once_expression (string) @import.path) @import
(include_expression (encapsed_string) @import.path) @import
(include_expression (string) @import.path) @import
(include_once_expression (encapsed_string) @import.path) @import
(include_once_expression (string) @import.path) @import
(comment) @comment
