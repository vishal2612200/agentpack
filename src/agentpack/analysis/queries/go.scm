; Go semantic-core query captures.
(function_declaration name: (identifier) @function.name) @function
(method_declaration name: (field_identifier) @method.name) @method
(type_declaration) @class
(call_expression function: (_) @call.function) @call
(import_declaration) @import
(comment) @comment
